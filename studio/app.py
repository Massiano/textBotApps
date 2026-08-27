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
from content import jobs, riddles, store, subjects
from core import corpus, ladder
from providers.fake import FakeGenerator
from providers.openrouter import OpenRouter

app = Flask(__name__, static_folder=None)
STATIC = config.BASE_DIR / "studio" / "static"

GEN = OpenRouter() if config.OPENROUTER_API_KEY else FakeGenerator(seed=11)
WORKER = jobs.Worker(GEN)


def body():
    return request.get_json(silent=True) or {}


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
