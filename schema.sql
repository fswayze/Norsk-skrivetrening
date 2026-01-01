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

    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at   TEXT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (last_sentence_id)
        REFERENCES source_sentences(id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);
CREATE INDEX IF NOT EXISTS idx_games_started_at ON games(started_at);

-- Idempotency / data quality:
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_sentences_unique
    ON source_sentences(level, sentence);

CREATE UNIQUE INDEX IF NOT EXISTS idx_valid_translations_unique
    ON valid_translations(sentence_id, translation);

CREATE INDEX IF NOT EXISTS idx_valid_translations_sentence_id
    ON valid_translations(sentence_id);