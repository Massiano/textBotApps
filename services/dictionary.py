"""Wiktionary as the authoritative sense inventory.

The LLM writes explanations a learner can read; Wiktionary supplies the things
a model should not be trusted to invent: how many distinct senses a word has,
which part of speech each belongs to, and the usage labels that tell you
whether a word is neutral, vulgar, archaic or regional.

The REST definition endpoint on en.wiktionary returns every language section
for a headword at once, keyed by language code, so one request covers all 33
languages the app supports.
"""

import html
import re
import threading
import time

import requests

import config

REST = "https://en.wiktionary.org/api/rest_v1/page/definition/{}"
UA = f"CineTot/1.0 ({config.SITE_URL})"

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 60 * 60 * 24

_TAG = re.compile(r"<[^>]+>")
_LABEL = re.compile(r"^\s*\(([^)]{1,80})\)\s*")

# Usage labels that carry register information, as opposed to topic labels.
REGISTER_HINTS = {
    "informal", "colloquial", "slang", "vulgar", "offensive", "derogatory",
    "formal", "literary", "poetic", "archaic", "obsolete", "dated", "rare",
    "humorous", "euphemistic", "childish", "familiar", "technical", "jargon",
    "regional", "dialectal", "proscribed", "nonstandard",
}


def _clean(fragment):
    return html.unescape(_TAG.sub("", fragment or "")).strip()


def lookup(word, lang):
    """Sense list for *word* in language *lang*, or None when unavailable."""
    ck = (word.lower(), lang)
    with _cache_lock:
        hit = _cache.get(ck)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]

    try:
        r = requests.get(REST.format(requests.utils.quote(word, safe="")),
                         headers={"User-Agent": UA, "Accept": "application/json"},
                         timeout=12)
        if r.status_code == 404:
            data = None
        else:
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None

    result = _parse(data, lang, word) if data else None
    with _cache_lock:
        _cache[ck] = (time.time(), result)
    return result


def _parse(data, lang, word):
    sections = data.get(lang) or []
    if not sections:
        return None

    out, registers = [], set()
    for sec in sections[:4]:
        pos = sec.get("partOfSpeech") or "?"
        senses = []
        for d in (sec.get("definitions") or [])[:5]:
            text = _clean(d.get("definition"))
            if not text:
                continue
            labels = []
            m = _LABEL.match(text)
            if m:
                for part in re.split(r"[,;]| or ", m.group(1)):
                    part = part.strip().lower()
                    if part:
                        labels.append(part)
                        if part in REGISTER_HINTS:
                            registers.add(part)
                text = text[m.end():].strip()
            examples = [_clean(e.get("example")) for e in (d.get("parsedExamples") or [])[:2]]
            senses.append({
                "gloss": text,
                "labels": labels,
                "examples": [e for e in examples if e],
            })
        if senses:
            out.append({"pos": pos, "senses": senses})

    if not out:
        return None
    return {
        "source": "en.wiktionary.org",
        "url": f"https://en.wiktionary.org/wiki/{requests.utils.quote(word)}#{_anchor(lang)}",
        "entries": out,
        "registers": sorted(registers),
    }


_ANCHORS = {
    "ar": "Arabic", "bg": "Bulgarian", "ca": "Catalan", "cs": "Czech", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish", "fa": "Persian",
    "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "lt": "Lithuanian",
    "lv": "Latvian", "mk": "Macedonian", "ms": "Malay", "nb": "Norwegian_Bokmål",
    "nl": "Dutch", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sl": "Slovene", "sv": "Swedish",
    "tr": "Turkish", "uk": "Ukrainian",
}


def _anchor(lang):
    return _ANCHORS.get(lang, "English")
