import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session
from dotenv import load_dotenv
import random
from datetime import datetime
from english_sentences import (
    A1_TRANSLATION_SENTENCES,
    A2_TRANSLATION_SENTENCES,
    B1_TRANSLATION_SENTENCES,
    B2_TRANSLATION_SENTENCES,
    C1_TRANSLATION_SENTENCES,
    C2_TRANSLATION_SENTENCES
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
SENTENCES_BY_LEVEL = {
    "A1": A1_TRANSLATION_SENTENCES,
    "A2": A2_TRANSLATION_SENTENCES,
    "B1": B1_TRANSLATION_SENTENCES,
    "B2": B2_TRANSLATION_SENTENCES,
    "C1": C1_TRANSLATION_SENTENCES,
    "C2": C2_TRANSLATION_SENTENCES,
}


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row  # gjør at vi kan lese kolonner med navn
    return connection

def init_db():
    connection = get_db_connection()
    # connection.execute(
    #     """
        
    #     """
    # )
    connection.commit()
    connection.close()

with app.app_context():
    init_db()

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
    bank = SENTENCES_BY_LEVEL[level]
    # Simple: sentence_id is index. Avoid repeating the last one if possible.
    if len(bank) == 0:
        return {"id": 0, "english": ""}
    idx = random.randrange(len(bank))
    if avoid_id is not None and len(bank) > 1:
        while idx == avoid_id:
            idx = random.randrange(len(bank))
    return {"id": idx, "english": bank[idx]}


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
    # sentence_id = request.form.get("sentence_id")  # available when you need it

    evaluation = evaluate_translation(
        game_state["level"],
        english_sentence,
        user_norwegian
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
