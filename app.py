"""CineTot — Flask entry point.

Route groups:
    /api/placement   measure the learner's base vocabulary
    /api/interest    measure the vocabulary they actually care about
    /api/round       generate a quiz constrained to that vocabulary, plus three new words
    /api/word        explain any word in the text, at their level
"""

import random
import uuid

from flask import Flask, jsonify, request, send_from_directory

import assessment
import config
import db
import domains
import lexicon
from services import dictionary, generate, images, llm

app = Flask(__name__, static_folder=None)
STATIC = config.BASE_DIR / "static"

DEFAULT_FRONTIER = 1200


# ------------------------------------------------------------------ plumbing

@app.before_request
def _identify():
    request.learner_id = request.cookies.get(config.COOKIE_NAME)
    request.fresh_uid = None
    if not request.learner_id:
        request.fresh_uid = str(uuid.uuid4())
        request.learner_id = request.fresh_uid
    if request.path.startswith("/api/"):
        db.touch_learner(
            request.learner_id,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent"),
        )


@app.after_request
def _set_cookie(response):
    if getattr(request, "fresh_uid", None):
        response.set_cookie(config.COOKIE_NAME, request.fresh_uid,
                            max_age=config.COOKIE_MAX_AGE, httponly=True, samesite="Lax")
    return response


def body():
    return request.get_json(silent=True) or {}


def valid_lang(lang):
    return lang if lang in config.LANGUAGES else "en"


def fail(message, code=400):
    return jsonify({"error": message}), code


def _profile_or_default(learner_id, lang):
    p = db.get_profile(learner_id, lang)
    if p:
        return p
    return {"vocab_estimate": 800, "frontier_rank": DEFAULT_FRONTIER,
            "cefr": "A1", "reliable": False, "bands": [], "provisional": True}


# --------------------------------------------------------------------- pages

@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC, path)


# ----------------------------------------------------------------- bootstrap

@app.route("/api/bootstrap")
def bootstrap():
    return jsonify({
        "languages": [{"code": k, "label": v, "rtl": k in config.RTL_LANGUAGES}
                      for k, v in config.LANGUAGES.items()],
        "domains": domains.listing(),
        "profiles": db.all_profiles(request.learner_id),
        "configured": bool(config.OPENROUTER_API_KEY),
    })


@app.route("/api/profile")
def profile():
    lang = valid_lang(request.args.get("lang", "en"))
    p = _profile_or_default(request.learner_id, lang)
    known = db.words_in_state(request.learner_id, lang, "known", "mastered")
    learning = db.words_in_state(request.learner_id, lang, "learning")
    unknown = db.words_in_state(request.learner_id, lang, "unknown")
    # Queue order mirrors teaching order: words already met in a riddle come
    # first because they are half-learned, then words the learner asked for.
    queue = [{"lemma": w, "met": True} for w in sorted(learning)]
    queue += [{"lemma": w, "met": False} for w in sorted(unknown)]
    return jsonify({
        "lang": lang,
        "profile": p,
        "interests": db.get_interests(request.learner_id, lang),
        "counts": {"known": len(known), "learning": len(learning), "unknown": len(unknown)},
        "queue": queue[:40],
    })


# ----------------------------------------------------------------- placement

@app.route("/api/placement/start", methods=["POST"])
def placement_start():
    lang = valid_lang(body().get("lang", "en"))
    items, key = assessment.build_placement_test(lang)
    test_id = db.save_test(request.learner_id, lang, "placement", key)
    return jsonify({
        "test_id": test_id, "lang": lang, "items": items,
        "instructions": ("Tap every word whose meaning you are sure of. "
                         "Some of these words are invented — tapping those tells us "
                         "how confidently you guess, so leave them alone."),
    })


@app.route("/api/placement/submit", methods=["POST"])
def placement_submit():
    b = body()
    test = db.load_test(b.get("test_id", ""), request.learner_id)
    if not test or test["kind"] != "placement":
        return fail("unknown or expired test", 404)

    lang = test["lang"]
    report = assessment.score_placement(test["key"], b.get("responses", {}))
    db.save_profile(request.learner_id, lang, report)
    db.set_word_states(request.learner_id, lang, report["known_words"], "known", "placement")
    db.set_word_states(request.learner_id, lang, report["unknown_words"], "unknown", "placement")
    # Show which items were invented. The check is worth more as feedback than
    # as a hidden measurement.
    report["reveal"] = {str(i): m["real"] for i, m in test["key"].items()}
    return jsonify(report)


# ------------------------------------------------------------------ interest

@app.route("/api/interest/start", methods=["POST"])
def interest_start():
    b = body()
    lang = valid_lang(b.get("lang", "en"))
    topics = [t.strip() for t in (b.get("topics") or []) if t.strip()][:8]
    if not topics:
        return fail("give at least one topic you are interested in")

    db.set_interests(request.learner_id, lang, topics)
    try:
        words = generate.propose_interest_vocabulary(lang, config.ENGLISH_NAME[lang], topics)
    except Exception as e:
        return fail(f"could not build the topic word list: {e}", 502)

    items = assessment.build_interest_check(lang, words)
    key = {it["id"]: {"word": it["word"], "real": True, "band": it["band"]} for it in items}
    test_id = db.save_test(request.learner_id, lang, "interest", key)
    return jsonify({"test_id": test_id, "lang": lang, "topics": topics, "items": items})


@app.route("/api/interest/submit", methods=["POST"])
def interest_submit():
    b = body()
    test = db.load_test(b.get("test_id", ""), request.learner_id)
    if not test or test["kind"] != "interest":
        return fail("unknown or expired test", 404)

    lang = test["lang"]
    responses = {int(k): bool(v) for k, v in (b.get("responses") or {}).items()}
    known = [m["word"] for i, m in test["key"].items() if responses.get(i)]
    wanted = [m["word"] for i, m in test["key"].items() if not responses.get(i)]

    db.set_word_states(request.learner_id, lang, known, "known", "interest")
    db.set_word_states(request.learner_id, lang, wanted, "unknown", "interest")
    return jsonify({"known": len(known), "to_learn": len(wanted), "targets_pool": wanted[:40]})


# --------------------------------------------------------------------- round

@app.route("/api/round", methods=["POST"])
def make_round():
    if not config.OPENROUTER_API_KEY:
        return fail("OPENROUTER_API_KEY is not set on the server", 503)

    b = body()
    lang = valid_lang(b.get("lang", "en"))
    domain_id = b.get("domain", domains.DEFAULT)
    prof = _profile_or_default(request.learner_id, lang)
    frontier = prof["frontier_rank"]

    known_extra = db.words_in_state(request.learner_id, lang, "known", "mastered")
    unknown = db.words_in_state(request.learner_id, lang, "unknown")
    mastered = set(db.words_in_state(request.learner_id, lang, "mastered"))

    # Prefer teaching words the learner said they wanted from their own topics.
    wanted = [w for w in unknown if w not in mastered]
    random.shuffle(wanted)

    targets = lexicon.pick_targets(
        lang, frontier, config.TARGET_WORDS_PER_ROUND,
        prefer=wanted[:40], exclude=set(known_extra) | mastered)

    allowed = lexicon.build_allowed_set(
        lang, frontier, known_extra=known_extra, unknown=unknown, targets=targets)

    try:
        payload = generate.make_round(
            lang, config.ENGLISH_NAME[lang], domain_id, prof, allowed, targets,
            avoid_answers=db.recent_answers(request.learner_id, lang))
    except Exception as e:
        return fail(str(e), 502)

    round_id = db.save_round(request.learner_id, lang, domain_id, payload)
    db.bump_seen(request.learner_id, lang, payload["meta"]["targets_used"])

    return jsonify({
        "round_id": round_id, "lang": lang, "domain": domain_id,
        "text": payload["text"], "emoji": payload["emoji"],
        "options": payload["options"], "tokens": payload["tokens"],
        "targets": payload["targets"], "quality": payload["meta"],
        "rtl": lang in config.RTL_LANGUAGES,
    })


@app.route("/api/round/<round_id>/answer", methods=["POST"])
def answer_round(round_id):
    choice = (body().get("choice") or "").strip()
    r = db.get_round(round_id, request.learner_id)
    if not r:
        return fail("unknown round", 404)
    db.answer_round(round_id, request.learner_id, choice)
    return jsonify({"correct": choice == r["answer"], "answer": r["answer"]})


# ---------------------------------------------------------------------- word

@app.route("/api/word", methods=["POST"])
def word_detail():
    b = body()
    lang = valid_lang(b.get("lang", "en"))
    word = (b.get("word") or "").strip()
    if not word:
        return fail("word is required")

    context = (b.get("context") or "")[:600]
    info = lexicon.describe_lemma(lang, word)
    prof = _profile_or_default(request.learner_id, lang)
    lex = lexicon.get_lexicon(lang)
    sample = lex.lemmas[: min(prof["frontier_rank"], 400)]
    sample = random.sample(sample, min(60, len(sample)))

    out = {"lexical": info, "dictionary": None, "explanation": None,
           "images": [], "notes": []}

    entry = dictionary.lookup(info["lemma"], lang) or dictionary.lookup(word, lang)
    out["dictionary"] = entry
    if entry is None:
        out["notes"].append("No Wiktionary entry found for this form.")

    if config.OPENROUTER_API_KEY:
        try:
            out["explanation"] = generate.explain_word(
                lang, config.ENGLISH_NAME[lang], word, info["lemma"], context, prof, sample)
        except Exception as e:
            out["notes"].append(f"Explanation unavailable: {e}")
    else:
        out["notes"].append("OPENROUTER_API_KEY is not set, so no generated explanation.")

    query = ""
    if out["explanation"] and out["explanation"].get("imageable"):
        query = out["explanation"].get("image_query") or out["explanation"].get("english", "")
    elif entry:
        first = entry["entries"][0]["senses"][0]["gloss"]
        query = " ".join(first.split()[:5])
    if query:
        out["images"] = images.search(query)
        if not out["images"]:
            out["notes"].append("No freely licensed picture matched this word.")

    db.bump_seen(request.learner_id, lang, [info["lemma"]])
    return jsonify(out)


@app.route("/api/word/mark", methods=["POST"])
def mark_word():
    b = body()
    lang = valid_lang(b.get("lang", "en"))
    lemma = (b.get("lemma") or "").strip().lower()
    state = b.get("state", "known")
    if not lemma or state not in ("known", "unknown", "learning", "mastered"):
        return fail("lemma and a valid state are required")
    db.set_word_states(request.learner_id, lang, [lemma], state, "manual")
    return jsonify({"ok": True, "lemma": lemma, "state": state})


@app.route("/api/vocabulary")
def vocabulary():
    lang = valid_lang(request.args.get("lang", "en"))
    return jsonify({"lang": lang, "words": db.word_rows(request.learner_id, lang)})


# --------------------------------------------------------------------- admin

@app.route(f"/admin/{config.ADMIN_PATH}")
def admin_export():
    return jsonify(db.export_all())


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "languages": len(config.LANGUAGES),
        "key_configured": bool(config.OPENROUTER_API_KEY),
        "models": llm.free_models()[:5] if config.OPENROUTER_API_KEY else [],
    })


db.init()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
