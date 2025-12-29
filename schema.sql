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