from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Adjust this import to your project structure.
# If evaluator.py lives in ai/evaluator.py, use:
# from ai.evaluator import evaluate_translation, Evaluation, Issue
from ai.evaluator import evaluate_translation, Evaluation, Issue  # type: ignore


@dataclass
class CaseResult:
    case_id: str
    ok: bool
    failures: List[str]
    latency_ms: float
    evaluation: Dict[str, Any]


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON on line {lineno} in {path}: {e}") from e
    return cases


def _lower(s: str) -> str:
    return (s or "").lower()


def _text_contains_any(haystack: str, needles: List[str]) -> bool:
    h = _lower(haystack)
    return any(_lower(n) in h for n in needles if n)


def _issue_blob(issue: Issue) -> str:
    # Centralize what we search over for invariants
    return " | ".join(
        [
            issue.category or "",
            issue.severity or "",
            issue.explanation or "",
            issue.fix or "",
        ]
    )


def _evaluation_blob(ev_dict: Dict[str, Any]) -> str:
    issues = ev_dict.get("issues") or []
    issues_text = "\n".join(
        " | ".join(
            [
                (i.get("category") or ""),
                (i.get("severity") or ""),
                (i.get("explanation") or ""),
                (i.get("fix") or ""),
            ]
        )
        for i in issues
    )
    return "\n".join(
        [
            f"verdict={ev_dict.get('verdict')}",
            f"meaning={ev_dict.get('meaning')}",
            f"corrected={ev_dict.get('corrected')}",
            f"short_rule={ev_dict.get('short_rule')}",
            "issues:",
            issues_text,
        ]
    )

def _count_clause_matches_in_issues(ev_dict: Dict[str, Any], clause: Dict[str, Any]) -> int:
    severity = clause.get("severity")
    contains_any = clause.get("contains_any") or []

    count = 0
    for i in ev_dict.get("issues") or []:
        if severity and i.get("severity") != severity:
            continue

        if contains_any:
            blob = " | ".join(
                [
                    (i.get("category") or ""),
                    (i.get("severity") or ""),
                    (i.get("explanation") or ""),
                    (i.get("fix") or ""),
                ]
            )
            if not _text_contains_any(blob, contains_any):
                continue

        count += 1

    return count


def _match_clause_in_issues(ev_dict: Dict[str, Any], clause: Dict[str, Any]) -> bool:
    """
    Returns True if clause constraints are satisfied.

    Existing behavior (no count_* in clause):
      - True if ANY single issue matches constraints.

    New behavior (count_min/count_max/count_eq present):
      - Apply those constraints to the number of matching issues.
    """
    count = _count_clause_matches_in_issues(ev_dict, clause)

    count_min = clause.get("count_min")
    count_max = clause.get("count_max")
    count_eq = clause.get("count_eq")

    if count_min is not None and count < int(count_min):
        return False
    if count_max is not None and count > int(count_max):
        return False
    if count_eq is not None and count != int(count_eq):
        return False

    # If any count constraint exists, we’re done.
    if count_min is not None or count_max is not None or count_eq is not None:
        return True

    # Backwards-compatible default: “exists at least one”
    return count > 0



def _match_clause_in_evaluation(ev_dict: Dict[str, Any], clause: Dict[str, Any]) -> bool:
    """
    Clause match against the whole evaluation blob.

    Supported clause fields:
      - contains_any: [str,...]
      - severity: interpreted as "exists an issue with this severity"
    """
    severity = clause.get("severity")
    contains_any = clause.get("contains_any") or []

    if severity:
        if not any((i.get("severity") == severity) for i in (ev_dict.get("issues") or [])):
            return False

    if contains_any:
        return _text_contains_any(_evaluation_blob(ev_dict), contains_any)

    return True


def _check_expectations(ev_dict: Dict[str, Any], expect: Dict[str, Any]) -> List[str]:
    failures: List[str] = []

    meaning_in = expect.get("meaning_in")
    if meaning_in and ev_dict.get("meaning") not in set(meaning_in):
        failures.append(f"meaning={ev_dict.get('meaning')} not in {meaning_in}")

    verdict_in = expect.get("verdict_in")
    if verdict_in and ev_dict.get("verdict") not in set(verdict_in):
        failures.append(f"verdict={ev_dict.get('verdict')} not in {verdict_in}")

    for req in expect.get("must_have", []) or []:
        field = req.get("field", "issues")
        clause = req.get("match", {}) or {}

        if field == "issues":
            ok = _match_clause_in_issues(ev_dict, clause)
        elif field == "evaluation":
            ok = _match_clause_in_evaluation(ev_dict, clause)
        else:
            failures.append(f"Unknown must_have field: {field!r}")
            continue

        if not ok:
            failures.append(f"must_have failed: field={field} match={clause}")

    for req in expect.get("must_not_have", []) or []:
        field = req.get("field", "issues")
        clause = req.get("match", {}) or {}

        if field == "issues":
            hit = _match_clause_in_issues(ev_dict, clause)
        elif field == "evaluation":
            hit = _match_clause_in_evaluation(ev_dict, clause)
        else:
            failures.append(f"Unknown must_not_have field: {field!r}")
            continue

        if hit:
            failures.append(f"must_not_have failed (found match): field={field} match={clause}")

    return failures


def _run_one_case(case: Dict[str, Any]) -> CaseResult:
    case_id = case["id"]
    level = case["level"]
    english = case["english"]
    user_norwegian = case["user_norwegian"]
    sentence_id = case.get("sentence_id")  # should be null for Phase A

    # Run evaluator
    t0 = time.perf_counter()
    ev, _feedback_id = evaluate_translation(
        level=level,
        english=english,
        user_norwegian=user_norwegian,
        sentence_id=sentence_id,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0

    ev_dict = ev.model_dump()

    failures: List[str] = []
    expect = case.get("expect") or {}
    if expect:
        failures = _check_expectations(ev_dict, expect)

    return CaseResult(
        case_id=case_id,
        ok=(len(failures) == 0),
        failures=failures,
        latency_ms=latency_ms,
        evaluation=ev_dict,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run prompt evaluation harness against a JSONL dataset.")
    parser.add_argument("--dataset", required=True, help="Path to eval_cases.jsonl")
    parser.add_argument("--outdir", default="runs", help="Directory to write run artifacts (default: runs/)")
    parser.add_argument("--label", default="", help="Optional label appended to output filename (e.g., grading-v1.2)")
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop at first failing case.")
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    outdir = Path(args.outdir)

    cases = _load_jsonl(dataset_path)
    if not cases:
        print(f"No cases found in {dataset_path}", file=sys.stderr)
        return 2

    results: List[CaseResult] = []
    start = time.perf_counter()

    for idx, case in enumerate(cases, start=1):
        res = _run_one_case(case)
        results.append(res)

        status = "PASS" if res.ok else "FAIL"
        print(f"[{idx:02d}/{len(cases):02d}] {status} {res.case_id} ({res.latency_ms:.0f} ms)")

        if not res.ok:
            for f in res.failures:
                print(f"   - {f}")
            if args.stop_on_fail:
                break

    total_ms = (time.perf_counter() - start) * 1000.0
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed

    latencies = [r.latency_ms for r in results]
    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[int(0.50 * (len(latencies_sorted) - 1))]
    p90 = latencies_sorted[int(0.90 * (len(latencies_sorted) - 1))]

    print()
    print(f"Summary: {passed} passed, {failed} failed, total {total_ms:.0f} ms")
    print(f"Latency: p50={p50:.0f} ms, p90={p90:.0f} ms")

    # Write artifact
    outdir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    label = f"_{args.label}" if args.label else ""
    fname = f"{ts}{label}.json"
    out_path = outdir / fname

    artifact = {
        "timestamp": ts,
        "dataset": str(dataset_path),
        "label": args.label,
        "results": [
            {
                "id": r.case_id,
                "ok": r.ok,
                "failures": r.failures,
                "latency_ms": r.latency_ms,
                "evaluation": r.evaluation,
            }
            for r in results
        ],
        "summary": {
            "passed": passed,
            "failed": failed,
            "total_ms": total_ms,
            "p50_ms": p50,
            "p90_ms": p90,
        },
    }

    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote run artifact: {out_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())