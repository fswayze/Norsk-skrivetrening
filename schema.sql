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

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    level TEXT NOT NULL DEFAULT 'A1',
    correct_streak INTEGER NOT NULL DEFAULT 0,
    incorrect_streak INTEGER NOT NULL DEFAULT 0,
    turns_at_level INTEGER NOT NULL DEFAULT 0,

    last_sentence_id INTEGER NULL,

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'ended')),

    end_reason TEXT NULL,
    locked_sentence_id INTEGER,
    locked_since TEXT,


    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at   TEXT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (last_sentence_id)
        REFERENCES source_sentences(id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);
CREATE INDEX IF NOT EXISTS idx_games_started_at ON games(started_at);

CREATE TABLE IF NOT EXISTS translation_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- cache key parts (kept as columns for querying/debugging)
    level TEXT NOT NULL,
    sentence_id INTEGER NOT NULL,

    model_id TEXT NOT NULL,           -- e.g. "gpt-5-nano"
    prompt_version TEXT NOT NULL,     -- e.g. "2025-08-07-1.0"

    translation_norm TEXT NOT NULL,   -- normalized user translation (optional but useful for debugging)
    translation_hash TEXT NOT NULL,   -- SHA-256 (hex) of translation_norm

    signature TEXT NOT NULL UNIQUE,   -- concatenation of the above (or hash of them)

    verdict TEXT NOT NULL,            -- correct/minor/incorrect
    feedback_json TEXT NOT NULL,      -- full evaluator output

    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    hit_count INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (sentence_id)
        REFERENCES source_sentences(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feedback_lookup
    ON translation_feedback(level, sentence_id, model_id, prompt_version, translation_hash);

CREATE TABLE IF NOT EXISTS translation_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    game_id INTEGER NOT NULL,
    sentence_id INTEGER NOT NULL,
    level TEXT NOT NULL,

    english_sentence TEXT NOT NULL,
    user_norwegian TEXT NOT NULL,

    verdict TEXT NOT NULL,

    feedback_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),

    FOREIGN KEY (game_id)
        REFERENCES games(id)
        ON DELETE CASCADE,

    FOREIGN KEY (sentence_id)
        REFERENCES source_sentences(id)
        ON DELETE CASCADE,

    FOREIGN KEY (feedback_id)
        REFERENCES translation_feedback(id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_translation_attempts_game_id
    ON translation_attempts(game_id);


-- Idempotency / data quality:
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_sentences_unique
    ON source_sentences(level, sentence);

CREATE UNIQUE INDEX IF NOT EXISTS idx_valid_translations_unique
    ON valid_translations(sentence_id, translation);

CREATE INDEX IF NOT EXISTS idx_valid_translations_sentence_id
    ON valid_translations(sentence_id);