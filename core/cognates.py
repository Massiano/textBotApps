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

Detection is fully offline and measured against the learner's **first
language**, not against English. Getting this wrong is not a small error: a
German speaker learning English decodes half the Latinate vocabulary in the
upper bands, and if English is treated as the reference there is no control at
all for exactly the learner who most needs one.

A word is transparent if it is orthographically close to a frequent lemma in
the reference language, or carries an international affix. This is a crude
binary sort, not a philological claim, and only has to be good enough to fill
two bins.
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
REF_CEILING = 6000
# A resemblance only helps if the learner actually knows the word it resembles.
# "mesa" and "travail" are real English lemmas, but rare ones, so matching them
# says nothing about how transparent the Spanish or French word is.


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


def _ref_index(ref):
    """Reference-language lemmas bucketed by first folded letter and length, so
    each lookup compares against a few hundred words, not forty thousand."""
    if ref in _index:
        return _index[ref]
    idx = {}
    for rank, w in enumerate(corpus.get(ref).lemmas):
        if rank >= REF_CEILING:
            break
        f = _fold(w)
        if len(f) < MIN_LENGTH:
            continue
        idx.setdefault((f[0], len(f) // 3), []).append((f, w))
    _index[ref] = idx
    return idx


@lru_cache(maxsize=100_000)
def evidence(word, lang, ref="en"):
    """(score, match, basis) — auditable, because this is a screen and a human
    should be able to overrule it from the dashboard.

    *ref* is the learner's first language. Transparency is a relation between
    two languages, so there is no such thing as a cognate in the abstract.
    """
    if not ref or ref == lang:
        return 0.0, None, "no reference language"
    f = _fold(word)
    if len(f) < MIN_LENGTH:
        return 0.0, None, "too short"

    best, match = 0.0, None
    idx = _ref_index(ref)
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


def score(word, lang, ref="en"):
    return evidence(word, lang, ref)[0]


def is_cognate(word, lang, ref="en", threshold=CLOSE):
    return evidence(word, lang, ref)[0] >= threshold


def split(words, lang, ref="en"):
    """Partition a word list into (transparent, opaque)."""
    clear, opaque = [], []
    for w in words:
        (clear if is_cognate(w, lang, ref) else opaque).append(w)
    return clear, opaque
