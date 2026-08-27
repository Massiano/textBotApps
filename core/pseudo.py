"""Invented but phonotactically plausible words, used as control items.

A yes/no vocabulary test where every item is real is trivially gamed by
answering "yes" to everything. Mixing in words that look native but do not
exist lets us measure how freely a learner claims knowledge, and subtract that
tendency from their score.

Generation is a character-level Markov chain trained on the language's own
frequency list, so no per-language phonotactic rules are needed.
"""

import bisect
import random
from collections import defaultdict

from core.tokens import lemma_of

from core import corpus

_ORDER = 3
_MODELS = {}
_SORTED = {}


def _is_fragment(word, lang):
    """True if *word* is the opening of a real word (so a learner would read it
    as a typo rather than an unfamiliar word)."""
    if lang not in _SORTED:
        _SORTED[lang] = sorted(corpus.get(lang).lemma_set)
    arr = _SORTED[lang]
    i = bisect.bisect_left(arr, word)
    for j in (i, i + 1):
        if j < len(arr) and arr[j].startswith(word):
            return True
    # also reject if trimming a letter or two yields a real word
    return any(word[:-k] in corpus.get(lang).lemma_set for k in (1, 2))


def _model(lang):
    if lang in _MODELS:
        return _MODELS[lang]
    lex = corpus.get(lang)
    chain = defaultdict(list)
    lengths = []
    for word in lex.lemmas[:8000]:
        if len(word) < 4:
            continue
        lengths.append(len(word))
        padded = "^" * _ORDER + word + "$"
        for i in range(len(padded) - _ORDER):
            chain[padded[i:i + _ORDER]].append(padded[i + _ORDER])
    _MODELS[lang] = (dict(chain), lengths or [6])
    return _MODELS[lang]


def generate(lang, count, rng=None):
    """Return *count* distinct invented words that are not real in *lang*."""
    rng = rng or random.Random()
    chain, lengths = _model(lang)
    lex = corpus.get(lang)
    lo, hi = min(lengths), max(min(lengths) + 6, 10)

    out, attempts = [], 0
    seen = set()
    while len(out) < count and attempts < count * 200:
        attempts += 1
        state = "^" * _ORDER
        word = ""
        target_len = rng.randint(max(lo, 4), hi)
        while True:
            options = chain.get(state)
            if not options:
                break
            ch = rng.choice(options)
            if ch == "$":
                break
            word += ch
            if len(word) > target_len + 3:
                break
            state = (state + ch)[-_ORDER:]

        if not (5 <= len(word) <= hi + 2):
            continue
        if word in seen or word in lex.lemma_set:
            continue
        if lemma_of(word, lang) in lex.lemma_set:
            continue
        if _is_fragment(word, lang):
            continue
        seen.add(word)
        out.append(word)
    return out
