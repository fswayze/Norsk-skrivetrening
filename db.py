import sqlite3
import os
from source_sentences import (
    A1_SEED,
    A2_SEED,
    B1_SEED,
    B2_SEED,
    C1_SEED,
    C2_SEED
);

DB_PATH = "data/app.db"
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
    connection.row_factory = sqlite3.Row 
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection

def init_db():
    connection = get_db_connection()

    schema_path = os.path.join(
        os.path.dirname(__file__),
        "schema.sql"
    )

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    connection.executescript(schema_sql)
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