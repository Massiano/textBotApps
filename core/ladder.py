"""The teaching ladder: which lemmas are worth spending a riddle on.

The frequency list is the learner's path — a learner at frontier F needs the
words just past F, and F moves out as they learn. No behavioural model is
needed for that. What is needed is a filter, because the raw list contains
plenty that is near in rank and worthless to teach:

    function words          already given for free
    morphological derivatives   overpay, dissatisfy, quickly
    surviving proper nouns  Schwerin, Porto

Derivatives are the interesting case. A learner who knows *pay* does not need a
riddle for *overpay*; they need the affix explained once. Detection is by
stripping known affixes and checking whether the result is a real, more frequent
lemma.

Affix tables are per language and deliberately incomplete — they are a curation
asset, and the dashboard can override any verdict.
"""

import config
from core import corpus

# (prefixes, suffixes) per language. Only the productive, meaning-preserving
# ones: an affix that changes meaning unpredictably is worth teaching.
AFFIXES = {
    "en": (["un", "dis", "over", "under", "re", "non", "mis", "pre", "anti"],
           ["ly", "ness", "less", "ful", "er", "est", "ish", "able", "ible",
            "ment", "ity", "ise", "ize", "ation", "ing", "ed", "s"]),
    "de": (["un", "vor", "nach", "über", "unter", "ver", "be", "ent", "miss"],
           ["ung", "heit", "keit", "lich", "los", "bar", "isch", "chen", "lein",
            "er", "in", "schaft"]),
    "es": (["des", "in", "re", "sobre", "anti", "pre"],
           ["mente", "ción", "dad", "oso", "osa", "able", "ible", "ito", "ita",
            "ero", "era", "ismo", "ista"]),
    "fr": (["dé", "in", "re", "sur", "sous", "anti", "pré"],
           ["ment", "tion", "té", "eux", "euse", "able", "ible", "iste", "isme",
            "eur", "euse"]),
    "it": (["dis", "in", "ri", "sopra", "sotto", "anti", "pre"],
           ["mente", "zione", "tà", "oso", "osa", "abile", "ibile", "ista",
            "ismo", "ore"]),
    "nl": (["on", "ver", "over", "onder", "her", "wan"],
           ["lijk", "heid", "loos", "baar", "isch", "je", "er", "ing"]),
    "pt": (["des", "in", "re", "sobre", "anti", "pré"],
           ["mente", "ção", "dade", "oso", "osa", "ável", "ível", "ismo", "ista"]),
}

MIN_LENGTH = 3
DERIVATIVE_GAP = 0.6   # base must be at least this much more frequent (rank ratio)


def derivative_of(lemma, lang):
    """The more frequent base this lemma is built from, or None."""
    lex = corpus.get(lang)
    prefixes, suffixes = AFFIXES.get(lang, ([], []))
    own = lex.rank_of(lemma)
    if own is None:
        return None

    candidates = []
    for p in prefixes:
        if lemma.startswith(p) and len(lemma) - len(p) >= MIN_LENGTH:
            candidates.append(lemma[len(p):])
    for s in suffixes:
        if lemma.endswith(s) and len(lemma) - len(s) >= MIN_LENGTH:
            stem = lemma[: -len(s)]
            candidates += [stem, stem + "e"]
            if stem.endswith("i"):          # happiness -> happy, easily -> easy
                candidates.append(stem[:-1] + "y")

    best = None
    for c in candidates:
        r = lex.rank_of(c)
        if r is None or r >= own:
            continue
        if r < own * DERIVATIVE_GAP:
            if best is None or r < lex.rank_of(best):
                best = c
    return best


def assess(lemma, lang):
    """Why a lemma is or is not worth teaching."""
    lex = corpus.get(lang)
    rank = lex.rank_of(lemma)
    reasons = []

    if rank is None:
        reasons.append("outside the frequency list")
    elif rank < config.FUNCTION_FLOOR:
        reasons.append("function word, assumed known")
    if len(lemma) < MIN_LENGTH:
        reasons.append("too short")

    base = derivative_of(lemma, lang) if rank is not None else None
    if base:
        reasons.append(f"derivative of '{base}'")

    return {"lemma": lemma, "rank": rank, "base": base,
            "teachable": not reasons, "reasons": reasons}


def ladder(lang, lo, hi, overrides=None):
    """Teachable lemmas in a rank range, in teaching order.

    *overrides* maps lemma -> bool and comes from dashboard curation, so a human
    verdict always beats the heuristic.
    """
    overrides = overrides or {}
    lex = corpus.get(lang)
    out = []
    for rank, lemma in enumerate(lex.lemmas[lo:hi], start=lo):
        if lemma in overrides:
            if overrides[lemma]:
                out.append(lemma)
            continue
        if assess(lemma, lang)["teachable"]:
            out.append(lemma)
    return out


def levels(lang):
    """Frontier values to precompute against: the band boundaries."""
    return [hi for _, _, hi in config.BANDS if hi <= config.MAX_RANK]


def level_for(frontier_rank):
    """Quantise a learner's frontier onto the nearest precomputed level."""
    best = config.BANDS[0][2]
    for _, _, hi in config.BANDS:
        if hi <= frontier_rank:
            best = hi
    return best
