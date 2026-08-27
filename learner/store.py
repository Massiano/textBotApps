"""Learner database: profiles and per-lemma knowledge.

Kept separate from the content database on purpose. That one holds shippable
content; this one holds personal data, and the two have different lifecycles,
different backup needs and different privacy weight.
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
    id TEXT PRIMARY KEY, created_at TEXT, last_seen TEXT,
    l1 TEXT, ip TEXT, user_agent TEXT
);
CREATE TABLE IF NOT EXISTS profile (
    learner_id TEXT, lang TEXT,
    vocab_estimate INTEGER, frontier_rank INTEGER, cefr TEXT,
    false_alarm REAL, reliable INTEGER, consistent INTEGER,
    transparency_gap REAL, bands_json TEXT, updated_at TEXT,
    PRIMARY KEY (learner_id, lang)
);
CREATE TABLE IF NOT EXISTS word_state (
    learner_id TEXT, lang TEXT, lemma TEXT,
    state TEXT, source TEXT, seen_count INTEGER DEFAULT 0, updated_at TEXT,
    PRIMARY KEY (learner_id, lang, lemma)
);
CREATE TABLE IF NOT EXISTS seen_riddle (
    learner_id TEXT, riddle_id TEXT, lang TEXT,
    answered TEXT, correct INTEGER, at TEXT,
    PRIMARY KEY (learner_id, riddle_id)
);
CREATE TABLE IF NOT EXISTS pending_test (
    id TEXT PRIMARY KEY, learner_id TEXT, lang TEXT, kind TEXT,
    key_json TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ws ON word_state (learner_id, lang, state);
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
        _local.conn = c
    return c


def init():
    conn().executescript(SCHEMA)
    conn().commit()


def touch(learner_id, ip=None, ua=None):
    conn().execute(
        "INSERT INTO learner (id, created_at, last_seen, ip, user_agent) VALUES (?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, ip=excluded.ip, "
        "user_agent=excluded.user_agent", (learner_id, now(), now(), ip, ua))
    conn().commit()


def set_l1(learner_id, l1):
    conn().execute("UPDATE learner SET l1=? WHERE id=?", (l1, learner_id))
    conn().commit()


def get_l1(learner_id):
    r = conn().execute("SELECT l1 FROM learner WHERE id=?", (learner_id,)).fetchone()
    return r["l1"] if r else None


def save_profile(learner_id, lang, rep):
    conn().execute(
        "INSERT INTO profile (learner_id, lang, vocab_estimate, frontier_rank, cefr, "
        "false_alarm, reliable, consistent, transparency_gap, bands_json, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(learner_id, lang) DO UPDATE SET "
        "vocab_estimate=excluded.vocab_estimate, frontier_rank=excluded.frontier_rank, "
        "cefr=excluded.cefr, false_alarm=excluded.false_alarm, reliable=excluded.reliable, "
        "consistent=excluded.consistent, transparency_gap=excluded.transparency_gap, "
        "bands_json=excluded.bands_json, updated_at=excluded.updated_at",
        (learner_id, lang, rep["vocab_estimate"], rep["frontier_rank"], rep["cefr"],
         rep["false_alarm_rate"], int(rep["reliable"]), int(rep["consistent"]),
         rep.get("transparency_gap"), json.dumps(rep["bands"]), now()))
    conn().commit()


def get_profile(learner_id, lang):
    r = conn().execute("SELECT * FROM profile WHERE learner_id=? AND lang=?",
                       (learner_id, lang)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["bands"] = json.loads(d.pop("bands_json") or "[]")
    d["reliable"] = bool(d["reliable"])
    d["consistent"] = bool(d["consistent"]) if d["consistent"] is not None else True
    return d


def set_frontier(learner_id, lang, frontier):
    conn().execute("UPDATE profile SET frontier_rank=?, updated_at=? "
                   "WHERE learner_id=? AND lang=?", (frontier, now(), learner_id, lang))
    conn().commit()


def set_words(learner_id, lang, lemmas, state, source):
    if not lemmas:
        return
    conn().executemany(
        "INSERT INTO word_state (learner_id, lang, lemma, state, source, seen_count, updated_at) "
        "VALUES (?,?,?,?,?,1,?) ON CONFLICT(learner_id, lang, lemma) DO UPDATE SET "
        "state=excluded.state, source=excluded.source, "
        "seen_count=word_state.seen_count+1, updated_at=excluded.updated_at",
        [(learner_id, lang, l.lower(), state, source, now()) for l in lemmas])
    conn().commit()


def bump(learner_id, lang, lemmas):
    if not lemmas:
        return
    conn().executemany(
        "INSERT INTO word_state (learner_id, lang, lemma, state, source, seen_count, updated_at) "
        "VALUES (?,?,?, 'learning','round',1,?) ON CONFLICT(learner_id, lang, lemma) DO UPDATE SET "
        "seen_count=word_state.seen_count+1, updated_at=excluded.updated_at",
        [(learner_id, lang, l.lower(), now()) for l in lemmas])
    conn().commit()


def words(learner_id, lang, *states):
    q = ("SELECT lemma FROM word_state WHERE learner_id=? AND lang=? AND state IN (%s)"
         % ",".join("?" * len(states)))
    return [r["lemma"] for r in conn().execute(q, (learner_id, lang, *states)).fetchall()]


def word_rows(learner_id, lang):
    return [dict(r) for r in conn().execute(
        "SELECT lemma, state, source, seen_count, updated_at FROM word_state "
        "WHERE learner_id=? AND lang=? ORDER BY updated_at DESC", (learner_id, lang))]


def mark_seen(learner_id, riddle_id, lang):
    conn().execute("INSERT OR IGNORE INTO seen_riddle (learner_id, riddle_id, lang, at) "
                   "VALUES (?,?,?,?)", (learner_id, riddle_id, lang, now()))
    conn().commit()


def record_answer(learner_id, riddle_id, choice, correct):
    conn().execute("UPDATE seen_riddle SET answered=?, correct=? WHERE learner_id=? "
                   "AND riddle_id=?", (choice, int(correct), learner_id, riddle_id))
    conn().commit()


def seen_ids(learner_id, lang, limit=200):
    return [r["riddle_id"] for r in conn().execute(
        "SELECT riddle_id FROM seen_riddle WHERE learner_id=? AND lang=? "
        "ORDER BY at DESC LIMIT ?", (learner_id, lang, limit))]


def recent_results(learner_id, lang, limit=12):
    return [dict(r) for r in conn().execute(
        "SELECT correct FROM seen_riddle WHERE learner_id=? AND lang=? AND answered IS NOT NULL "
        "ORDER BY at DESC LIMIT ?", (learner_id, lang, limit))]


def save_test(learner_id, lang, kind, key):
    tid = str(uuid.uuid4())
    conn().execute("INSERT INTO pending_test (id, learner_id, lang, kind, key_json, created_at) "
                   "VALUES (?,?,?,?,?,?)", (tid, learner_id, lang, kind, json.dumps(key), now()))
    conn().commit()
    return tid


def load_test(test_id, learner_id):
    r = conn().execute("SELECT * FROM pending_test WHERE id=? AND learner_id=?",
                       (test_id, learner_id)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["key"] = {int(k): v for k, v in json.loads(d.pop("key_json")).items()}
    return d
