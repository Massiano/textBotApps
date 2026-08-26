"""Central configuration. Everything tunable lives here."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LEX_CACHE_DIR = DATA_DIR / "lex"
DB_PATH = Path(os.environ.get("CINETOT_DB", DATA_DIR / "cinetot.sqlite3"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:5000")
SITE_NAME = os.environ.get("SITE_NAME", "CineTot")
ADMIN_PATH = os.environ.get("ADMIN_EXPORT_PATH", "changeme-export")

COOKIE_NAME = "cinetot_uid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 2

# --- Languages -------------------------------------------------------------
# Intersection of wordfreq (frequency data) and simplemma (lemmatisation).
# CJK is deliberately absent: neither library covers it without heavy
# tokeniser dependencies (jieba / mecab), which we do not want to ship.
LANGUAGES = {
    "ar": "العربية (Arabic)",
    "bg": "Български (Bulgarian)",
    "ca": "Català (Catalan)",
    "cs": "Čeština (Czech)",
    "da": "Dansk (Danish)",
    "de": "Deutsch (German)",
    "el": "Ελληνικά (Greek)",
    "en": "English",
    "es": "Español (Spanish)",
    "fa": "فارسی (Persian)",
    "fi": "Suomi (Finnish)",
    "fr": "Français (French)",
    "he": "עברית (Hebrew)",
    "hi": "हिन्दी (Hindi)",
    "hu": "Magyar (Hungarian)",
    "id": "Bahasa Indonesia",
    "is": "Íslenska (Icelandic)",
    "it": "Italiano (Italian)",
    "lt": "Lietuvių (Lithuanian)",
    "lv": "Latviešu (Latvian)",
    "mk": "Македонски (Macedonian)",
    "ms": "Bahasa Melayu (Malay)",
    "nb": "Norsk bokmål",
    "nl": "Nederlands (Dutch)",
    "pl": "Polski (Polish)",
    "pt": "Português (Portuguese)",
    "ro": "Română (Romanian)",
    "ru": "Русский (Russian)",
    "sk": "Slovenčina (Slovak)",
    "sl": "Slovenščina (Slovene)",
    "sv": "Svenska (Swedish)",
    "tr": "Türkçe (Turkish)",
    "uk": "Українська (Ukrainian)",
}

ENGLISH_NAME = {k: (v.split("(")[-1].rstrip(")") if "(" in v else v) for k, v in LANGUAGES.items()}

RTL_LANGUAGES = {"ar", "fa", "he"}

# --- Vocabulary bands ------------------------------------------------------
# Rank ranges over the frequency-ordered lemma list. Classic "1K/2K/..." design.
# (label, start_rank, end_rank)
BANDS = [
    ("1K", 0, 1000),
    ("2K", 1000, 2000),
    ("3K", 2000, 3000),
    ("5K", 3000, 5000),
    ("8K", 5000, 8000),
    ("12K", 8000, 12000),
    ("20K", 12000, 20000),
    ("30K", 20000, 30000),
]

MAX_RANK = 40000          # depth of the lemma list we build per language
ITEMS_PER_BAND = 6        # real words shown per band in the placement test
PSEUDO_RATIO = 0.30       # share of the test made up of invented words
FUNCTION_FLOOR = 250      # top-N lemmas always treated as available

# --- Generation ------------------------------------------------------------
TARGET_WORDS_PER_ROUND = 3       # the "+3" of i+1
MAX_GENERATION_RETRIES = 2       # vocabulary-violation feedback loop
LEAK_TOLERANCE = 3               # accept a round with at most this many strays

PREFERRED_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-nemo:free",
]

HTTP_TIMEOUT = 45
