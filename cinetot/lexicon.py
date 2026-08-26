"""Frequency-ranked lemma lists, lemmatisation and controlled-vocabulary checking.

This module is the part of CineTot that does not involve an LLM. Everything the
app claims about a learner's vocabulary is grounded here: which lemma a surface
form belongs to, how frequent that lemma is, and whether a generated text stays
inside a permitted word set.

Built on:
  wordfreq   - corpus frequency for 40+ languages (bundled data, no network)
  simplemma  - rule+dictionary lemmatiser for 50+ languages (bundled data)
"""

import json
import re
import threading
from functools import lru_cache

from wordfreq import top_n_list, zipf_frequency
from simplemma import Lemmatizer
from simplemma.strategies.dictionaries.dictionary_factory import DefaultDictionaryFactory

import config

_lemmatizer = Lemmatizer()
_dict_factory = DefaultDictionaryFactory()
_build_lock = threading.Lock()

# Letters only (any script), optional internal hyphen/apostrophe.
_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*", re.UNICODE)


# ---------------------------------------------------------------- tokenising

def tokenize(text):
    """Return [(surface, start, end)] for every word-like span in *text*."""
    return [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(text)]


@lru_cache(maxsize=200_000)
def lemma_of(surface, lang):
    """Lowercase lemma for a surface form. Falls back to the lowercased word."""
    w = surface.strip()
    if not w:
        return ""
    try:
        return _lemmatizer.lemmatize(w, lang=lang).lower()
    except Exception:
        return w.lower()


# ------------------------------------------------------- per-language corpus

class LanguageLexicon:
    """Frequency-ordered, realness-filtered lemma list for one language.

    Built once and cached to data/lex/<lang>.json so start-up after the first
    request is a plain file read rather than a scan of a million dictionary
    entries.
    """

    def __init__(self, lang, lemmas):
        self.lang = lang
        self.lemmas = lemmas                                   # rank-ordered
        self.rank = {w: i for i, w in enumerate(lemmas)}
        self.lemma_set = set(lemmas)

    # -- lookups ---------------------------------------------------------
    def rank_of(self, lemma):
        """Rank in the frequency list, or None if outside the top MAX_RANK."""
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

    def known_by_rank(self, max_rank):
        """All lemmas at or above a frequency threshold."""
        return set(self.lemmas[: max(max_rank, config.FUNCTION_FLOOR)])

    # -- construction ----------------------------------------------------
    @staticmethod
    def build(lang):
        """Scan the frequency list, keep entries that are real lexemes, and
        collapse inflected forms onto their lemma so ranks describe lexemes
        rather than word forms."""
        real = _realness_pool(lang)
        seen, out = set(), []
        for word in top_n_list(lang, 250_000):
            if not word or not _WORD_RE.fullmatch(word):
                continue
            if word not in real:
                continue
            lem = lemma_of(word, lang)
            if not lem or lem in seen:
                continue
            seen.add(lem)
            out.append(lem)
            if len(out) >= config.MAX_RANK:
                break
        return out


def _realness_pool(lang):
    """Case-folded set of every form simplemma recognises for *lang*.

    Used to strip corpus noise from the frequency list: numerals, fragments,
    foreign proper nouns and OCR debris are all absent from a real lexicon.
    """
    try:
        d = _dict_factory.get_dictionary(lang)
    except Exception:
        return None
    pool = set()
    for k in d.keys():
        pool.add(k.lower() if isinstance(k, str) else k.decode("utf-8").lower())
    for v in d.values():
        pool.add(v.lower() if isinstance(v, str) else v.decode("utf-8").lower())
    return pool


class _AlwaysReal(frozenset):
    def __contains__(self, item):
        return True


_CACHE = {}


def get_lexicon(lang):
    """Load (and if necessary build+cache) the lexicon for *lang*."""
    if lang in _CACHE:
        return _CACHE[lang]
    with _build_lock:
        if lang in _CACHE:
            return _CACHE[lang]
        config.LEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LEX_CACHE_DIR / f"{lang}.json"
        if path.exists():
            lemmas = json.loads(path.read_text(encoding="utf-8"))
        else:
            lemmas = LanguageLexicon.build(lang)
            path.write_text(json.dumps(lemmas, ensure_ascii=False), encoding="utf-8")
        lex = LanguageLexicon(lang, lemmas)
        _CACHE[lang] = lex
        return lex


# --------------------------------------------------- controlled vocabulary

def build_allowed_set(lang, frontier_rank, known_extra=(), unknown=(), targets=()):
    """The exact lemma set a generated text may draw on.

    frontier_rank  learner's estimated knowledge boundary (rank in the list)
    known_extra    lemmas marked known despite falling outside the frontier
    unknown        lemmas explicitly marked unknown inside the frontier
    targets        the i+1 words this round is meant to teach
    """
    lex = get_lexicon(lang)
    allowed = lex.known_by_rank(frontier_rank)
    allowed |= {w.lower() for w in known_extra}
    allowed -= {w.lower() for w in unknown}
    allowed |= set(lex.lemmas[: config.FUNCTION_FLOOR])   # function words always in
    allowed |= {w.lower() for w in targets}
    return allowed


def verify_text(text, lang, allowed, targets=()):
    """Check a generated text against a permitted lemma set.

    Returns a dict with the offending lemmas, which target words actually
    appeared, and a per-token annotation the front end uses for highlighting.
    Proper nouns are exempt: a title or character name is not a vocabulary
    burden in the same way a common noun is, and no lemmatiser handles them.
    """
    lex = get_lexicon(lang)
    targets_l = {t.lower() for t in targets}
    tokens, violations, hit_targets = [], set(), set()

    for surface, start, end in tokenize(text):
        lem = lemma_of(surface, lang)
        low = surface.lower()
        in_allowed = lem in allowed or low in allowed
        is_target = lem in targets_l or low in targets_l
        # A capitalised word the corpus has never seen is a name (Vader,
        # Hogwarts, Skywalker), not a vocabulary item. Position in the
        # sentence is unreliable, so we key on absence from the lexicon.
        proper = surface[:1].isupper() and lex.rank_of(lem) is None and lex.rank_of(low) is None

        if is_target:
            hit_targets.add(lem if lem in targets_l else low)
            kind = "target"
        elif in_allowed:
            kind = "known"
        elif proper:
            kind = "name"
        else:
            kind = "stray"
            violations.add(lem)

        tokens.append({"w": surface, "s": start, "e": end, "lemma": lem, "kind": kind})

    return {
        "tokens": tokens,
        "violations": sorted(violations),
        "targets_used": sorted(hit_targets),
        "targets_missing": sorted(targets_l - hit_targets),
    }


def _sentence_initial(text, idx):
    """True if position *idx* follows sentence-final punctuation."""
    j = idx - 1
    while j >= 0 and text[j] in " \n\t\"'\u201c\u2018(":
        j -= 1
    return j < 0 or text[j] in ".!?:;\u061f\u06d4\u3002"


def pick_targets(lang, frontier_rank, count, prefer=(), exclude=()):
    """Choose i+1 words: just beyond the learner's frontier, favouring lemmas
    from their declared interests."""
    lex = get_lexicon(lang)
    excl = {w.lower() for w in exclude}
    picked = []

    for w in prefer:
        lem = lemma_of(w, lang)
        r = lex.rank_of(lem)
        if lem in excl or lem in picked:
            continue
        if r is None or r >= frontier_rank:
            picked.append(lem)
        if len(picked) >= count:
            return picked[:count]

    # Draw from a window immediately past the frontier, weighted toward its
    # near edge. A word 200 ranks out is the next thing worth learning; one
    # 8000 ranks out is noise the learner has no use for yet.
    lo = frontier_rank
    hi = min(int(frontier_rank * 1.35) + 250, len(lex.lemmas))
    import random
    pool = [w for w in lex.lemmas[lo:hi] if w not in excl and w not in picked]
    if not pool:
        return picked[:count]
    weights = [1.0 / (1 + i / 60) for i in range(len(pool))]
    while len(picked) < count and pool:
        choice = random.choices(pool, weights=weights, k=1)[0]
        i = pool.index(choice)
        pool.pop(i)
        weights.pop(i)
        picked.append(choice)
    return picked[:count]


def describe_lemma(lang, surface):
    """Frequency profile of a single word, for the word-detail panel."""
    lex = get_lexicon(lang)
    lem = lemma_of(surface, lang)
    r = lex.rank_of(lem)
    return {
        "surface": surface,
        "lemma": lem,
        "rank": r,
        "band": lex.band_of(lem),
        "zipf": round(lex.zipf(lem), 2),
        "inflected": lem != surface.lower(),
    }
