from __future__ import annotations

from datetime import datetime
from typing import Optional, Any, Dict

from flask import render_template, request, redirect, url_for, session

from db import get_db_connection
from ai.evaluator import evaluate_translation
import json
from collections import defaultdict
from pprint import pprint

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


def pick_sentence(level: str, avoid_id=None):
    """
    Picks a random English prompt from source_sentences for the given level.
    avoid_id: last sentence id shown (DB id), to reduce immediate repeats.
    Returns: {"id": int, "english": str}
    """
    connection = get_db_connection()
    connection.execute("PRAGMA foreign_keys = ON;")

    row = None

    if avoid_id is not None:
        row = connection.execute(
            """
            SELECT id, sentence
            FROM source_sentences
            WHERE level = ?
              AND id != ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (level, avoid_id),
        ).fetchone()

    if row is None:
        row = connection.execute(
            """
            SELECT id, sentence
            FROM source_sentences
            WHERE level = ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (level,),
        ).fetchone()

    connection.close()

    if row is None:
        return {"id": 0, "english": ""}

    return {"id": row["id"], "english": row["sentence"]}


# -----------------------------
# DB helpers for games
# -----------------------------

def create_game() -> int:
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db_connection()
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.execute(
        """
        INSERT INTO games (level, correct_streak, incorrect_streak, turns_at_level,
                           last_sentence_id, status, end_reason, started_at)
        VALUES ('A1', 0, 0, 0, NULL, 'active', NULL, ?)
        """,
        (started_at,),
    )
    conn.commit()
    game_id = cur.lastrowid
    conn.close()
    return int(game_id)

def get_game(game_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    conn.execute("PRAGMA foreign_keys = ON;")

    row = conn.execute(
        """
        SELECT id, level, correct_streak, incorrect_streak, turns_at_level, last_sentence_id,
               status, end_reason, started_at, ended_at
        FROM games
        WHERE id = ?
        """,
        (game_id,),
    ).fetchone()

    conn.close()
    if row is None:
        return None

    # Convert sqlite Row -> plain dict
    return dict(row)

def insert_translation_attempt(
    game_id: int,
    *,
    sentence_id: int,
    level: str,
    english_sentence: str,
    user_norwegian: str,
    verdict: str,
    feedback_id: int,
) -> int:
    """
    Insert a translation attempt row. Returns the attempt id.
    """
    conn = get_db_connection()
    conn.execute("PRAGMA foreign_keys = ON;")

    cur = conn.execute(
        """
        INSERT INTO translation_attempts (
            game_id,
            sentence_id,
            level,
            english_sentence,
            user_norwegian,
            verdict,
            feedback_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            sentence_id,
            level,
            english_sentence,
            user_norwegian,
            verdict,
            feedback_id,
        ),
    )
    conn.commit()
    attempt_id = int(cur.lastrowid)
    conn.close()
    return attempt_id



def update_game(game_id: int, **fields: Any) -> None:
    """
    Updates a game row with the provided fields.

    Example:
        update_game(game_id, last_sentence_id=10, correct_streak=1)
    """
    if not fields:
        return

    allowed = {
        "level",
        "correct_streak",
        "incorrect_streak",
        "turns_at_level",
        "last_sentence_id",
        "status",
        "end_reason",
        "ended_at",
    }
    for k in list(fields.keys()):
        if k not in allowed:
            raise ValueError(f"Disallowed field for games update: {k}")

    cols = ", ".join([f"{k} = ?" for k in fields.keys()])
    vals = list(fields.values())

    conn = get_db_connection()
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        f"UPDATE games SET {cols} WHERE id = ?",
        (*vals, game_id),
    )
    conn.commit()
    conn.close()


def end_game(game_id: int, reason: str) -> None:
    # ended_at stored as ISO string consistent with your code style
    ended_at = datetime.now().isoformat(timespec="seconds")
    update_game(game_id, status="ended", end_reason=reason, ended_at=ended_at)


# -----------------------------
# Routes
# -----------------------------

def register_routes(app):
    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    @app.route("/game/start", methods=["POST"])
    def game_start():
        game_id = create_game()
        session["game_id"] = game_id
        return redirect(url_for("game"))

    @app.route("/game", methods=["GET"])
    def game():
        game_id = session.get("game_id")
        if not game_id:
            return redirect(url_for("index"))

        game_state = get_game(int(game_id))
        if not game_state:
            session.pop("game_id", None)
            return redirect(url_for("index"))

        if game_state.get("status") == "ended":
            return redirect(url_for("game_result"))

        sentence = pick_sentence(
            game_state["level"],
            avoid_id=game_state.get("last_sentence_id"),
        )

        # persist last_sentence_id
        update_game(int(game_id), last_sentence_id=sentence["id"])

        # refresh game state (optional, but keeps template context accurate)
        game_state["last_sentence_id"] = sentence["id"]

        context = {
            **game_state,
            "game_id": int(game_id),
            "sentence_id": sentence["id"],
            "english_sentence": sentence["english"],
        }
        return render_template("game.html", **context)

    @app.route("/game/submit", methods=["POST"])
    def game_submit():
        game_id = session.get("game_id")
        if not game_id:
            return redirect(url_for("index"))

        game_state = get_game(int(game_id))
        if not game_state or game_state.get("status") != "active":
            return redirect(url_for("index"))

        user_norwegian = request.form.get("norwegian", "").strip()
        english_sentence = request.form.get("english_sentence", "").strip()

        sentence_id_raw = request.form.get("sentence_id")
        sentence_id: Optional[int] = None
        if sentence_id_raw and str(sentence_id_raw).isdigit():
            sentence_id = int(sentence_id_raw)

        if sentence_id is None:
            # This should not happen in your game flow; fail loudly while developing
            raise ValueError("Missing sentence_id on submit; cannot save translation_attempt")

        evaluation, feedback_id = evaluate_translation(
            game_state["level"],
            english_sentence,
            user_norwegian,
            sentence_id=sentence_id,
        )

        if feedback_id is None:
            raise RuntimeError("evaluate_translation returned no feedback_id")

        verdict = evaluation.verdict

        # Save attempt immediately (before redirect/render)
        insert_translation_attempt(
            int(game_id),
            sentence_id=sentence_id,
            level=game_state["level"],
            english_sentence=english_sentence,
            user_norwegian=user_norwegian,
            verdict=verdict,
            feedback_id=feedback_id,
        )

        # Update counters (in memory first)
        turns_at_level = int(game_state["turns_at_level"]) + 1
        correct_streak = int(game_state["correct_streak"])
        incorrect_streak = int(game_state["incorrect_streak"])
        level = game_state["level"]

        if verdict == "correct":
            correct_streak += 1
            incorrect_streak = 0
        elif verdict == "incorrect":
            incorrect_streak += 1
            correct_streak = 0
        else:  # minor
            correct_streak = 0
            incorrect_streak = 0

        ended = False
        reason = None

        if incorrect_streak >= 2:
            ended = True
            reason = "two_incorrect"
        elif turns_at_level >= 5 and correct_streak < 2:
            ended = True
            reason = "no_progress"

        # Level up logic
        if not ended and correct_streak >= 2:
            idx = LEVELS.index(level)
            if idx < len(LEVELS) - 1:
                level = LEVELS[idx + 1]

            # Reset for next level
            correct_streak = 0
            incorrect_streak = 0
            turns_at_level = 0

        # Persist state
        if ended and reason:
            end_game(int(game_id), reason)
            update_game(
                int(game_id),
                level=level,
                correct_streak=correct_streak,
                incorrect_streak=incorrect_streak,
                turns_at_level=turns_at_level,
            )
        else:
            update_game(
                int(game_id),
                level=level,
                correct_streak=correct_streak,
                incorrect_streak=incorrect_streak,
                turns_at_level=turns_at_level,
                status="active",
                end_reason=None,
                ended_at=None,
            )

        # Reload final state for rendering
        game_state = get_game(int(game_id)) or {}
        # If ended_at/end_reason are useful in template, they’ll be present.

        return render_template(
            "feedback.html",
            **game_state,
            english_sentence=english_sentence,
            user_norwegian=user_norwegian,
            evaluation=evaluation.model_dump(),
        )

    @app.route("/game/next", methods=["POST"])
    def game_next():
        game_id = session.get("game_id")
        if not game_id:
            return redirect(url_for("index"))

        game_state = get_game(int(game_id))
        if game_state and game_state.get("status") == "ended":
            return redirect(url_for("game_result"))

        return redirect(url_for("game"))

    @app.route("/game/result", methods=["GET"])
    def game_result():
        game_id = session.get("game_id")
        if not game_id:
            return redirect(url_for("index"))

        game_state = get_game(int(game_id))
        if not game_state or game_state.get("status") != "ended":
            return redirect(url_for("index"))

        return render_template("game_result.html", **game_state)

    @app.route("/history", methods=["GET"])
    def history():
        conn = get_db_connection()
        conn.execute("PRAGMA foreign_keys = ON;")

        games = conn.execute(
            """
            SELECT
                id,
                level,
                status,
                end_reason,
                started_at,
                ended_at
            FROM games
            ORDER BY datetime(started_at) DESC, id DESC
            LIMIT 200
            """
        ).fetchall()

        conn.close()

        return render_template("history.html", games=games)

    @app.route("/history/<int:game_id>", methods=["GET"])
    def history_detail(game_id: int):
        conn = get_db_connection()
        conn.execute("PRAGMA foreign_keys = ON;")

        game = conn.execute(
            """
            SELECT id, level, correct_streak, incorrect_streak, turns_at_level,
                status, end_reason, started_at, ended_at
            FROM games
            WHERE id = ?
            """,
            (game_id,),
        ).fetchone()

        if game is None:
            conn.close()
            return redirect(url_for("history"))

        rows = conn.execute(
            """
            SELECT
                ta.id AS attempt_id,
                ta.level AS attempt_level,
                ta.created_at,
                ta.sentence_id,
                ta.english_sentence,
                ta.user_norwegian,
                ta.verdict,
                tf.feedback_json
            FROM translation_attempts ta
            JOIN translation_feedback tf ON tf.id = ta.feedback_id
            WHERE ta.game_id = ?
            ORDER BY ta.id ASC
            """,
            (game_id,),
        ).fetchall()

        conn.close()
        print(rows)

        # Group attempts by level and decode evaluation JSON for template use
        grouped = defaultdict(list)
        for r in rows:
            d = dict(r)
            try:
                d["evaluation"] = json.loads(d["feedback_json"]) if d.get("feedback_json") else {}
            except Exception:
                d["evaluation"] = {}
            grouped[d["attempt_level"]].append(d)

        # Keep order A1..C2
        LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
        attempts_by_level = [(lvl, grouped[lvl]) for lvl in LEVELS if grouped.get(lvl)]


        return render_template(
            "history_detail.html",
            game=game,
            attempts_by_level=attempts_by_level,
        )

