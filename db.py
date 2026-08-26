"""SQLite persistence.

A learner is identified by a cookie, not an account. IP and user agent are kept
alongside as weak recovery hints, never as the lookup key.

Word knowledge is stored per lemma rather than per surface form, which is what
makes "you already know 'run', so 'ran' is free" work.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    learner_id     TEXT NOT NULL,
    lang           TEXT NOT NULL,
    vocab_estimate INTEGER,
    frontier_rank  INTEGER,
    cefr           TEXT,
    false_alarm    REAL,
    reliable       INTEGER,
    consistent     INTEGER,
    bands_json     TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (learner_id, lang)
);

CREATE TABLE IF NOT EXISTS word_state (
    learner_id TEXT NOT NULL,
    lang       TEXT NOT NULL,
    lemma      TEXT NOT NULL,
    state      TEXT NOT NULL,          -- known | unknown | learning | mastered
    source     TEXT,                   -- placement | interest | round | manual
    seen_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (learner_id, lang, lemma)
);

CREATE TABLE IF NOT EXISTS interest (
    learner_id TEXT NOT NULL,
    lang       TEXT NOT NULL,
    topic      TEXT NOT NULL,
    PRIMARY KEY (learner_id, lang, topic)
);

CREATE TABLE IF NOT EXISTS pending_test (
    id         TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    lang       TEXT NOT NULL,
    kind       TEXT NOT NULL,          -- placement | interest
    key_json   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS round (
    id           TEXT PRIMARY KEY,
    learner_id   TEXT NOT NULL,
    lang         TEXT NOT NULL,
    domain       TEXT NOT NULL,
    answer       TEXT NOT NULL,
    options_json TEXT NOT NULL,
    text         TEXT NOT NULL,
    targets_json TEXT NOT NULL,
    tokens_json  TEXT NOT NULL,
    meta_json    TEXT,
    created_at   TEXT NOT NULL,
    answered     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_learner ON word_state (learner_id, lang, state);
CREATE INDEX IF NOT EXISTS idx_round_learner ON round (learner_id, lang, created_at);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init():
    conn().executescript(SCHEMA)
    conn().commit()


# ------------------------------------------------------------------ learner

def touch_learner(learner_id, ip=None, ua=None):
    c = conn()
    c.execute(
        "INSERT INTO learner (id, created_at, last_seen, ip, user_agent) VALUES (?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, ip=excluded.ip, user_agent=excluded.user_agent",
        (learner_id, now(), now(), ip, ua),
    )
    c.commit()


# ------------------------------------------------------------------ profile

def save_profile(learner_id, lang, report):
    c = conn()
    c.execute(
        "INSERT INTO profile (learner_id, lang, vocab_estimate, frontier_rank, cefr, false_alarm, reliable, consistent, bands_json, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(learner_id, lang) DO UPDATE SET "
        "vocab_estimate=excluded.vocab_estimate, frontier_rank=excluded.frontier_rank, cefr=excluded.cefr, "
        "false_alarm=excluded.false_alarm, reliable=excluded.reliable, consistent=excluded.consistent, "
        "bands_json=excluded.bands_json, updated_at=excluded.updated_at",
        (learner_id, lang, report["vocab_estimate"], report["frontier_rank"], report["cefr"],
         report["false_alarm_rate"], int(report["reliable"]), int(report["consistent"]),
         json.dumps(report["bands"]), now()),
    )
    c.commit()


def get_profile(learner_id, lang):
    row = conn().execute(
        "SELECT * FROM profile WHERE learner_id=? AND lang=?", (learner_id, lang)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["bands"] = json.loads(d.pop("bands_json") or "[]")
    d["reliable"] = bool(d["reliable"])
    d["consistent"] = bool(d["consistent"]) if d.get("consistent") is not None else True
    return d


def all_profiles(learner_id):
    rows = conn().execute(
        "SELECT lang, vocab_estimate, cefr, updated_at FROM profile WHERE learner_id=?", (learner_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------- word state

def set_word_states(learner_id, lang, lemmas, state, source):
    if not lemmas:
        return
    c = conn()
    ts = now()
    c.executemany(
        "INSERT INTO word_state (learner_id, lang, lemma, state, source, seen_count, updated_at) "
        "VALUES (?,?,?,?,?,1,?) ON CONFLICT(learner_id, lang, lemma) DO UPDATE SET "
        "state=excluded.state, source=excluded.source, seen_count=word_state.seen_count+1, updated_at=excluded.updated_at",
        [(learner_id, lang, l.lower(), state, source, ts) for l in lemmas],
    )
    c.commit()


def bump_seen(learner_id, lang, lemmas):
    """Record exposure without asserting a state change."""
    if not lemmas:
        return
    c = conn()
    ts = now()
    c.executemany(
        "INSERT INTO word_state (learner_id, lang, lemma, state, source, seen_count, updated_at) "
        "VALUES (?,?,?,'learning','round',1,?) ON CONFLICT(learner_id, lang, lemma) DO UPDATE SET "
        "seen_count=word_state.seen_count+1, updated_at=excluded.updated_at",
        [(learner_id, lang, l.lower(), ts) for l in lemmas],
    )
    c.commit()


def words_in_state(learner_id, lang, *states):
    q = "SELECT lemma FROM word_state WHERE learner_id=? AND lang=? AND state IN (%s)" % ",".join("?" * len(states))
    return [r["lemma"] for r in conn().execute(q, (learner_id, lang, *states)).fetchall()]


def word_rows(learner_id, lang):
    return [dict(r) for r in conn().execute(
        "SELECT lemma, state, source, seen_count, updated_at FROM word_state "
        "WHERE learner_id=? AND lang=? ORDER BY updated_at DESC", (learner_id, lang)).fetchall()]


# ---------------------------------------------------------------- interests

def set_interests(learner_id, lang, topics):
    c = conn()
    c.execute("DELETE FROM interest WHERE learner_id=? AND lang=?", (learner_id, lang))
    c.executemany("INSERT OR IGNORE INTO interest (learner_id, lang, topic) VALUES (?,?,?)",
                  [(learner_id, lang, t.strip()) for t in topics if t.strip()])
    c.commit()


def get_interests(learner_id, lang):
    return [r["topic"] for r in conn().execute(
        "SELECT topic FROM interest WHERE learner_id=? AND lang=?", (learner_id, lang)).fetchall()]


# ------------------------------------------------------------ pending tests

def save_test(learner_id, lang, kind, key):
    tid = str(uuid.uuid4())
    conn().execute(
        "INSERT INTO pending_test (id, learner_id, lang, kind, key_json, created_at) VALUES (?,?,?,?,?,?)",
        (tid, learner_id, lang, kind, json.dumps(key), now()),
    )
    conn().commit()
    return tid


def load_test(test_id, learner_id):
    row = conn().execute(
        "SELECT * FROM pending_test WHERE id=? AND learner_id=?", (test_id, learner_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["key"] = {int(k): v for k, v in json.loads(d.pop("key_json")).items()}
    return d


# ------------------------------------------------------------------- rounds

def save_round(learner_id, lang, domain, payload):
    rid = str(uuid.uuid4())
    conn().execute(
        "INSERT INTO round (id, learner_id, lang, domain, answer, options_json, text, targets_json, tokens_json, meta_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rid, learner_id, lang, domain, payload["answer"], json.dumps(payload["options"]),
         payload["text"], json.dumps(payload["targets"]), json.dumps(payload["tokens"]),
         json.dumps(payload.get("meta", {})), now()),
    )
    conn().commit()
    return rid


def get_round(round_id, learner_id):
    row = conn().execute("SELECT * FROM round WHERE id=? AND learner_id=?", (round_id, learner_id)).fetchone()
    return dict(row) if row else None


def answer_round(round_id, learner_id, choice):
    conn().execute("UPDATE round SET answered=? WHERE id=? AND learner_id=?", (choice, round_id, learner_id))
    conn().commit()


def recent_answers(learner_id, lang, limit=25):
    return [r["answer"] for r in conn().execute(
        "SELECT answer FROM round WHERE learner_id=? AND lang=? ORDER BY created_at DESC LIMIT ?",
        (learner_id, lang, limit)).fetchall()]


def export_all():
    out = {}
    for t in ("learner", "profile", "word_state", "interest", "round"):
        out[t] = [dict(r) for r in conn().execute(f"SELECT * FROM {t}").fetchall()]
    return out
