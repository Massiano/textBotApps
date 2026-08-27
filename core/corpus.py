"""Frequency-ranked lemma lists, one per language.

Built once from wordfreq, filtered against simplemma's dictionary so corpus
noise and foreign proper nouns drop out, then cached to disk as a plain list.
Rank in this list is the unit every other subsystem measures in.
"""

import json
import threading

from wordfreq import top_n_list, zipf_frequency
from simplemma.strategies.dictionaries.dictionary_factory import DefaultDictionaryFactory

import config
from core.tokens import WORD_RE, lemma_of

_factory = DefaultDictionaryFactory()
_lock = threading.Lock()
_cache = {}


class LanguageLexicon:
    def __init__(self, lang, lemmas):
        self.lang = lang
        self.lemmas = lemmas
        self.rank = {w: i for i, w in enumerate(lemmas)}
        self.lemma_set = set(lemmas)

    def rank_of(self, lemma):
        return self.rank.get(lemma)

    def band_of(self, lemma):
        r = self.rank_of(lemma)
        if r is None:
            return "rare"
        for label, lo, hi in config.BANDS:
            if lo <= r < hi:
                return label
        return "rare"

    def zipf(self, lemma):
        return zipf_frequency(lemma, self.lang)

    def slice(self, lo, hi):
        return self.lemmas[lo:hi]

    def top(self, n):
        return set(self.lemmas[: max(n, config.FUNCTION_FLOOR)])


def _realness_pool(lang):
    """Case-folded set of everything simplemma recognises for this language."""
    try:
        d = _factory.get_dictionary(lang)
    except Exception:
        return None
    pool = set()
    for k in d.keys():
        pool.add(k.lower() if isinstance(k, str) else k.decode("utf-8").lower())
    for v in d.values():
        pool.add(v.lower() if isinstance(v, str) else v.decode("utf-8").lower())
    return pool


def build(lang):
    real = _realness_pool(lang)
    seen, out = set(), []
    for word in top_n_list(lang, 250_000):
        if not word or not WORD_RE.fullmatch(word):
            continue
        if real is not None and word not in real:
            continue
        lem = lemma_of(word, lang)
        if not lem or lem in seen:
            continue
        seen.add(lem)
        out.append(lem)
        if len(out) >= config.MAX_RANK:
            break
    return out


def get(lang):
    if lang in _cache:
        return _cache[lang]
    with _lock:
        if lang in _cache:
            return _cache[lang]
        config.LEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LEX_CACHE_DIR / f"{lang}.json"
        if path.exists():
            lemmas = json.loads(path.read_text(encoding="utf-8"))
        else:
            lemmas = build(lang)
            path.write_text(json.dumps(lemmas, ensure_ascii=False), encoding="utf-8")
        _cache[lang] = LanguageLexicon(lang, lemmas)
        return _cache[lang]
