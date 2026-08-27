"""Cognate detection, for controlling the placement test.

The yes/no instrument measures recognition of *form*. It cannot tell "I know
this word" from "I can decode this word", and Latinate or international
vocabulary is transparent to anyone with English or a Romance language. That
transparency concentrates in exactly the advanced bands the test uses to place
people high, so the error is one-directional: cognates only ever inflate.

The fix is to treat them as a control item type, in the same way pseudowords
control for yes-saying. Tag items, sample both kinds in known proportion, and
score them separately. The gap between the two hit rates then *measures* the
inflation instead of hiding it.

Detection is fully offline. We already hold an English lemma list, so a word is
a probable cognate if it is orthographically close to some English lemma, or if
it carries an international affix. This is a crude binary sort, not a
philological claim, and it only has to be good enough to fill two bins.
"""

import unicodedata
from functools import lru_cache

from core import corpus

# Endings that mark international Graeco-Latin vocabulary across many
# languages. A word carrying one is decodable by most European learners.
INTERNATIONAL_SUFFIXES = [
    "tion", "sion", "ción", "zione", "ção", "tion", "cja", "ция", "ция",
    "ity", "ität", "idad", "ité", "ità", "idade",
    "ism", "ismo", "ismus", "isme", "izm",
    "ist", "ista", "iste",
    "ic", "ico", "ique", "isch", "ico",
    "al", "ale", "ell",
    "ive", "ivo", "iv",
    "ology", "ologie", "ología", "ologia",
    "graphy", "grafie", "grafía", "grafia",
]

CLOSE = 0.78        # normalised similarity at or above this counts as a cognate
MIN_LENGTH = 5      # short words collide by chance
ENGLISH_CEILING = 6000
# A resemblance only helps if the learner knows the English word it resembles.
# "mesa" and "travail" are real English lemmas, but rare ones, so matching them
# tells us nothing about how transparent the Spanish or French word is.


def _fold(s):
    """Strip diacritics and case so 'información' and 'information' compare."""
    n = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")


def _similarity(a, b):
    """1 - normalised Levenshtein distance."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
        prev = cur
    return 1.0 - prev[lb] / max(la, lb)


_index = {}


def _english_index():
    """English lemmas bucketed by first folded letter and length, so each
    lookup compares against a few hundred words rather than forty thousand."""
    if _index:
        return _index
    for rank, w in enumerate(corpus.get("en").lemmas):
        if rank >= ENGLISH_CEILING:
            break
        f = _fold(w)
        if len(f) < MIN_LENGTH:
            continue
        _index.setdefault((f[0], len(f) // 3), []).append((f, w))
    return _index


@lru_cache(maxsize=100_000)
def evidence(word, lang):
    """(score, english_match, basis) — auditable, because this is a screen and
    a human should be able to overrule it from the dashboard."""
    if lang == "en":
        return 1.0, word, "same language"
    f = _fold(word)
    if len(f) < MIN_LENGTH:
        return 0.0, None, "too short"

    best, match = 0.0, None
    idx = _english_index()
    b = len(f) // 3
    for bucket in (b - 1, b, b + 1):
        for cand, original in idx.get((f[0], bucket), ()):
            if abs(len(cand) - len(f)) > 3:
                continue
            sim = _similarity(f, cand)
            if sim > best:
                best, match = sim, original
                if best >= 0.95:
                    return best, match, "orthographic"

    for suf in INTERNATIONAL_SUFFIXES:
        if f.endswith(_fold(suf)) and len(f) > len(suf) + 2:
            if best < CLOSE:
                return CLOSE, None, f"international ending -{suf}"
            break
    return best, match, "orthographic" if match else "none"


def score(word, lang):
    return evidence(word, lang)[0]


def is_cognate(word, lang, threshold=CLOSE):
    return evidence(word, lang)[0] >= threshold


def split(words, lang):
    """Partition a word list into (transparent, opaque)."""
    clear, opaque = [], []
    for w in words:
        (clear if is_cognate(w, lang) else opaque).append(w)
    return clear, opaque
