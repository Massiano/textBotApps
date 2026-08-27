"""Known sets, and what a text costs a learner who holds one.

The central operation is `analyse`: given a text and a known set, report which
words fall outside it. Those words are the new words. Nothing is forced in
advance — the constraint is on the *count* and the *distance*, not the identity.

Two derived numbers are what make offline precomputation exact:

    ceiling_rank    highest rank among the words a reader must already know
    overflow        the lemmas a reader will meet for the first time

A stored riddle is servable to any learner whose frontier clears its
ceiling_rank. That is an integer comparison, and the same code computes it
offline and online.
"""

import config
from core import corpus
from core.tokens import lemma_of, tokenize

KNOWN = "known"
NEW = "new"
NAME = "name"


def build_known_set(lang, frontier_rank, extra=(), minus=()):
    """The lemmas a learner is taken to hold."""
    lex = corpus.get(lang)
    known = lex.top(frontier_rank)
    known |= {w.lower() for w in extra}
    known -= {w.lower() for w in minus}
    known |= set(lex.lemmas[: config.FUNCTION_FLOOR])   # function words always
    return known


def analyse(text, lang, known):
    """Classify every token, and report the overflow.

    Proper nouns are exempt. A capitalised word the corpus has never seen is a
    name (Hogwarts, Skywalker) and costs a reader nothing in vocabulary terms.
    Position in the sentence is not a reliable signal, so absence from the
    lexicon is used instead.
    """
    lex = corpus.get(lang)
    tokens, overflow, names = [], {}, set()
    ceiling = 0

    for surface, start, end in tokenize(text):
        lem = lemma_of(surface, lang)
        low = surface.lower()
        rank = lex.rank_of(lem)
        if rank is None:
            rank = lex.rank_of(low)

        if lem in known or low in known:
            kind = KNOWN
            if rank is not None:
                ceiling = max(ceiling, rank)
        elif surface[:1].isupper() and rank is None:
            kind = NAME
            names.add(surface)
        else:
            kind = NEW
            overflow.setdefault(lem, rank)

        tokens.append({"w": surface, "s": start, "e": end,
                       "lemma": lem, "kind": kind, "rank": rank})

    return {
        "tokens": tokens,
        "overflow": sorted(overflow),
        "overflow_ranks": overflow,
        "names": sorted(names),
        "ceiling_rank": ceiling,
        "n_new": len(overflow),
    }


def verdict(report, lang, frontier_rank, n_min=None, n_max=None):
    """Judge an analysed text against the KNOWN + N rule.

    Returns (ok, reasons, repair) where *repair* names the specific words to
    act on, so feedback to a generator can be small and targeted rather than a
    demand for a wholesale rewrite.
    """
    n_min = config.MIN_NEW_WORDS if n_min is None else n_min
    n_max = config.MAX_NEW_WORDS if n_max is None else n_max

    lex = corpus.get(lang)
    too_far = [w for w, r in report["overflow_ranks"].items()
               if r is None or r > frontier_rank * config.SHELL_FACTOR + config.SHELL_MARGIN]
    within = [w for w in report["overflow"] if w not in too_far]

    reasons, repair = [], {}

    if too_far:
        reasons.append("some new words are far beyond the learner")
        repair["simplify"] = sorted(too_far)

    n = len(report["overflow"])
    if n > n_max:
        reasons.append(f"{n} new words, at most {n_max} allowed")
        # Ask for the furthest ones to go first; they are the least teachable.
        ordered = sorted(within, key=lambda w: -(report["overflow_ranks"][w] or 0))
        repair["remove"] = sorted(too_far) or ordered[: n - n_max]
    elif n < n_min:
        reasons.append("no new words, nothing is being taught")
        repair["enrich"] = True

    return (not reasons), reasons, repair


def shell_window(lang, frontier_rank):
    """The rank range that counts as the next shell out."""
    hi = int(frontier_rank * config.SHELL_FACTOR) + config.SHELL_MARGIN
    return frontier_rank, min(hi, len(corpus.get(lang).lemmas))


def servable(riddle_ceiling, riddle_lemmas, frontier_rank, unknown_set):
    """Can a stored riddle be shown to this learner?

    The ceiling check covers the frequency-based part of the known set. The
    intersection covers the handful of words this particular learner marked
    unknown despite them being common.
    """
    if riddle_ceiling > frontier_rank:
        return False
    return not (unknown_set & set(riddle_lemmas))
