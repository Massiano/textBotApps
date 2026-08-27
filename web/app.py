"""The learner-facing app.

Thin by design: routing and session identity only. Every decision about what to
show and what to believe lives in learner/policy.py, so the batch worker and the
request handler share one implementation rather than drifting apart.
"""

import random
import uuid

from flask import Flask, jsonify, request, send_from_directory

import config
from content import store as content_store
from core import corpus, placement, vocab
from learner import policy, store as learner_store
from providers.fake import FakeGenerator
from providers.imagery import Commons
from providers.openrouter import OpenRouter
from providers.wiktionary import Wiktionary

app = Flask(__name__, static_folder=None)
STATIC = config.BASE_DIR / "web" / "static"

GEN = OpenRouter() if config.OPENROUTER_API_KEY else FakeGenerator(seed=21)
DICT = Wiktionary()
IMAGES = Commons()


@app.before_request
def _identify():
    request.learner_id = request.cookies.get(config.COOKIE_NAME)
    request.fresh_uid = None
    if not request.learner_id:
        request.fresh_uid = str(uuid.uuid4())
        request.learner_id = request.fresh_uid
    if request.path.startswith("/api/"):
        learner_store.touch(request.learner_id,
                            request.headers.get("X-Forwarded-For", request.remote_addr),
                            request.headers.get("User-Agent"))


@app.after_request
def _cookie(resp):
    if getattr(request, "fresh_uid", None):
        resp.set_cookie(config.COOKIE_NAME, request.fresh_uid,
                        max_age=config.COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    return resp


def body():
    return request.get_json(silent=True) or {}


def lang_of(value):
    return value if value in config.LANGUAGES else "en"


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


# ---------------------------------------------------------------- bootstrap

@app.route("/api/bootstrap")
def bootstrap():
    return jsonify({
        "languages": [{"code": k, "label": v, "rtl": k in config.RTL_LANGUAGES}
                      for k, v in config.LANGUAGES.items()],
        "domains": [{"id": d, "label": d.title()} for d in config.DOMAIN_IDS],
        "l1": learner_store.get_l1(request.learner_id),
        "live": bool(config.OPENROUTER_API_KEY),
    })


@app.route("/api/l1", methods=["POST"])
def set_l1():
    """First language. Needed to size the cognate control in placement."""
    learner_store.set_l1(request.learner_id, body().get("l1", "en"))
    return jsonify({"ok": True})


@app.route("/api/profile")
def profile():
    lang = lang_of(request.args.get("lang", "en"))
    p = policy.profile_for(request.learner_id, lang)
    known, unknown = policy.known_and_unknown(request.learner_id, lang)
    learning = learner_store.words(request.learner_id, lang, "learning")
    queue = ([{"lemma": w, "met": True} for w in sorted(learning)]
             + [{"lemma": w, "met": False} for w in sorted(unknown)])
    return jsonify({
        "lang": lang, "profile": p,
        "counts": {"known": len(known), "learning": len(learning), "unknown": len(unknown)},
        "queue": queue[:40],
    })


# ----------------------------------------------------------------- placement

@app.route("/api/placement/start", methods=["POST"])
def placement_start():
    lang = lang_of(body().get("lang", "en"))
    items, key = placement.build(lang)
    tid = learner_store.save_test(request.learner_id, lang, "placement", key)
    return jsonify({"test_id": tid, "lang": lang, "items": items})


@app.route("/api/placement/submit", methods=["POST"])
def placement_submit():
    b = body()
    test = learner_store.load_test(b.get("test_id", ""), request.learner_id)
    if not test or test["kind"] != "placement":
        return jsonify({"error": "unknown or expired test"}), 404
    lang = test["lang"]
    rep = placement.score(test["key"], b.get("responses", {}))
    learner_store.save_profile(request.learner_id, lang, rep)
    learner_store.set_words(request.learner_id, lang, rep["known_words"], "known", "placement")
    learner_store.set_words(request.learner_id, lang, rep["unknown_words"], "unknown", "placement")
    return jsonify(rep)


# --------------------------------------------------------------------- play

@app.route("/api/round", methods=["POST"])
def round_():
    b = body()
    lang = lang_of(b.get("lang", "en"))
    domain = b.get("domain", "movies")
    try:
        out = policy.serve(request.learner_id, lang, domain, generator=GEN,
                           lang_name=config.ENGLISH_NAME.get(lang, "English"))
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    if out is None:
        return jsonify({"error": "nothing available for this level yet"}), 503
    return jsonify(out)


@app.route("/api/round/<rid>/answer", methods=["POST"])
def answer(rid):
    out = policy.answer(request.learner_id, rid, body().get("choice", ""))
    if out is None:
        return jsonify({"error": "unknown round"}), 404
    return jsonify(out)


# --------------------------------------------------------------------- word

@app.route("/api/word", methods=["POST"])
def word():
    b = body()
    lang = lang_of(b.get("lang", "en"))
    surface = (b.get("word") or "").strip()
    if not surface:
        return jsonify({"error": "word is required"}), 400

    from core.tokens import lemma_of
    lex = corpus.get(lang)
    lemma = lemma_of(surface, lang)
    info = {"surface": surface, "lemma": lemma, "rank": lex.rank_of(lemma),
            "band": lex.band_of(lemma), "zipf": round(lex.zipf(lemma), 2),
            "inflected": lemma != surface.lower()}

    out = {"lexical": info, "dictionary": None, "images": [], "notes": []}
    out["dictionary"] = DICT.lookup(lemma, lang) or DICT.lookup(surface, lang)
    if out["dictionary"] is None:
        out["notes"].append("No Wiktionary entry found for this form.")
    else:
        gloss = out["dictionary"]["entries"][0]["senses"][0]["gloss"]
        out["images"] = IMAGES.search(" ".join(gloss.split()[:5]))

    learner_store.bump(request.learner_id, lang, [lemma])
    return jsonify(out)


@app.route("/api/word/mark", methods=["POST"])
def mark():
    b = body()
    lang = lang_of(b.get("lang", "en"))
    lemma = (b.get("lemma") or "").strip().lower()
    state = b.get("state", "known")
    if not lemma or state not in ("known", "unknown", "learning", "mastered"):
        return jsonify({"error": "lemma and a valid state are required"}), 400
    learner_store.set_words(request.learner_id, lang, [lemma], state, "manual")
    return jsonify({"ok": True, "lemma": lemma, "state": state})


@app.route("/api/vocabulary")
def vocabulary():
    lang = lang_of(request.args.get("lang", "en"))
    return jsonify({"lang": lang, "words": learner_store.word_rows(request.learner_id, lang)})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "generator": GEN.name,
                    "corpus": content_store.counts(),
                    "languages": len(config.LANGUAGES)})


learner_store.init()
content_store.init()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, use_reloader=False)
