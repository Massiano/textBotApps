-- CineTot content store: everything produced offline and reviewed.
-- Separate from the learner database on purpose. This one is shippable
-- content; that one is personal data.

CREATE TABLE IF NOT EXISTS subject (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    domain            TEXT NOT NULL,
    title             TEXT NOT NULL,
    year              INTEGER,
    aliases_json      TEXT DEFAULT '[]',
    recognizability   INTEGER DEFAULT 3,   -- 1-5, globally, hand-curated
    retellability     INTEGER DEFAULT 3,   -- 1-5, measured by the probe panel
    min_frontier      INTEGER DEFAULT 0,   -- below this the plot cannot be told
    concreteness      INTEGER DEFAULT 3,   -- physical events vs interior plot
    distractor_group  TEXT,                -- cluster wrong options are drawn from
    status            TEXT DEFAULT 'active',
    note              TEXT,
    UNIQUE (domain, title)
);
CREATE INDEX IF NOT EXISTS idx_subject_pick
    ON subject (domain, status, min_frontier, recognizability);

CREATE TABLE IF NOT EXISTS riddle (
    id            TEXT PRIMARY KEY,
    lang          TEXT NOT NULL,
    domain        TEXT NOT NULL,
    subject_id    INTEGER REFERENCES subject(id),
    answer        TEXT NOT NULL,
    options_json  TEXT NOT NULL,
    emoji         TEXT,
    text          TEXT NOT NULL,

    level         INTEGER NOT NULL,        -- frontier this was generated for
    ceiling_rank  INTEGER NOT NULL,        -- the serving index
    new_json      TEXT NOT NULL,           -- overflow lemmas: the words taught
    lemmas_json   TEXT NOT NULL,           -- all known-side lemmas, for exclusion checks
    names_json    TEXT DEFAULT '[]',

    status        TEXT NOT NULL DEFAULT 'draft',  -- draft|probing|candidate|accepted|rejected
    reject_reason TEXT,
    model         TEXT,
    drafts        INTEGER DEFAULT 1,
    origin        TEXT DEFAULT 'batch',    -- batch | live
    stats_json    TEXT,
    probe_json    TEXT,
    created_at    TEXT NOT NULL,
    reviewed_at   TEXT,
    review_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_riddle_serve
    ON riddle (lang, domain, status, ceiling_rank);
CREATE INDEX IF NOT EXISTS idx_riddle_queue ON riddle (status, lang, created_at);

-- The teaching index. This is the primary key of the corpus: the product is
-- teaching a word, not owning a riddle.
CREATE TABLE IF NOT EXISTS riddle_new (
    riddle_id TEXT NOT NULL REFERENCES riddle(id) ON DELETE CASCADE,
    lang      TEXT NOT NULL,
    lemma     TEXT NOT NULL,
    rank      INTEGER,
    PRIMARY KEY (riddle_id, lemma)
);
CREATE INDEX IF NOT EXISTS idx_new_lemma ON riddle_new (lang, lemma);

CREATE TABLE IF NOT EXISTS probe (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    riddle_id  TEXT NOT NULL REFERENCES riddle(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,      -- open | forced | options_only | cloze
    model      TEXT NOT NULL,
    response   TEXT,
    correct    INTEGER,
    cues       TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_probe_riddle ON probe (riddle_id, kind);

-- Human curation of the teaching ladder overrides the heuristic.
CREATE TABLE IF NOT EXISTS teachability (
    lang      TEXT NOT NULL,
    lemma     TEXT NOT NULL,
    teachable INTEGER NOT NULL,
    note      TEXT,
    PRIMARY KEY (lang, lemma)
);

CREATE TABLE IF NOT EXISTS job (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,      -- generate | probe
    lang        TEXT NOT NULL,
    domain      TEXT,
    level       INTEGER,
    want        INTEGER DEFAULT 1,
    done        INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued|running|finished|cancelled
    note        TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_status ON job (status, id);

-- One row per generation attempt, for the yield telemetry that drives
-- refinement: which model, which cell, how many drafts, why it failed.
CREATE TABLE IF NOT EXISTS attempt (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lang       TEXT NOT NULL,
    domain     TEXT,
    level      INTEGER,
    model      TEXT,
    drafts     INTEGER,
    outcome    TEXT NOT NULL,       -- accepted | rejected_vocab | rejected_probe | error
    detail     TEXT,
    seconds    REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempt_cell ON attempt (lang, domain, level, model);
