"""Vocabulary placement.

Two control item types, measuring two different ways of being wrong:

    pseudowords   catch yes-saying          -> false alarm rate
    cognates      catch decode-without-know -> transparency gap

The frontier is computed from *opaque* words only. Cognate performance is
reported separately rather than folded in, because a learner who scores far
better on transparent words is not more advanced, only more European.

The estimate is deliberately biased low. Cognate error is one-directional and
comprehension failure costs far more than a few easy rounds, so where the
evidence is ambiguous this lands on the cautious side and lets play push the
frontier out.
"""

import random

import config
from core import cognates, corpus, pseudo

PLAIN, COGNATE, PSEUDO = "plain", "cognate", "pseudo"


def build(lang, seed=None, per_band=None):
    """Return (items, key). Items carry nothing that reveals their type."""
    rng = random.Random(seed)
    lex = corpus.get(lang)
    per_band = per_band or config.ITEMS_PER_BAND

    items, key = [], {}

    def add(word, kind, band):
        items.append({"id": len(items), "word": word})
        key[len(key)] = {"word": word, "kind": kind, "band": band}

    # The cognate axis is measured against English. When English *is* the
    # target there is no transparency to control for, so every item is plain.
    split_cognates = lang != "en"

    for label, lo, hi in config.BANDS:
        pool = [w for w in lex.slice(lo, hi) if len(w) > 2]
        if not pool:
            continue
        rng.shuffle(pool)

        if not split_cognates:
            for w in pool[:per_band]:
                add(w, PLAIN, label)
            continue

        clear, opaque = [], []
        for w in pool:
            (clear if cognates.is_cognate(w, lang) else opaque).append(w)
            if len(opaque) >= per_band and len(clear) >= per_band // 2:
                break
        # Opaque words carry the measurement; a couple of transparent ones per
        # band are enough to size the gap.
        for w in opaque[:per_band]:
            add(w, PLAIN, label)
        for w in clear[: max(1, per_band // 3)]:
            add(w, COGNATE, label)

    n_real = len(items)
    n_pseudo = max(6, int(n_real * config.PSEUDO_RATIO / (1 - config.PSEUDO_RATIO)))
    for w in pseudo.generate(lang, n_pseudo, rng):
        add(w, PSEUDO, None)

    rng.shuffle(items)
    return items, key


def score(key, responses):
    responses = {int(k): bool(v) for k, v in responses.items()}

    def rate(kind, band=None):
        ids = [i for i, m in key.items()
               if m["kind"] == kind and (band is None or m["band"] == band)]
        if not ids:
            return None, 0
        return sum(1 for i in ids if responses.get(i)) / len(ids), len(ids)

    false_alarm, n_pseudo = rate(PSEUDO)
    false_alarm = false_alarm or 0.0
    fa = min(false_alarm, 0.85)     # beyond this there is no signal left to correct

    h_cognate, _ = rate(COGNATE)
    h_plain, _ = rate(PLAIN)

    bands, vocab, frontier, found = [], 0.0, config.FUNCTION_FLOOR, False
    prev_corr, prev_hi = 1.0, 0

    for label, lo, hi in config.BANDS:
        raw, n = rate(PLAIN, label)
        if raw is None:
            continue
        corr = max(0.0, (raw - fa) / (1 - fa)) if fa < 1 else 0.0
        vocab += (hi - lo) * corr
        cog_raw, _ = rate(COGNATE, label)
        bands.append({"band": label, "range": [lo, hi], "asked": n,
                      "raw": round(raw, 3), "corrected": round(corr, 3),
                      "cognate_raw": None if cog_raw is None else round(cog_raw, 3)})

        # Only the first crossing counts. Once knowledge falls below half, a
        # later band scoring well is sampling noise and must not push the
        # ceiling back out.
        if not found and prev_corr >= 0.5 > corr:
            span = prev_corr - corr
            t = (prev_corr - 0.5) / span if span > 0 else 0.0
            frontier = int(prev_hi + t * (hi - prev_hi))
            found = True
        prev_corr, prev_hi = corr, hi

    if not bands:
        # No measurable items: refuse to guess high.
        frontier = config.FUNCTION_FLOOR * 4
    elif not found:
        frontier = config.BANDS[-1][2]

    order = [b["corrected"] for b in bands]
    inversions = sum(1 for a, b in zip(order, order[1:]) if b > a + 0.25)

    gap = None if (h_cognate is None or h_plain is None) else round(h_cognate - h_plain, 3)

    frontier = max(int(frontier * config.FRONTIER_BIAS), config.FUNCTION_FLOOR)

    return {
        "vocab_estimate": int(round(vocab, -2)),
        "frontier_rank": frontier,
        "false_alarm_rate": round(false_alarm, 3),
        "reliable": false_alarm <= 0.30,
        "consistent": inversions == 0,
        "transparency_gap": gap,
        "cognate_inflated": gap is not None and gap >= 0.25,
        "hit_plain": None if h_plain is None else round(h_plain, 3),
        "hit_cognate": None if h_cognate is None else round(h_cognate, 3),
        "bands": bands,
        "cefr": cefr_for(vocab),
        "known_words": sorted(key[i]["word"] for i in responses
                              if responses.get(i) and key.get(i, {}).get("kind") != PSEUDO),
        "unknown_words": sorted(m["word"] for i, m in key.items()
                                if m["kind"] != PSEUDO and not responses.get(i)),
        "reveal": {str(i): m["kind"] for i, m in key.items()},
    }


_CEFR = [(750, "A1"), (1500, "A2"), (3000, "B1"), (5000, "B2"), (8000, "C1")]


def cefr_for(vocab):
    for limit, label in _CEFR:
        if vocab < limit:
            return label
    return "C2"
