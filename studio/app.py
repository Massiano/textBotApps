"""The studio: generate, inspect and review precomputed content.

Five jobs, in the order they constrain throughput:

    coverage    which words can actually be taught, and where the corpus is thin
    generate    enqueue work against the thin regions
    review      accept or reject, judging only what the probes cannot
    telemetry   drafts per acceptance by model — this is what refinement means
    subjects    curate recognizability; watch the panel curate retellability

Review is the bottleneck, so it is keyboard-driven and shows the machine's own
findings inline: a reviewer should never have to work out whether a riddle is
solvable or within vocabulary, only whether it reads well and is appropriate.
"""

import json

from flask import Flask, jsonify, request, send_from_directory

import config
import json

from content import jobs, riddles, store, subjects
from core import corpus, ladder, vocab
from providers.fake import FakeGenerator
from providers.openrouter import OpenRouter

app = Flask(__name__, static_folder=None)
STATIC = config.BASE_DIR / "studio" / "static"

GEN = OpenRouter() if config.OPENROUTER_API_KEY else FakeGenerator(seed=11)
WORKER = jobs.Worker(GEN)


def body():
    return request.get_json(silent=True) or {}


def _solvers():
    """Which models the probe panel will actually use, for display."""
    from content import probes
    try:
        return probes.solver_panel(GEN)
    except Exception:
        return []


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


# ---------------------------------------------------------------- overview

@app.route("/api/overview")
def overview():
    counts = store.counts()
    langs = store.rows("SELECT lang, COUNT(*) n FROM riddle GROUP BY lang")
    return jsonify({
        "counts": counts,
        "total": sum(counts.values()),
        "languages": langs,
        "domains": sorted({d["domain"] for d in store.rows("SELECT DISTINCT domain FROM riddle")}
                          | set(config.DOMAIN_IDS)),
        "levels": ladder.levels("en"),
        "worker": WORKER.state(),
        "jobs": store.jobs(12),
        "generator": GEN.name,
        "live_key": bool(config.OPENROUTER_API_KEY),
        "solvers": _solvers(),
        "lang_options": [{"code": k, "label": v} for k, v in config.LANGUAGES.items()],
    })


# ---------------------------------------------------------------- coverage

@app.route("/api/coverage")
def coverage():
    lang = request.args.get("lang", "en")
    domain = request.args.get("domain") or None
    hi = int(request.args.get("hi", 8000))
    rows = store.coverage(lang, domain, 0, hi)
    by_lemma = {r["lemma"]: r["n"] for r in rows}

    overrides = store.teachability_overrides(lang)
    buckets = []
    for label, lo, bhi in config.BANDS:
        if lo >= hi:
            break
        teachable = ladder.ladder(lang, lo, min(bhi, hi), overrides)
        covered = [w for w in teachable if by_lemma.get(w, 0) >= 1]
        well = [w for w in teachable if by_lemma.get(w, 0) >= 3]
        thin = [w for w in teachable if by_lemma.get(w, 0) < 3][:40]
        buckets.append({
            "band": label, "lo": lo, "hi": min(bhi, hi),
            "teachable": len(teachable), "covered": len(covered),
            "well_covered": len(well),
            "pct": round(100 * len(covered) / max(1, len(teachable))),
            "pct_well": round(100 * len(well) / max(1, len(teachable))),
            "thin_sample": thin,
        })
    return jsonify({"lang": lang, "domain": domain, "bands": buckets,
                    "taught_lemmas": len(by_lemma)})


@app.route("/api/ladder")
def ladder_view():
    lang = request.args.get("lang", "en")
    lo = int(request.args.get("lo", 1000))
    hi = int(request.args.get("hi", 1060))
    overrides = store.teachability_overrides(lang)
    lex = corpus.get(lang)
    out = []
    for rank, lemma in enumerate(lex.lemmas[lo:hi], start=lo):
        a = ladder.assess(lemma, lang)
        a["rank"] = rank
        a["override"] = overrides.get(lemma)
        out.append(a)
    return jsonify({"lang": lang, "items": out})


@app.route("/api/teachable", methods=["POST"])
def set_teachable():
    b = body()
    store.set_teachable(b["lang"], b["lemma"], bool(b["teachable"]), b.get("note"))
    return jsonify({"ok": True})


# ------------------------------------------------------------------- jobs

@app.route("/api/generate", methods=["POST"])
def generate():
    b = body()
    lang = b.get("lang", "en")
    domain = b.get("domain", "movies")
    levels = b.get("levels") or [b.get("level", 2000)]
    want = int(b.get("want", 5))
    ids = [store.enqueue("generate", lang, domain, int(lv), want) for lv in levels]
    if b.get("start", True):
        WORKER.start()
    return jsonify({"queued": ids, "worker": WORKER.state()})


@app.route("/api/generate/one", methods=["POST"])
def generate_one():
    """Generate a single riddle synchronously and return what happened.

    A background job that takes forty seconds and reports nothing is
    indistinguishable from a broken button. This runs in the request so the
    first thing a user does gives an immediate, legible answer — including the
    rejection reason when it fails.
    """
    import time
    b = body()
    lang = b.get("lang", "en")
    domain = b.get("domain", "movies")
    level = int(b.get("level", 2000))
    started = time.time()
    try:
        payload = riddles.generate(GEN, lang, config.ENGLISH_NAME.get(lang, "English"),
                                   domain, level)
    except Exception as e:
        return jsonify({"ok": False, "stage": "generate", "error": str(e),
                        "seconds": round(time.time() - started, 1)}), 200

    rid = riddles.persist(payload, origin="manual")
    out = {"ok": payload["accepted_vocab"], "id": rid, "stage": "vocabulary",
           "answer": payload["answer"], "text": payload["text"],
           "new": payload["new"], "ceiling_rank": payload["ceiling_rank"],
           "drafts": payload["drafts"], "model": payload["model"],
           "reason": payload.get("reject_reason"),
           "seconds": round(time.time() - started, 1)}
    if not payload["accepted_vocab"]:
        return jsonify(out)

    if b.get("probe", True):
        from content import probes
        r = store.get_riddle(rid)
        try:
            verdict = probes.run(GEN, r, config.ENGLISH_NAME.get(lang, "English"))
            store.set_probe_result(rid, verdict)
            out["stage"] = "probe"
            out["probe"] = verdict
            out["ok"] = verdict["pass"]
            if not verdict["pass"]:
                store.set_status(rid, "rejected", "; ".join(verdict["reasons"]))
                out["reason"] = "; ".join(verdict["reasons"])
        except Exception as e:
            out["probe_error"] = str(e)
    out["seconds"] = round(time.time() - started, 1)
    return jsonify(out)


@app.route("/api/worker", methods=["POST"])
def worker():
    action = body().get("action")
    if action == "start":
        WORKER.start()
    elif action == "stop":
        WORKER.stop()
    return jsonify(WORKER.state())


@app.route("/api/jobs/<int:job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    store.update_job(job_id, status="cancelled")
    return jsonify({"ok": True})


@app.route("/api/probe/<rid>", methods=["POST"])
def probe_one(rid):
    from content import probes
    r = store.get_riddle(rid)
    if not r:
        return jsonify({"error": "unknown riddle"}), 404
    verdict = probes.run(GEN, r, config.ENGLISH_NAME.get(r["lang"], "English"))
    store.set_probe_result(rid, verdict)
    return jsonify(verdict)


# ------------------------------------------------------------------ review

@app.route("/api/queue")
def review_queue():
    status = request.args.get("status", "candidate")
    lang = request.args.get("lang") or None
    items = store.queue(status, lang, limit=int(request.args.get("limit", 40)))
    for r in items:
        r["subject"] = store.one("SELECT title, recognizability, retellability "
                                 "FROM subject WHERE id=?", (r["subject_id"],))
    return jsonify({"items": items, "counts": store.counts()})


@app.route("/api/review/<rid>", methods=["POST"])
def review(rid):
    b = body()
    decision = b.get("decision")
    if decision not in ("accepted", "rejected", "draft"):
        return jsonify({"error": "bad decision"}), 400
    store.set_status(rid, decision, b.get("reason"), b.get("note"))
    return jsonify({"ok": True, "counts": store.counts()})


# --------------------------------------------------------------- authoring

@app.route("/api/analyse", methods=["POST"])
def analyse():
    """Live vocabulary check for hand-written text.

    The same verifier the generator is judged by, pointed at a human author, so
    writing a retelling by hand is a guided activity rather than a guess.
    """
    b = body()
    lang = b.get("lang", "en")
    level = int(b.get("level", 2000))
    text = b.get("text", "")
    known = vocab.build_known_set(lang, level)
    report = vocab.analyse(text, lang, known)
    ok, reasons, repair = vocab.verdict(report, lang, level)
    lex = corpus.get(lang)
    return jsonify({
        "tokens": report["tokens"],
        "new": [{"lemma": w, "rank": report["overflow_ranks"].get(w)}
                for w in report["overflow"]],
        "names": report["names"],
        "ceiling_rank": report["ceiling_rank"],
        "ok": ok, "reasons": reasons, "repair": repair,
        "stats": riddles.text_stats(text, lang),
        "shell": list(vocab.shell_window(lang, level)),
        "level": level,
    })


@app.route("/api/suggest", methods=["POST"])
def suggest():
    """Easier alternatives near a word the author used, for manual repair."""
    b = body()
    lang = b.get("lang", "en")
    level = int(b.get("level", 2000))
    lex = corpus.get(lang)
    lemma = b.get("lemma", "").lower()
    rank = lex.rank_of(lemma)
    return jsonify({
        "lemma": lemma, "rank": rank,
        "inside_level": rank is not None and rank <= level,
        "band": lex.band_of(lemma),
    })


@app.route("/api/compose", methods=["POST"])
def compose():
    """Save a hand-written riddle.

    Authored text still passes the verifier — the point is not to bypass the
    rules but to let a person write inside them. It lands as a candidate so it
    goes through probes and review like anything else.
    """
    b = body()
    lang = b.get("lang", "en")
    domain = b.get("domain", "movies")
    level = int(b.get("level", 2000))
    text = (b.get("text") or "").strip()
    subject_id = b.get("subject_id")
    if not text or not subject_id:
        return jsonify({"error": "text and subject are required"}), 400

    subject = store.one("SELECT * FROM subject WHERE id=?", (subject_id,))
    if not subject:
        return jsonify({"error": "unknown subject"}), 404

    known = vocab.build_known_set(lang, level)
    report = vocab.analyse(text, lang, known)
    ok, reasons, _ = vocab.verdict(report, lang, level)
    if not ok and not b.get("force"):
        return jsonify({"error": "; ".join(reasons), "reasons": reasons}), 400

    options = b.get("options") or ([subject["title"]] + store.distractors_for(subject, 3))
    rid = store.save_riddle({
        "lang": lang, "domain": domain, "level": level,
        "subject_id": subject_id, "answer": subject["title"],
        "options": options, "emoji": b.get("emoji") or "\U0001F3AC",
        "text": text, "ceiling_rank": report["ceiling_rank"],
        "new": report["overflow"], "new_ranks": report["overflow_ranks"],
        "lemmas": sorted({t["lemma"] for t in report["tokens"] if t["kind"] == vocab.KNOWN}),
        "names": report["names"], "status": "candidate",
        "model": "hand-written", "drafts": 1, "origin": "authored",
        "stats": riddles.text_stats(text, lang),
    })
    return jsonify({"ok": True, "id": rid, "new": report["overflow"],
                    "ceiling_rank": report["ceiling_rank"]})


@app.route("/api/riddle/<rid>")
def riddle_detail(rid):
    r = store.get_riddle(rid)
    return (jsonify(r), 200) if r else (jsonify({"error": "not found"}), 404)


@app.route("/api/riddle/<rid>/text", methods=["POST"])
def edit_text(rid):
    """Rewrite an existing riddle by hand, re-deriving its index values."""
    b = body()
    r = store.get_riddle(rid)
    if not r:
        return jsonify({"error": "not found"}), 404
    text = (b.get("text") or "").strip()
    known = vocab.build_known_set(r["lang"], r["level"])
    report = vocab.analyse(text, r["lang"], known)
    store.conn().execute(
        "UPDATE riddle SET text=?, ceiling_rank=?, new_json=?, lemmas_json=?, "
        "names_json=?, probe_json=NULL, origin='authored' WHERE id=?",
        (text, report["ceiling_rank"], json.dumps(report["overflow"]),
         json.dumps(sorted({t["lemma"] for t in report["tokens"]
                            if t["kind"] == vocab.KNOWN})),
         json.dumps(report["names"]), rid))
    store.conn().execute("DELETE FROM riddle_new WHERE riddle_id=?", (rid,))
    store.conn().executemany(
        "INSERT INTO riddle_new (riddle_id, lang, lemma, rank) VALUES (?,?,?,?)",
        [(rid, r["lang"], w, report["overflow_ranks"].get(w)) for w in report["overflow"]])
    store.conn().commit()
    return jsonify({"ok": True, "new": report["overflow"],
                    "ceiling_rank": report["ceiling_rank"]})


@app.route("/api/subjects/new", methods=["POST"])
def subject_new():
    b = body()
    title = (b.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    sid = store.add_subject(
        b.get("domain", "movies"), title, year=b.get("year"),
        recognizability=int(b.get("recognizability", 3)),
        retellability=int(b.get("retellability", 3)),
        min_frontier=int(b.get("min_frontier", 800)),
        concreteness=int(b.get("concreteness", 3)),
        distractor_group=b.get("distractor_group") or "misc")
    return jsonify({"ok": True, "id": sid})


# --------------------------------------------------------------- telemetry

@app.route("/api/telemetry")
def telemetry():
    lang = request.args.get("lang") or None
    return jsonify({
        "by_model": store.yield_by_model(lang),
        "failures": store.failure_reasons(),
        "attempts": store.rows(
            "SELECT lang, domain, level, COUNT(*) n, "
            "SUM(CASE WHEN outcome='accepted' THEN 1 ELSE 0 END) ok, AVG(drafts) d "
            "FROM attempt GROUP BY lang, domain, level ORDER BY n DESC LIMIT 20"),
        "probe_rates": store.rows(
            "SELECT kind, COUNT(*) n, SUM(correct) ok FROM probe GROUP BY kind"),
    })


# ---------------------------------------------------------------- subjects

@app.route("/api/subjects")
def subject_list():
    domain = request.args.get("domain", "movies")
    items = store.subjects(domain=domain, status=None)
    used = {r["answer"]: r["n"] for r in store.rows(
        "SELECT answer, COUNT(*) n FROM riddle GROUP BY answer")}
    for s in items:
        s["riddles"] = used.get(s["title"], 0)
    return jsonify({"items": items})


@app.route("/api/subjects/<int:sid>", methods=["POST"])
def subject_update(sid):
    store.update_subject(sid, **body())
    return jsonify({"ok": True})


@app.route("/api/subjects/seed", methods=["POST"])
def subject_seed():
    return jsonify({"added": subjects.seed(force=body().get("force", False))})


store.init()
subjects.seed()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5100, debug=False, use_reloader=False)
