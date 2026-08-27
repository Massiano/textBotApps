"""Access to the content database."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import config

_local = threading.local()
SCHEMA = (config.BASE_DIR / "content" / "schema.sql")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        config.CONTENT_DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.CONTENT_DB, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init():
    conn().executescript(SCHEMA.read_text(encoding="utf-8"))
    conn().commit()


def rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


# ----------------------------------------------------------------- subjects

def add_subject(domain, title, **kw):
    cur = conn().execute(
        "INSERT OR IGNORE INTO subject (domain, title, year, aliases_json, "
        "recognizability, retellability, min_frontier, concreteness, "
        "distractor_group, status, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (domain, title, kw.get("year"), json.dumps(kw.get("aliases", [])),
         kw.get("recognizability", 3), kw.get("retellability", 3),
         kw.get("min_frontier", 0), kw.get("concreteness", 3),
         kw.get("distractor_group"), kw.get("status", "active"), kw.get("note")))
    conn().commit()
    return cur.lastrowid


def update_subject(subject_id, **fields):
    allowed = {"recognizability", "retellability", "min_frontier", "concreteness",
               "distractor_group", "status", "note", "title", "year"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn().execute(f"UPDATE subject SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                   (*sets.values(), subject_id))
    conn().commit()


def subjects(domain=None, status="active", min_frontier=None):
    sql = "SELECT * FROM subject WHERE 1=1"
    args = []
    if domain:
        sql += " AND domain=?"; args.append(domain)
    if status:
        sql += " AND status=?"; args.append(status)
    if min_frontier is not None:
        sql += " AND min_frontier<=?"; args.append(min_frontier)
    return rows(sql + " ORDER BY recognizability DESC, title", args)


def distractors_for(subject, limit=3):
    """Real alternatives from the same cluster, so options are never invented."""
    out = rows(
        "SELECT title FROM subject WHERE domain=? AND status='active' AND id<>? "
        "AND distractor_group=? ORDER BY RANDOM() LIMIT ?",
        (subject["domain"], subject["id"], subject["distractor_group"], limit))
    if len(out) < limit:
        out += rows(
            "SELECT title FROM subject WHERE domain=? AND status='active' AND id<>? "
            "AND title NOT IN (%s) ORDER BY RANDOM() LIMIT ?"
            % ",".join("?" * len(out)),
            (subject["domain"], subject["id"], *[o["title"] for o in out],
             limit - len(out)))
    return [o["title"] for o in out]


# ------------------------------------------------------------------ riddles

def save_riddle(payload):
    rid = payload.get("id") or str(uuid.uuid4())
    conn().execute(
        "INSERT INTO riddle (id, lang, domain, subject_id, answer, options_json, emoji, "
        "text, level, ceiling_rank, new_json, lemmas_json, names_json, status, model, "
        "drafts, origin, stats_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, payload["lang"], payload["domain"], payload.get("subject_id"),
         payload["answer"], json.dumps(payload["options"]), payload.get("emoji"),
         payload["text"], payload["level"], payload["ceiling_rank"],
         json.dumps(payload["new"]), json.dumps(payload["lemmas"]),
         json.dumps(payload.get("names", [])), payload.get("status", "draft"),
         payload.get("model"), payload.get("drafts", 1),
         payload.get("origin", "batch"), json.dumps(payload.get("stats", {})), now()))
    conn().executemany(
        "INSERT OR REPLACE INTO riddle_new (riddle_id, lang, lemma, rank) VALUES (?,?,?,?)",
        [(rid, payload["lang"], w, payload.get("new_ranks", {}).get(w))
         for w in payload["new"]])
    conn().commit()
    return rid


def get_riddle(rid):
    r = one("SELECT * FROM riddle WHERE id=?", (rid,))
    return _hydrate(r) if r else None


def _hydrate(r):
    for k, t in (("options_json", "options"), ("new_json", "new"),
                 ("lemmas_json", "lemmas"), ("names_json", "names")):
        r[t] = json.loads(r.pop(k) or "[]")
    for k, t in (("stats_json", "stats"), ("probe_json", "probe")):
        r[t] = json.loads(r.pop(k) or "{}")
    return r


def set_status(rid, status, reason=None, note=None):
    conn().execute(
        "UPDATE riddle SET status=?, reject_reason=?, review_note=COALESCE(?, review_note), "
        "reviewed_at=? WHERE id=?", (status, reason, note, now(), rid))
    conn().commit()


def set_probe_result(rid, result):
    conn().execute("UPDATE riddle SET probe_json=? WHERE id=?",
                   (json.dumps(result), rid))
    conn().commit()


def queue(status="candidate", lang=None, limit=50):
    sql = "SELECT * FROM riddle WHERE status=?"
    args = [status]
    if lang:
        sql += " AND lang=?"; args.append(lang)
    # Probed riddles first: a reviewer should spend attention on items the
    # machine has already vetted, not on ones it has not looked at yet.
    return [_hydrate(r) for r in rows(
        sql + " ORDER BY (probe_json IS NULL), created_at LIMIT ?", (*args, limit))]


def counts():
    return {r["status"]: r["n"] for r in
            rows("SELECT status, COUNT(*) n FROM riddle GROUP BY status")}


def subject_usage(lang, domain, limit=30):
    return [r["answer"] for r in rows(
        "SELECT answer FROM riddle WHERE lang=? AND domain=? "
        "ORDER BY created_at DESC LIMIT ?", (lang, domain, limit))]


# ----------------------------------------------------------------- coverage

def coverage(lang, domain=None, lo=0, hi=8000):
    """How many accepted riddles teach each lemma at a reachable ceiling.

    This is the corpus's real quality metric: not how many riddles exist, but
    how many words can actually be taught.
    """
    sql = ("SELECT n.lemma, n.rank, COUNT(*) AS n FROM riddle_new n "
           "JOIN riddle r ON r.id = n.riddle_id "
           "WHERE n.lang=? AND r.status='accepted' AND r.ceiling_rank <= n.rank "
           "AND n.rank >= ? AND n.rank < ?")
    args = [lang, lo, hi]
    if domain:
        sql += " AND r.domain=?"; args.append(domain)
    return rows(sql + " GROUP BY n.lemma ORDER BY n.rank", args)


def match(lang, domain, frontier, wanted=(), exclude_ids=(), unknown=(), limit=20):
    """Accepted riddles servable to this learner, preferring wanted words."""
    sql = ("SELECT DISTINCT r.* FROM riddle r "
           "LEFT JOIN riddle_new n ON n.riddle_id = r.id "
           "WHERE r.lang=? AND r.status='accepted' AND r.ceiling_rank <= ?")
    args = [lang, frontier]
    if domain:
        sql += " AND r.domain=?"; args.append(domain)
    if exclude_ids:
        sql += " AND r.id NOT IN (%s)" % ",".join("?" * len(exclude_ids))
        args += list(exclude_ids)
    if wanted:
        sql += " AND n.lemma IN (%s)" % ",".join("?" * len(wanted))
        args += list(wanted)
    out = [_hydrate(r) for r in rows(sql + " ORDER BY RANDOM() LIMIT ?", (*args, limit))]
    # The ceiling covers the frequency-based known set; this covers the handful
    # of common words this particular learner marked unknown.
    bad = set(unknown)
    return [r for r in out if not (bad & set(r["lemmas"]))]


# --------------------------------------------------------------- teachability

def teachability_overrides(lang):
    return {r["lemma"]: bool(r["teachable"])
            for r in rows("SELECT lemma, teachable FROM teachability WHERE lang=?", (lang,))}


def set_teachable(lang, lemma, teachable, note=None):
    conn().execute(
        "INSERT INTO teachability (lang, lemma, teachable, note) VALUES (?,?,?,?) "
        "ON CONFLICT(lang, lemma) DO UPDATE SET teachable=excluded.teachable, note=excluded.note",
        (lang, lemma, int(teachable), note))
    conn().commit()


# --------------------------------------------------------------------- jobs

def enqueue(kind, lang, domain=None, level=None, want=1, note=None):
    cur = conn().execute(
        "INSERT INTO job (kind, lang, domain, level, want, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?, 'queued', ?, ?)", (kind, lang, domain, level, want, now(), now()))
    conn().commit()
    return cur.lastrowid


def next_job():
    return one("SELECT * FROM job WHERE status IN ('queued','running') ORDER BY id LIMIT 1")


def update_job(job_id, **f):
    allowed = {"done", "failed", "status", "note", "want"}
    sets = {k: v for k, v in f.items() if k in allowed}
    sets["updated_at"] = now()
    conn().execute(f"UPDATE job SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                   (*sets.values(), job_id))
    conn().commit()


def jobs(limit=40):
    return rows("SELECT * FROM job ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------- telemetry

def log_attempt(lang, domain, level, model, drafts, outcome, detail=None, seconds=None):
    conn().execute(
        "INSERT INTO attempt (lang, domain, level, model, drafts, outcome, detail, "
        "seconds, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (lang, domain, level, model, drafts, outcome, detail, seconds, now()))
    conn().commit()


def yield_by_model(lang=None):
    """Drafts per acceptance, per model. This is what refinement optimises."""
    sql = ("SELECT model, COUNT(*) attempts, "
           "SUM(CASE WHEN outcome='accepted' THEN 1 ELSE 0 END) accepted, "
           "AVG(drafts) avg_drafts, AVG(seconds) avg_seconds "
           "FROM attempt WHERE model IS NOT NULL")
    args = []
    if lang:
        sql += " AND lang=?"; args.append(lang)
    out = rows(sql + " GROUP BY model ORDER BY accepted DESC", args)
    for r in out:
        r["yield"] = round((r["accepted"] or 0) / r["attempts"], 3) if r["attempts"] else 0
        r["avg_drafts"] = round(r["avg_drafts"] or 0, 2)
        r["avg_seconds"] = round(r["avg_seconds"] or 0, 1)
    return out


def failure_reasons(limit=12):
    return rows("SELECT outcome, detail, COUNT(*) n FROM attempt "
                "WHERE outcome<>'accepted' GROUP BY outcome, detail "
                "ORDER BY n DESC LIMIT ?", (limit,))


def add_probe(riddle_id, kind, model, response, correct, cues=None):
    conn().execute(
        "INSERT INTO probe (riddle_id, kind, model, response, correct, cues, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (riddle_id, kind, model, response, int(bool(correct)), cues, now()))
    conn().commit()
