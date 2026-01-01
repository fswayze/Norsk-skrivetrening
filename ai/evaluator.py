# ai/evaluator.py (near the top)

from __future__ import annotations

import os
import re
from typing import Literal, List, Optional, Dict, Any, Tuple

import requests
from openai import OpenAI
from pydantic import BaseModel, Field
import sqlite3

client = OpenAI()

Severity = Literal["error", "variant", "style"]
DB_PATH = os.getenv("APP_DB_PATH", "data/app.db")


class Issue(BaseModel):
    category: str = Field(
        ...,
        description="F.eks. V2, preposisjon, bøying, kjønn, ordvalg, tegnsetting, register, rettskriving",
    )
    severity: Severity = Field(
        ...,
        description="error=må rettes, variant=akseptabel alternativ form, style=valgfri forbedring",
    )
    explanation: str = Field(..., description="Kort forklaring på norsk (1–2 setninger).")
    fix: str = Field(..., description="Minimal retting eller anbefalt formulering på norsk.")


class Evaluation(BaseModel):
    verdict: Literal["correct", "minor", "incorrect"]
    meaning: Literal["same", "minor_drift", "different"]
    corrected: str = Field(..., description="Én naturlig, eksamensnær bokmålsversjon med samme mening.")
    issues: List[Issue] = Field(default_factory=list, description="Maks 3 punkter.")
    short_rule: str = Field(..., description="Én setning med viktigste regel eller råd.")

def _get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def get_valid_translations(sentence_id: int) -> List[str]:
    conn = _get_db_connection()
    rows = conn.execute(
        "SELECT translation FROM valid_translations WHERE sentence_id = ?",
        (sentence_id,),
    ).fetchall()
    conn.close()
    return [r["translation"] for r in rows]


def _normalize_nb(s: str) -> str:
    """
    Conservative normalization for Bokmål matching:
    - trim
    - collapse whitespace
    - normalize curly quotes
    - remove trailing sentence punctuation (., !, ?)
    - lower-case
    """
    s = (s or "").strip()
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s+", " ", s)

    # Remove trailing punctuation (common in user input variance)
    s = re.sub(r"[.!?]+$", "", s.strip())

    return s.lower()


def check_against_gold(sentence_id: int, user_norwegian: str) -> Optional[str]:
    """
    Returns the matched gold translation (original stored string) if it matches,
    else None.
    """
    gold = get_valid_translations(sentence_id)
    if not gold:
        return None

    user_norm = _normalize_nb(user_norwegian)
    for t in gold:
        if _normalize_nb(t) == user_norm:
            return t  # return the canonical stored form
    return None



# -------------------------
# LanguageTool integration
# -------------------------

LT_ENDPOINT = os.getenv("LANGUAGETOOL_ENDPOINT", "https://api.languagetool.org/v2/check")
LT_LANGUAGE = os.getenv("LANGUAGETOOL_LANGUAGE", "nb")  # Bokmål


def _languagetool_check(text: str, timeout_s: float = 4.0) -> Dict[str, Any]:
    """
    Call LanguageTool /v2/check. Returns JSON response.
    Set LANGUAGETOOL_ENDPOINT to self-hosted endpoint if needed.
    """
    payload = {
        "language": LT_LANGUAGE,
        "text": text,
    }
    r = requests.post(LT_ENDPOINT, data=payload, timeout=timeout_s)
    r.raise_for_status()
    return r.json()


def _lt_is_objective_error(match: Dict[str, Any]) -> bool:
    """
    LanguageTool returns a mix of grammar/spelling/style. We treat these as objective
    errors for verdict arbitration: spelling/misspelling + grammar + some punctuation.
    """
    rule = match.get("rule", {}) or {}
    issue_type = (rule.get("issueType") or "").lower()

    # Heuristic: treat these as objective enough for 'error'
    if issue_type in {"misspelling", "typographical", "grammar"}:
        return True

    # Many punctuation issues are also fairly objective; keep conservative
    if issue_type in {"punctuation"}:
        return True

    # Everything else (style, inconsistency, etc.) is not an "error" arbiter
    return False


def _lt_category(match: Dict[str, Any]) -> str:
    rule = match.get("rule", {}) or {}
    issue_type = (rule.get("issueType") or "").lower()
    if issue_type in {"misspelling", "typographical"}:
        return "rettskriving"
    if issue_type == "punctuation":
        return "tegnsetting"
    if issue_type == "grammar":
        return "grammatikk"
    return "stil"


def _lt_suggest_fix(match: Dict[str, Any], original_text: str) -> str:
    # Prefer first replacement if present; otherwise fall back to message
    repl = match.get("replacements") or []
    if repl and isinstance(repl, list) and repl[0].get("value"):
        return repl[0]["value"]

    # As a fallback, show the problematic span
    offset = int(match.get("offset", 0))
    length = int(match.get("length", 0))
    span = original_text[offset : offset + length] if length > 0 else ""
    return span.strip() or "—"


def _lt_to_issues(lt_json: Dict[str, Any], original_text: str) -> Tuple[List[Issue], List[Dict[str, Any]]]:
    """
    Returns:
      - issues: up to 3 Issue objects (objective errors prioritized)
      - objective_matches: all objective matches for verdict arbitration
    """
    matches = lt_json.get("matches", []) or []

    objective = [m for m in matches if _lt_is_objective_error(m)]
    non_objective = [m for m in matches if not _lt_is_objective_error(m)]

    # Build Issues: objective first, then (optionally) non-objective if room
    issues: List[Issue] = []

    def add_from_match(m: Dict[str, Any], severity: Severity) -> None:
        rule = m.get("rule", {}) or {}
        message = (m.get("message") or "").strip()
        cat = _lt_category(m)
        fix = _lt_suggest_fix(m, original_text)

        # Keep explanation short and in Norwegian; LT message may be in English.
        # We leave explanation minimal here; the LLM can rewrite explanations later.
        explanation = message if message else "Språkverktøyet fant et mulig problem."

        issues.append(
            Issue(
                category=cat,
                severity=severity,
                explanation=explanation,
                fix=fix,
            )
        )

    for m in objective:
        if len(issues) >= 3:
            break
        add_from_match(m, "error")

    # If we still have space, include up to one style-ish LT suggestion as 'style'
    for m in non_objective:
        if len(issues) >= 3:
            break
        add_from_match(m, "style")

    return issues, objective


def _lt_verdict_floor(objective_matches: List[Dict[str, Any]]) -> Optional[Literal["minor", "incorrect"]]:
    """
    If LanguageTool finds objective errors, it sets a minimum severity floor.
    You can tune thresholds.
    """
    n = len(objective_matches)
    if n <= 0:
        return None
    # Tune: 1 objective error => minor, 2+ => incorrect
    if n == 1:
        return "minor"
    return "incorrect"

# -------------------------
# Prompt builders
# -------------------------

def _grading_system_prompt() -> str:
    return (
        "Du er en konsekvent og rubrikkstyrt sensor for norskprøven skriftlig (bokmål). "
        "Du vurderer én enkelt setning oversatt fra engelsk til norsk. "
        "Fokus: grammatikk, ordstilling (V2), bøying, preposisjoner, idiomatisk språk, register og rettskriving. "
        "VIKTIG: Ikke motsi deg selv. Hvis flere løsninger er akseptable, "
        "skal dette merkes som 'variant' og ikke kalles feil. "
        "VIKTIG PRESISJON: Marker bare severity='error' når brukerens form er ugrammatisk, bryter en klar regel "
        "eller er en tydelig rettskrivingsfeil. "
        "Hvis det finnes en akseptabel alternativ løsning (selv om den er mindre vanlig), bruk severity='variant' "
        "og forklar nyansen. "
        "Ikke forveksle 'mindre vanlig' med 'feil'. "
        "Hvis bare ett ord/uttrykk er problemet, lag kun ett issue (ikke del opp i V2 + bøying + ordvalg). "
        "Abstrakte substantiv (f.eks. «samfunn», «demokrati», «familie») kan stå i både ubestemt og bestemt form uten at det er feil. "
        "Marker dette som variant eller utelat issue. "
        "Ikke kall én korrekt konstruksjon feil bare fordi en annen formulering er vanligere (f.eks. «føle selvtillit» vs. «føle seg selvsikker»). "
        "PRIMÆR KILDE: Du får også funn fra LanguageTool (rettskriving/grammatikk). Ikke overstyr disse. "
        "Hvis du er uenig, kan du nedgradere til variant/style, men ikke hevde at et tydelig LT-rettskrivingsfunn er 'riktig'."
    )


def _grading_user_prompt(
    level: str,
    english: str,
    user_norwegian: str,
    lt_summary: str,
) -> str:
    return f"""
NIVÅ: {level}

ENGELSK SETNING:
{english}

BRUKERENS NORSK:
{user_norwegian}

LANGUAGETOOL-FUNN (primært for rettskriving/grammatikk):
{lt_summary}

VURDERING:
- verdict:
  - correct: eksamensgod, naturlig bokmål uten feil som må rettes
  - minor: betydningen er riktig, men 1–2 små feil / klare rettskrivings- eller grammatikkfeil, eller litt uidiomatisk
  - incorrect: flere feil eller feil som endrer mening / forstyrrer forståelsen

ISSUES:
- Maks 3.
- Hvert issue må ha: category, severity (error / variant / style), explanation, fix.
- severity='variant' betyr: brukerens løsning kan være akseptabel.
- severity='style' betyr: valgfri forbedring, ikke grammatikkfeil.
- Ikke del opp én feil i flere issues. Én språklig feil → maks ett issue.
- Prioritér objektive feil (rettskriving/grammatikk) over stil.

OUTPUT:
- corrected: én naturlig, eksamensnær bokmålsversjon (samme mening).
- short_rule: én setning med viktigste regel eller råd.
"""


def _audit_system_prompt() -> str:
    return (
        "Du er en kvalitetssikrer (audit) for en norskprøven-sensor. "
        "Du får: engelsk original, brukerens norske setning, LanguageTool-funn, og et foreslått evalueringsobjekt. "
        "Oppgave: Returner et REVIDERT evalueringsobjekt i samme schema. "
        "Regler:\n"
        "1) Ikke legg til unødvendige issues. Maks 3.\n"
        "2) Fjern eller nedgrader issues som ikke er reelle feil. Hvis noe er en akseptabel variant, bruk severity='variant' eller fjern.\n"
        "3) Ta med manglende KLARE feil, spesielt rettskriving/ortografi og åpenbare grammatikkfeil.\n"
        "4) Issues må samsvare med corrected. Hvis corrected endrer X, må issue forklare X (med minimal fix).\n"
        "5) Ikke hallusiner grammatikkregler: Hvis du er usikker, bruk variant/style eller utelat.\n"
        "6) LanguageTool-funn for rettskriving/grammatikk veier tungt. Ikke ignorer dem uten god grunn.\n"
    )


def _audit_user_prompt(
    level: str,
    english: str,
    user_norwegian: str,
    lt_summary: str,
    draft: Evaluation,
) -> str:
    return f"""
NIVÅ: {level}

ENGELSK SETNING:
{english}

BRUKERENS NORSK:
{user_norwegian}

LANGUAGETOOL-FUNN:
{lt_summary}

UTKAST (fra sensor):
- verdict: {draft.verdict}
- corrected: {draft.corrected}
- short_rule: {draft.short_rule}
- issues:
{[i.model_dump() for i in draft.issues]}

REVIDER:
- Returner et nytt Evaluation-objekt (samme JSON/Pydantic-format).
- Maks 3 issues.
- Prioritér objektive feil fra LanguageTool.
"""


def _format_lt_summary(lt_json: Optional[Dict[str, Any]], original: str, max_items: int = 6) -> str:
    """
    Compact summary passed to LLM; we don't need every detail.
    """
    if not lt_json:
        return "Ingen (LanguageTool ikke tilgjengelig)."

    matches = lt_json.get("matches", []) or []
    if not matches:
        return "Ingen funn."

    lines: List[str] = []
    for m in matches[:max_items]:
        offset = int(m.get("offset", 0))
        length = int(m.get("length", 0))
        span = original[offset : offset + length] if length > 0 else ""
        cat = _lt_category(m)
        obj = "OBJ" if _lt_is_objective_error(m) else "STIL"
        fix = _lt_suggest_fix(m, original)
        msg = (m.get("message") or "").strip()
        # Keep concise
        lines.append(f"- [{obj}/{cat}] «{span}» → forslag: «{fix}» ({msg})")
    return "\n".join(lines)


# -------------------------
# Core evaluation
# -------------------------

def evaluate_translation(
    level: str,
    english: str,
    user_norwegian: str,
    sentence_id: Optional[int] = None
) -> Evaluation:

    # 0) Stored Translations First
    if sentence_id is not None:
        matched = check_against_gold(sentence_id, user_norwegian)
        if matched is not None:
            return Evaluation(
                verdict="correct",
                meaning="same",
                corrected=matched,
                issues=[],
                short_rule="Godkjent: Svaret matcher en lagret fasit (bokmål).",
            )

    # 1) LanguageTool (objective arbiter)
    lt_json: Optional[Dict[str, Any]] = None
    lt_issues: List[Issue] = []
    lt_objective: List[Dict[str, Any]] = []

    try:
        lt_json = _languagetool_check(user_norwegian)
        lt_issues, lt_objective = _lt_to_issues(lt_json, user_norwegian)
    except Exception:
        lt_json = None
        lt_issues = []
        lt_objective = []

    lt_summary = _format_lt_summary(lt_json, user_norwegian)

    # 2) Single LLM pass: grading
    response = client.responses.parse(
        model="gpt-5-nano-2025-08-07",
        input=[
            {"role": "system", "content": _grading_system_prompt()},
            {"role": "user", "content": _grading_user_prompt(
                level, english, user_norwegian, lt_summary
            )},
        ],
        text_format=Evaluation,
    )

    ev: Evaluation = response.output_parsed

    # 3) Merge LT issues (objective errors win)
    merged: List[Issue] = []

    if lt_issues:
        merged.extend(lt_issues)

    for iss in ev.issues:
        if len(merged) >= 3:
            break
        dup = any(
            iss.category == m.category and iss.fix == m.fix
            for m in merged
        )
        if not dup:
            merged.append(iss)

    ev.issues = merged[:3]

    # 4) Verdict arbitration via LT floor
    floor = _lt_verdict_floor(lt_objective)

    if floor is not None:
        if floor == "minor" and ev.verdict == "correct":
            ev.verdict = "minor"
        elif floor == "incorrect":
            ev.verdict = "incorrect"

    # 5) Reinjection safety: ensure LT objective errors are not lost
    if lt_issues and not any(i.severity == "error" for i in ev.issues):
        reinject = [i for i in lt_issues if i.severity == "error"]
        ev.issues = (reinject + ev.issues)[:3]

    # 6) Final correctness override
    error_count = sum(1 for i in ev.issues if i.severity == "error")

    if (
        len(lt_objective) == 0
        and ev.meaning == "same"
        and error_count == 0
    ):
        ev.verdict = "correct"

    return ev
