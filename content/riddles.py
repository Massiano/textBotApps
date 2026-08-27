"""Generating a riddle under a vocabulary ceiling.

The loop does not choose which words to teach. It constrains the model to the
learner's known vocabulary, lets it write, then measures what spilled over.
The spill *is* the new-word set. A draft is accepted when it overflows by
between one and three lemmas, all inside the next shell.

This matters for text quality: forcing three arbitrary words makes the model
bend a story around vocabulary, and creates a failure mode (required word
missing) that only exists because we invented the requirement. Measuring
instead of dictating removes it, and makes the repair feedback small and
specific — usually one word to replace, not a rewrite.
"""

import random
import time

import config
from content import store, subjects
from core import corpus, vocab

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "emoji": {"type": "string"},
    },
    "required": ["text", "emoji"],
    "additionalProperties": False,
}

DOMAIN_RULES = {
    "movies": ("a film", "Do not name the film, its director, or any character."),
    "books": ("a book", "Do not name the book, its author, or any character."),
    "history": ("a historical event", "Do not name the event, its date, or the people."),
    "people": ("a famous person", "Do not give any name that identifies them."),
    "songs": ("a song", "Do not quote lyrics, name the song, or name the artist."),
    "games": ("a video game", "Do not name the game, its studio, or its hero."),
    "animals": ("an animal", "Do not give the common or scientific name."),
    "inventions": ("an object", "Do not name the object."),
}


def style_card(lang, domain, level):
    """Shape derived from riddles that already passed review.

    Exemplar texts would transmit plots as well as style — the model reuses
    subjects and phrasings it is shown. Numbers cannot be copied, so the corpus
    can lend consistency without lending content.
    """
    accepted = store.rows(
        "SELECT stats_json FROM riddle WHERE lang=? AND domain=? AND status='accepted' "
        "AND ABS(level - ?) < 2000 ORDER BY created_at DESC LIMIT 40",
        (lang, domain, level))
    if len(accepted) < 5:
        return {"sentences": [4, 7], "words_per_sentence": [8, 14]}

    import json
    sent, wps = [], []
    for r in accepted:
        s = json.loads(r["stats_json"] or "{}")
        if s.get("sentences"):
            sent.append(s["sentences"])
        if s.get("words_per_sentence"):
            wps.append(s["words_per_sentence"])
    if not sent:
        return {"sentences": [4, 7], "words_per_sentence": [8, 14]}
    sent.sort(); wps.sort()
    q = lambda a, f: a[int(len(a) * f)] if a else 0
    return {"sentences": [q(sent, 0.2), q(sent, 0.8)],
            "words_per_sentence": [round(q(wps, 0.2)), round(q(wps, 0.8))],
            "from": len(accepted)}


def text_stats(text, lang):
    import re
    sentences = [s for s in re.split(r"[.!?\u061f\u3002]+", text) if s.strip()]
    words = len(vocab.tokenize(text)) if hasattr(vocab, "tokenize") else None
    from core.tokens import tokenize
    words = len(tokenize(text))
    return {"sentences": len(sentences),
            "words": words,
            "words_per_sentence": round(words / max(1, len(sentences)), 1)}


def _prompt(lang_name, level, subject, domain, style, sample, avoid):
    kind, secrecy = DOMAIN_RULES.get(domain, DOMAIN_RULES["movies"])
    lo, hi = style["words_per_sentence"]
    s_lo, s_hi = style["sentences"]

    system = (
        f"You write for someone learning {lang_name} who knows roughly the "
        f"{level} most common words of the language and no more. You write only "
        f"in {lang_name}. You use short plain sentences and no idioms. When you "
        "want a word the reader will not know, you choose a simpler one instead."
    )

    user = f"""Describe {kind} so that a reader can guess which one it is: **{subject['title']}**.

{secrecy} The reader must work it out from what happens.

Write {s_lo} to {s_hi} sentences, about {lo}-{hi} words each, in {lang_name}.
Describe concrete events and things that can be pictured.

Almost every word must be among the {level} most common words in {lang_name}.
One or two unusual words are fine and welcome — they are what the reader
learns. Many unusual words are not.

Common words you may freely use include: {', '.join(sample)}.

Choose one emoji that suits it."""

    if avoid:
        user += "\n\nRecently used, do not describe these: " + ", ".join(list(avoid)[:15]) + "."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate(gen, lang, lang_name, domain, level, subject=None, rng=None,
             known_extra=(), unknown=()):
    """Produce one riddle. Returns a payload dict, accepted or not."""
    rng = rng or random.Random()
    started = time.time()

    avoid = store.subject_usage(lang, domain)
    subject = subject or subjects.pick(domain, level, avoid=avoid, rng=rng)

    known = vocab.build_known_set(lang, level, extra=known_extra, minus=unknown)
    lex = corpus.get(lang)
    sample = rng.sample(lex.lemmas[200:min(level, 1200)], min(45, max(1, min(level, 1200) - 200)))
    style = style_card(lang, domain, level)

    messages = _prompt(lang_name, level, subject, domain, style, sample, avoid)
    report = model = None
    drafts = 0
    reasons = []

    for attempt in range(config.MAX_GENERATION_RETRIES + 1):
        result, model = gen.json(messages, SCHEMA, "cinetot_riddle")
        drafts += 1
        text = (result.get("text") or "").strip()
        report = vocab.analyse(text, lang, known)
        ok, reasons, repair = vocab.verdict(report, lang, level)
        if ok or attempt == config.MAX_GENERATION_RETRIES:
            break
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": _repair_note(repair)},
        ]

    text = (result.get("text") or "").strip()
    ok, reasons, _ = vocab.verdict(report, lang, level)

    distractors = store.distractors_for(subject, 3)
    options = list(dict.fromkeys([subject["title"], *distractors]))
    rng.shuffle(options)

    return {
        "lang": lang, "domain": domain, "level": level,
        "subject_id": subject["id"], "answer": subject["title"],
        "options": options, "emoji": (result.get("emoji") or "\U0001F3AC")[:4],
        "text": text,
        "ceiling_rank": report["ceiling_rank"],
        "new": report["overflow"],
        "new_ranks": report["overflow_ranks"],
        "lemmas": sorted({t["lemma"] for t in report["tokens"] if t["kind"] == vocab.KNOWN}),
        "names": report["names"],
        "tokens": report["tokens"],
        "model": model, "drafts": drafts,
        "status": "candidate" if ok else "rejected",
        "reject_reason": None if ok else "; ".join(reasons),
        "stats": {**text_stats(text, lang), "seconds": round(time.time() - started, 1)},
        "accepted_vocab": ok,
    }


def _repair_note(repair):
    """Feedback naming the specific words to act on."""
    parts = []
    if repair.get("simplify"):
        parts.append("These words are too hard for this reader: "
                     + ", ".join(repair["simplify"])
                     + ". Replace each with a much more common word, or say the "
                       "idea differently using easy words.")
    if repair.get("remove"):
        parts.append("There are too many unfamiliar words. Keep at most "
                     f"{config.MAX_NEW_WORDS}. Start by replacing: "
                     + ", ".join(repair["remove"]) + ".")
    if repair.get("enrich"):
        parts.append("Every word here is already familiar, so the reader learns "
                     "nothing. Use one or two slightly less common words where "
                     "they fit naturally.")
    parts.append("Keep the same subject. Return the full JSON object again.")
    return " ".join(parts)


def persist(payload, origin="batch"):
    """Store a generated riddle and log the attempt for telemetry."""
    payload = dict(payload, origin=origin)
    rid = store.save_riddle(payload)
    store.log_attempt(payload["lang"], payload["domain"], payload["level"],
                      payload["model"], payload["drafts"],
                      "accepted" if payload["accepted_vocab"] else "rejected_vocab",
                      payload.get("reject_reason"),
                      payload["stats"].get("seconds"))
    if not payload["accepted_vocab"]:
        store.set_status(rid, "rejected", payload.get("reject_reason"))
    return rid
