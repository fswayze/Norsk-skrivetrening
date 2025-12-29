from __future__ import annotations

from datetime import datetime
from typing import Optional

from flask import render_template, request, redirect, url_for, session

from db import get_db_connection
from ai.evaluator import evaluate_translation

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


def new_game_state():
    return {
        "game_id": datetime.now().isoformat(timespec="seconds"),
        "level": "A1",
        "correct_streak": 0,
        "incorrect_streak": 0,
        "turns_at_level": 0,
        "last_sentence_id": None,
        "status": "active",
    }


def pick_sentence(level: str, avoid_id=None):
    """
    Picks a random English prompt from source_sentences for the given level.
    avoid_id: last sentence id shown (DB id), to reduce immediate repeats.
    Returns: {"id": int, "english": str}
    """
    connection = get_db_connection()
    connection.execute("PRAGMA foreign_keys = ON;")

    row = None

    # First try: avoid repeating the previous sentence
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

    # Fallback: no avoid_id provided or only one sentence exists at this level
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


def register_routes(app):
    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    @app.route("/game/start", methods=["POST"])
    def game_start():
        session["game"] = new_game_state()
        return redirect(url_for("game"))

    @app.route("/game", methods=["GET"])
    def game():
        game_state = session.get("game")

        if not game_state or game_state.get("status") != "active":
            return redirect(url_for("index"))

        if game_state.get("status") == "ended":
            return redirect(url_for("game_result"))

        sentence = pick_sentence(
            game_state["level"],
            avoid_id=game_state.get("last_sentence_id")
        )
        game_state["last_sentence_id"] = sentence["id"]
        session["game"] = game_state

        context = {
            **game_state,
            "sentence_id": sentence["id"],
            "english_sentence": sentence["english"],
        }
        return render_template("game.html", **context)

    @app.route("/game/submit", methods=["POST"])
    def game_submit():
        game_state = session.get("game")
        if not game_state or game_state.get("status") != "active":
            return redirect(url_for("index"))

        user_norwegian = request.form.get("norwegian", "").strip()
        english_sentence = request.form.get("english_sentence", "").strip()

        sentence_id_raw = request.form.get("sentence_id")
        sentence_id: Optional[int] = None
        if sentence_id_raw and str(sentence_id_raw).isdigit():
            sentence_id = int(sentence_id_raw)

        evaluation = evaluate_translation(
            game_state["level"],
            english_sentence,
            user_norwegian,
            sentence_id=sentence_id,   # IMPORTANT: pass as int/None
        )

        verdict = evaluation.verdict

        # Update counters
        game_state["turns_at_level"] += 1

        if verdict == "correct":
            game_state["correct_streak"] += 1
            game_state["incorrect_streak"] = 0
        elif verdict == "incorrect":
            game_state["incorrect_streak"] += 1
            game_state["correct_streak"] = 0
        else:  # minor
            game_state["correct_streak"] = 0
            game_state["incorrect_streak"] = 0

        # End conditions
        ended = False
        reason = None

        if game_state["incorrect_streak"] >= 2:
            ended = True
            reason = "two_incorrect"
        elif game_state["turns_at_level"] >= 5 and game_state["correct_streak"] < 2:
            ended = True
            reason = "no_progress"

        # Level up
        if not ended and game_state["correct_streak"] >= 2:
            current = game_state["level"]
            idx = LEVELS.index(current)
            if idx < len(LEVELS) - 1:
                game_state["level"] = LEVELS[idx + 1]
            game_state["correct_streak"] = 0
            game_state["incorrect_streak"] = 0
            game_state["turns_at_level"] = 0

        if ended:
            game_state["status"] = "ended"
            game_state["end_reason"] = reason

        session["game"] = game_state

        return render_template(
            "feedback.html",
            **game_state,
            english_sentence=english_sentence,
            user_norwegian=user_norwegian,
            evaluation=evaluation.model_dump(),
        )

    @app.route("/game/next", methods=["POST"])
    def game_next():
        game_state = session.get("game")
        if game_state and game_state.get("status") == "ended":
            return redirect(url_for("game_result"))
        return redirect(url_for("game"))

    @app.route("/game/result", methods=["GET"])
    def game_result():
        game_state = session.get("game")
        if not game_state or game_state.get("status") != "ended":
            return redirect(url_for("index"))

        return render_template("game_result.html", **game_state)
