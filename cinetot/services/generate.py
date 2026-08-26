"""Turning a vocabulary profile into a playable round.

The interesting part is not the prompt, it is the loop around it. Free models
will not respect a vocabulary ceiling on the first attempt, so every draft is
tokenised, lemmatised and checked against the learner's permitted lemma set.
Words outside it are fed back by name and the model is asked to replace them.
Only if the loop cannot converge do we accept a text, and any survivors are
handed to the front end as extra clickable words rather than silently passed
off as understood.
"""

import random

import config
import domains
import lexicon
from services import llm

ROUND_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "answer": {"type": "string"},
        "distractors": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "emoji": {"type": "string"},
    },
    "required": ["text", "answer", "distractors", "emoji"],
    "additionalProperties": False,
}

WORD_SCHEMA = {
    "type": "object",
    "properties": {
        "english": {"type": "string"},
        "part_of_speech": {"type": "string"},
        "register": {"type": "string"},
        "senses": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {"gloss": {"type": "string"}, "example": {"type": "string"}},
                "required": ["gloss", "example"], "additionalProperties": False,
            },
        },
        "not_this": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "synonyms": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "image_query": {"type": "string"},
        "imageable": {"type": "boolean"},
    },
    "required": ["english", "part_of_speech", "register", "senses", "not_this",
                 "synonyms", "image_query", "imageable"],
    "additionalProperties": False,
}

INTEREST_SCHEMA = {
    "type": "object",
    "properties": {"words": {"type": "array", "items": {"type": "string"}, "minItems": 10, "maxItems": 40}},
    "required": ["words"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- the round

def make_round(lang, lang_name, domain_id, profile, allowed, targets, avoid_answers=()):
    """Generate a clue text that stays inside *allowed* plus *targets*."""
    dom = domains.get(domain_id)
    cefr = profile.get("cefr", "A2")
    vocab = profile.get("vocab_estimate", 1000)

    avoid_line = ""
    if avoid_answers:
        avoid_line = ("\nDo not choose any of these, they were used recently: "
                      + ", ".join(list(avoid_answers)[:20]) + ".")

    system = (
        f"You write graded reading material for someone learning {lang_name}. "
        f"Their receptive vocabulary is roughly {vocab} words, around CEFR {cefr}. "
        "You write only in the target language, at or just below their level, "
        "with short sentences and no idioms. You never exceed their vocabulary "
        "except for words you are explicitly told to teach."
    )

    user = f"""Choose {dom['subject']} that most people worldwide would recognise.{avoid_line}

Write {dom['clue']} in {lang_name}, 60-90 words, as a riddle the learner must solve.
{dom['avoid']} The reader has to work out what it is from the description alone.

You must use each of these words at least once, they are the words being taught:
{', '.join(targets)}

Every other word must be one of the most common words in {lang_name}. If you
reach for a less common word, replace it with a simpler one or explain the idea
using easy words instead. Proper names are allowed only if unavoidable.

Also give three wrong answers: {dom['distractor']}. They must be real and
plausible, never the correct one.

Pick one emoji that fits the subject."""

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    result = model = None
    check = None

    for attempt in range(config.MAX_GENERATION_RETRIES + 1):
        result, model = llm.complete_json(messages, ROUND_SCHEMA, "cinetot_round")
        text = (result.get("text") or "").strip()
        check = lexicon.verify_text(text, lang, allowed, targets)

        problems = []
        if check["violations"]:
            problems.append(
                "These words are above the learner's level: "
                + ", ".join(check["violations"][:25])
                + ". Replace every one of them with a much more common word, or "
                  "rephrase the sentence to avoid the idea."
            )
        if check["targets_missing"]:
            problems.append("You did not use these required words: "
                            + ", ".join(check["targets_missing"]) + ". Include each of them.")

        if not problems or (len(check["violations"]) <= config.LEAK_TOLERANCE
                            and not check["targets_missing"]):
            break
        if attempt == config.MAX_GENERATION_RETRIES:
            break

        messages = messages + [
            {"role": "assistant", "content": result.get("text", "")},
            {"role": "user", "content": " ".join(problems)
             + " Keep the same subject and the same four answer options. "
               "Return the full corrected JSON object."},
        ]

    text = (result.get("text") or "").strip()
    answer = (result.get("answer") or "").strip()
    distractors = [d.strip() for d in (result.get("distractors") or []) if d.strip()][:3]
    options = list(dict.fromkeys([answer] + distractors))
    while len(options) < 4:
        options.append(f"Option {len(options) + 1}")
    random.shuffle(options)

    return {
        "text": text,
        "answer": answer,
        "options": options,
        "emoji": (result.get("emoji") or "🎬")[:4],
        "targets": targets,
        "tokens": check["tokens"],
        "meta": {
            "model": model,
            "violations": check["violations"],
            "targets_used": check["targets_used"],
            "targets_missing": check["targets_missing"],
            "attempts": attempt + 1,
            "controlled": not check["violations"],
        },
    }


# ------------------------------------------------------------- word support

def explain_word(lang, lang_name, word, lemma, context, profile, allowed_sample):
    """Definitions written inside the learner's own vocabulary.

    A dictionary that explains an unknown word with three more unknown words is
    useless to a beginner, so the model is told the ceiling and told to stay
    under it, and to avoid the headword's own root.
    """
    cefr = profile.get("cefr", "A2")
    vocab = profile.get("vocab_estimate", 1000)

    system = (
        f"You are a dictionary for a learner of {lang_name} with about {vocab} "
        f"words, CEFR {cefr}. Explanations are written in {lang_name} using only "
        "words simpler and more common than the word being explained. You never "
        "use the headword or any word sharing its root inside an explanation."
    )

    user = f"""Explain the word "{word}" (dictionary form: "{lemma}") in {lang_name}.
It appeared in this sentence: "{context[:400]}"

Give:
- its English translation
- its part of speech
- its register: neutral, formal, informal, slang, literary, technical, dated, or vulgar
- one to three senses, each with a short plain explanation in {lang_name} and a
  new short example sentence in {lang_name} using easy words
- one to three short statements in {lang_name} saying what it is NOT, to mark the
  boundary against words learners confuse it with
- up to five synonyms or near-synonyms in {lang_name}, easiest first
- a short English image search phrase that would return a picture showing the
  meaning, and whether the word can be pictured at all

Words you may safely use include: {', '.join(allowed_sample)}."""

    result, model = llm.complete_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        WORD_SCHEMA, "cinetot_word", temperature=0.4)
    result["model"] = model
    return result


def propose_interest_vocabulary(lang, lang_name, topics, count=30):
    """Ask for domain vocabulary a learner with these interests would meet."""
    system = (f"You list vocabulary in {lang_name}. You return dictionary forms only, "
              "lower case, no articles, no explanations.")
    user = (f"A learner of {lang_name} is interested in: {', '.join(topics)}.\n"
            f"List {count} single words in {lang_name} that come up constantly when "
            "talking or reading about those things. Mix everyday words with a few "
            "that are specific to the topic. Nouns, verbs and adjectives, no proper names.")
    result, _ = llm.complete_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        INTEREST_SCHEMA, "cinetot_interest", temperature=0.6)
    return result.get("words", [])
