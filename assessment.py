"""Vocabulary placement: build a yes/no test, then turn answers into a number.

Design follows the standard yes/no (checklist) format used in X_Lex and similar
instruments: sample real words from fixed frequency bands, salt the set with
invented words, and correct each band's hit rate by the rate at which the
learner claims to know words that do not exist.

    corrected = (hits - false_alarms) / (1 - false_alarms)

The two outputs that matter downstream are:

    vocab_estimate   headline number, for the learner
    frontier_rank    the rank where corrected knowledge falls through 50%;
                     this is what the text generator is actually constrained by
"""

import random

import config
import lexicon
import pseudowords


def build_placement_test(lang, seed=None):
    """Return (items, answer_key). Items are safe to send to the client."""
    rng = random.Random(seed)
    lex = lexicon.get_lexicon(lang)

    items, key = [], {}
    idx = 0

    for label, lo, hi in config.BANDS:
        pool = [w for w in lex.slice(lo, hi) if len(w) > 2]
        if not pool:
            continue
        chosen = rng.sample(pool, min(config.ITEMS_PER_BAND, len(pool)))
        for w in chosen:
            items.append({"id": idx, "word": w})
            key[idx] = {"word": w, "real": True, "band": label}
            idx += 1

    n_pseudo = max(6, int(len(items) * config.PSEUDO_RATIO / (1 - config.PSEUDO_RATIO)))
    for w in pseudowords.generate(lang, n_pseudo, rng):
        items.append({"id": idx, "word": w})
        key[idx] = {"word": w, "real": False, "band": None}
        idx += 1

    rng.shuffle(items)
    return items, key


def score_placement(key, responses):
    """*responses* maps item id -> bool ("I know this word").

    Returns a report dict; band entries carry raw and corrected rates so the
    result can be shown honestly rather than as a single opaque score.
    """
    responses = {int(k): bool(v) for k, v in responses.items()}

    pseudo = [i for i, m in key.items() if not m["real"]]
    fa_hits = sum(1 for i in pseudo if responses.get(i))
    false_alarm = fa_hits / len(pseudo) if pseudo else 0.0
    # Cap the correction: a learner saying yes to nearly everything gives us no
    # signal, and 1 - false_alarm approaching zero would explode the ratio.
    fa = min(false_alarm, 0.85)

    bands, vocab, frontier_rank = [], 0.0, config.FUNCTION_FLOOR
    prev_corr, prev_hi, found = 1.0, 0, False

    for label, lo, hi in config.BANDS:
        ids = [i for i, m in key.items() if m["real"] and m["band"] == label]
        if not ids:
            continue
        hits = sum(1 for i in ids if responses.get(i))
        raw = hits / len(ids)
        corr = max(0.0, (raw - fa) / (1 - fa)) if fa < 1 else 0.0
        width = hi - lo
        vocab += width * corr
        bands.append({
            "band": label, "range": [lo, hi], "asked": len(ids),
            "known": hits, "raw": round(raw, 3), "corrected": round(corr, 3),
        })

        # Frontier: linear interpolation of the point where corrected knowledge
        # first crosses one half. Only the first crossing counts — once a
        # learner drops below half, a later band scoring well is sampling noise
        # (or a cluster of loanwords) and must not push the ceiling back out.
        if not found and prev_corr >= 0.5 > corr:
            span = prev_corr - corr
            t = (prev_corr - 0.5) / span if span > 0 else 0.0
            frontier_rank = int(prev_hi + t * (hi - prev_hi))
            found = True
        prev_corr, prev_hi = corr, hi

    if not found:
        frontier_rank = config.BANDS[-1][2]

    frontier_rank = max(frontier_rank, config.FUNCTION_FLOOR)

    # Real vocabulary knowledge decays with frequency. If a learner scores well
    # on a rare band after failing a common one, the answers are inconsistent
    # and the headline total is worth more than the frontier says it is.
    order = [b["corrected"] for b in bands]
    inversions = sum(1 for a, b in zip(order, order[1:]) if b > a + 0.25)

    return {
        "consistent": inversions == 0,
        "inversions": inversions,
        "vocab_estimate": int(round(vocab, -2)),
        "frontier_rank": frontier_rank,
        "false_alarm_rate": round(false_alarm, 3),
        "reliable": false_alarm <= 0.30,
        "bands": bands,
        "cefr": cefr_for(vocab),
        "known_words": sorted(key[i]["word"] for i in responses
                              if responses.get(i) and key.get(i, {}).get("real")),
        "unknown_words": sorted(key[i]["word"] for i, m in key.items()
                                if m["real"] and not responses.get(i)),
    }


# Band boundaries follow the commonly cited receptive-vocabulary ranges for
# each CEFR level. They are indicative, not a certification.
_CEFR = [(750, "A1"), (1500, "A2"), (3000, "B1"), (5000, "B2"), (8000, "C1")]


def cefr_for(vocab):
    for limit, label in _CEFR:
        if vocab < limit:
            return label
    return "C2"


def build_interest_check(lang, candidate_words, seed=None):
    """A short yes/no round over domain vocabulary the learner cares about."""
    rng = random.Random(seed)
    lex = lexicon.get_lexicon(lang)
    seen, items = set(), []
    for w in candidate_words:
        lem = lexicon.lemma_of(w, lang)
        if not lem or lem in seen:
            continue
        seen.add(lem)
        items.append({
            "id": len(items),
            "word": lem,
            "rank": lex.rank_of(lem),
            "band": lex.band_of(lem),
        })
    rng.shuffle(items)
    return items
