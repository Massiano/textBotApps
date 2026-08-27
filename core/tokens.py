"""Tokenisation and lemmatisation. No I/O beyond simplemma's bundled data."""

import re
from functools import lru_cache

from simplemma import Lemmatizer

_lemmatizer = Lemmatizer()

# Letters in any script, optional internal hyphen or apostrophe.
WORD_RE = re.compile(r"[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*", re.UNICODE)


def tokenize(text):
    """[(surface, start, end)] for every word-like span."""
    return [(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(text)]


@lru_cache(maxsize=200_000)
def lemma_of(surface, lang):
    w = (surface or "").strip()
    if not w:
        return ""
    try:
        return _lemmatizer.lemmatize(w, lang=lang).lower()
    except Exception:
        return w.lower()


def lemmas(text, lang):
    return [lemma_of(s, lang) for s, _, _ in tokenize(text)]
