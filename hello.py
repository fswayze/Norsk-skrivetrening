import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session
from dotenv import load_dotenv
import random
from datetime import datetime
from source_sentences import (
    A1_SEED,
    A2_SEED,
    B1_SEED,
    B2_SEED,
    C1_SEED,
    C2_SEED
);
from openai import OpenAI
from pydantic import BaseModel, Field
from typing import Literal, List
from ai.evaluator import evaluate_translation

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"
load_dotenv()

PROMPT = "Skriv et kort avsnitt om en morgenrutine du liker."
DB_PATH = "data/app.db"
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
SEED_BY_LEVEL = {
    "A1": A1_SEED,
    "A2": A2_SEED,
    "B1": B1_SEED,
    "B2": B2_SEED,
    "C1": C1_SEED,
    "C2": C2_SEED,
}


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row  # gjør at vi kan lese kolonner med navn
    return connection

def init_db():
    connection = get_db_connection()
    connection.execute("PRAGMA foreign_keys = ON;")

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            sentence TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS valid_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sentence_id INTEGER NOT NULL,
            translation TEXT NOT NULL,
            FOREIGN KEY (sentence_id)
                REFERENCES source_sentences(id)
                ON DELETE CASCADE
        );

         -- Idempotency / data quality:
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_sentences_unique
            ON source_sentences(level, sentence);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_valid_translations_unique
            ON valid_translations(sentence_id, translation);

        CREATE INDEX IF NOT EXISTS idx_valid_translations_sentence_id
            ON valid_translations(sentence_id);
        """
    )

    connection.commit()
    connection.close()

def seed_db():
    connection = get_db_connection()
    connection.execute("PRAGMA foreign_keys = ON;")

    for level, items in SEED_BY_LEVEL.items():
        for english_sentence, bokmaal_translations in items:
            english_sentence = (english_sentence or "").strip()
            if not english_sentence:
                continue

            # 1) Insert sentence (idempotent)
            connection.execute(
                "INSERT OR IGNORE INTO source_sentences (level, sentence) VALUES (?, ?)",
                (level, english_sentence),
            )

            # 2) Fetch sentence_id (works whether it was inserted now or already existed)
            row = connection.execute(
                "SELECT id FROM source_sentences WHERE level = ? AND sentence = ?",
                (level, english_sentence),
            ).fetchone()
            sentence_id = row["id"]

            # 3) Insert translations (idempotent)
            cleaned = []
            for t in bokmaal_translations:
                t = (t or "").strip()
                if t:
                    cleaned.append((sentence_id, t))

            connection.executemany(
                "INSERT OR IGNORE INTO valid_translations (sentence_id, translation) VALUES (?, ?)",
                cleaned,
            )

    connection.commit()
    connection.close()


with app.app_context():
    init_db()
    seed_db()

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

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
    else:
        row = None

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


@app.route("/game/start", methods=["POST"])
def game_start():
    session["game"] = new_game_state()
    return redirect(url_for("game"))

@app.route("/game", methods=["GET"])
def game():
    game_state = session.get("game")
    if game_state["status"] == "ended":
        return redirect(url_for("game_result"))
    if not game_state or game_state.get("status") != "active":
        return redirect(url_for("index"))  # or a dedicated game home page

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

    # Inputs
    user_norwegian = request.form.get("norwegian", "").strip()
    english_sentence = request.form.get("english_sentence", "").strip()
    sentence_id = request.form.get("sentence_id")
    print(sentence_id, '218')
    evaluation = evaluate_translation(
        game_state["level"],
        english_sentence,
        user_norwegian,
        sentence_id
    )
    print(evaluation)

    verdict = evaluation.verdict

    print(verdict)

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

    session["game"] = game_state

    return render_template(
    "feedback.html",
    **game_state,
    english_sentence=english_sentence,
    user_norwegian=user_norwegian,
    evaluation=evaluation.model_dump(),  # makes it Jinja-friendly
    )

@app.route("/game/next", methods=["POST"])
def game_next():
    game_state = session.get("game")
    if game_state["status"] == "ended":
        return redirect(url_for("game_result"))
    return redirect(url_for("game"))


@app.route("/game/result", methods=["GET"])
def game_result():
    game_state = session.get("game")
    if not game_state or game_state.get("status") != "ended":
        return redirect(url_for("index"))

    return render_template("game_result.html", **game_state)
