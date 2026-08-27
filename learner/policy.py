"""Deciding what to show a learner, and updating what we believe about them.

Two things live here.

**Serving.** Query the corpus first; generate live only on a miss. Every live
generation is written back as a draft, so real play fills the corpus in exactly
the places real learners land — a better prior than any cell-by-cell plan.

**Estimation.** The placement test is a prior, not a verdict. Comprehension
behaviour during play is the evidence that corrects it: words clicked, words
marked unknown, answers got wrong. The frontier is a running estimate.
"""

import random

import config
from content import riddles, store as content_store
from core import corpus, ladder, vocab
from learner import store as learner_store

DEFAULT_FRONTIER = 1000


def profile_for(learner_id, lang):
    p = learner_store.get_profile(learner_id, lang)
    if p:
        return p
    return {"vocab_estimate": 700, "frontier_rank": DEFAULT_FRONTIER, "cefr": "A1",
            "reliable": False, "consistent": True, "bands": [], "provisional": True}


def known_and_unknown(learner_id, lang):
    known = learner_store.words(learner_id, lang, "known", "mastered")
    unknown = learner_store.words(learner_id, lang, "unknown")
    return known, unknown


def serve(learner_id, lang, domain, generator=None, lang_name=None, allow_live=True):
    """Return a round for this learner, from the corpus if possible."""
    prof = profile_for(learner_id, lang)
    frontier = prof["frontier_rank"]
    known, unknown = known_and_unknown(learner_id, lang)
    seen = learner_store.seen_ids(learner_id, lang)

    # Words the learner has met but not mastered are the ones worth repeating.
    wanted = learner_store.words(learner_id, lang, "learning", "unknown")
    random.shuffle(wanted)

    hits = content_store.match(lang, domain, frontier, wanted=wanted[:40],
                              exclude_ids=seen, unknown=set(unknown))
    if not hits:
        hits = content_store.match(lang, domain, frontier, exclude_ids=seen,
                                  unknown=set(unknown))

    if hits:
        riddle = random.choice(hits)
        return _present(learner_id, riddle, lang, known, unknown, "corpus")

    if not allow_live or generator is None:
        return None

    level = ladder.level_for(frontier)
    payload = riddles.generate(generator, lang, lang_name or config.ENGLISH_NAME.get(lang, "English"),
                               domain, level, known_extra=known, unknown=unknown)
    # Written back regardless of acceptance: a rejected draft is still data for
    # the studio, and an accepted one becomes corpus after review.
    rid = riddles.persist(payload, origin="live")
    if payload["accepted_vocab"]:
        content_store.enqueue("probe", lang, domain, level, want=1, note=rid)
    riddle = content_store.get_riddle(rid)
    return _present(learner_id, riddle, lang, known, unknown, "live")


def _present(learner_id, riddle, lang, known, unknown, source):
    """Re-analyse the stored text against *this* learner's actual known set.

    The stored ceiling says the riddle fits the learner's frequency band, but
    the individual words they marked known or unknown shift the picture, so the
    highlighting is computed per learner rather than replayed from storage.
    """
    known_set = vocab.build_known_set(lang, profile_for(learner_id, lang)["frontier_rank"],
                                     extra=known, minus=unknown)
    report = vocab.analyse(riddle["text"], lang, known_set)
    learner_store.mark_seen(learner_id, riddle["id"], lang)
    learner_store.bump(learner_id, lang, report["overflow"])

    options = list(riddle["options"])
    random.shuffle(options)
    return {
        "round_id": riddle["id"],
        "lang": lang, "domain": riddle["domain"],
        "text": riddle["text"], "emoji": riddle["emoji"],
        "options": options,
        "tokens": report["tokens"],
        "new": report["overflow"],
        "source": source,
        "rtl": lang in config.RTL_LANGUAGES,
    }


def answer(learner_id, riddle_id, choice):
    riddle = content_store.get_riddle(riddle_id)
    if not riddle:
        return None
    correct = choice.strip() == riddle["answer"]
    learner_store.record_answer(learner_id, riddle_id, choice, correct)
    reestimate(learner_id, riddle["lang"])
    return {"correct": correct, "answer": riddle["answer"]}


# --------------------------------------------------------------- estimation

def reestimate(learner_id, lang):
    """Nudge the frontier from play evidence.

    Deliberately asymmetric and slow. Over-estimating a learner produces texts
    they cannot read, which is what makes people stop; under-estimating costs a
    few easy rounds. So the frontier retreats faster than it advances.
    """
    prof = learner_store.get_profile(learner_id, lang)
    if not prof:
        return None
    results = learner_store.recent_results(learner_id, lang, limit=10)
    if len(results) < 4:
        return prof["frontier_rank"]

    correct = sum(1 for r in results if r["correct"])
    rate = correct / len(results)
    marked_unknown = len(learner_store.words(learner_id, lang, "unknown"))

    frontier = prof["frontier_rank"]
    lex_size = len(corpus.get(lang).lemmas)

    if rate >= 0.8 and marked_unknown < 15:
        frontier = int(frontier * 1.08)
    elif rate <= 0.4 or marked_unknown > 40:
        frontier = int(frontier * 0.80)

    frontier = max(config.FUNCTION_FLOOR, min(frontier, lex_size))
    if frontier != prof["frontier_rank"]:
        learner_store.set_frontier(learner_id, lang, frontier)
    return frontier
