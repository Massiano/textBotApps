"""Run the real app with the network edges stubbed, for looking at the UI.

    python demo_server.py

Everything server-side is genuine — the same lexicon, the same verification
loop, the same routes. Only the three things that reach the internet are
replaced: OpenRouter, Wiktionary and Commons.
"""

import json

import config

config.OPENROUTER_API_KEY = "demo"
config.DB_PATH = config.BASE_DIR / "data" / "demo.sqlite3"

from services import dictionary, images, llm  # noqa: E402

DRAFTS = [
    # First draft deliberately overshoots, to exercise the correction loop.
    {"text": "A young man lives on an arid world with two suns. He discovers a "
             "luminous sword that once belonged to his father, and joins an "
             "inscrutable old hermit. Together with a smuggler and two droids they "
             "attempt to rescue a princess from an immense armoured station, then "
             "destroy it. Vader watches from the dark.",
     "answer": "Star Wars", "distractors": ["Dune", "Flash Gordon", "The Fifth Element"],
     "emoji": "\U0001F680"},
    {"text": "A young man lives on a dry world with two suns. He finds a bright "
             "sword that once belonged to his father, and joins a strange old man. "
             "With a pilot and two robots they try to save a princess from an "
             "enormous grey station, and then they break it. Vader watches from "
             "the dark.",
     "answer": "Star Wars", "distractors": ["Dune", "Flash Gordon", "The Fifth Element"],
     "emoji": "\U0001F680"},
]

WORD_REPLY = {
    "english": "sword",
    "part_of_speech": "noun",
    "register": "neutral",
    "senses": [
        {"gloss": "A long piece of metal with a sharp edge, held in the hand and "
                  "used to fight.",
         "example": "The soldier carried a sword at his side."},
        {"gloss": "In stories, a sign of power that a king or hero carries.",
         "example": "He was given his father's sword."},
    ],
    "not_this": [
        "Not a knife: a knife is short and used for food.",
        "Not a gun: a gun sends something through the air.",
    ],
    "synonyms": ["blade", "weapon"],
    "image_query": "medieval sword blade",
    "imageable": True,
}

_calls = {"n": 0}


def fake_complete_json(messages, schema, name, models=None, temperature=0.8):
    if name == "cinetot_word":
        return json.loads(json.dumps(WORD_REPLY)), "demo/mock"
    if name == "cinetot_interest":
        return {"words": ["rocket", "orbit", "launch", "planet", "crew", "fuel",
                          "gravity", "landing", "satellite", "telescope", "recipe",
                          "boil", "roast", "knife", "flour", "spice"]}, "demo/mock"
    i = min(_calls["n"], len(DRAFTS) - 1)
    _calls["n"] += 1
    return json.loads(json.dumps(DRAFTS[i])), "demo/mock"


def fake_lookup(word, lang):
    if word.lower() not in ("sword", "swords"):
        return None
    return {
        "source": "en.wiktionary.org",
        "url": "https://en.wiktionary.org/wiki/sword#English",
        "registers": ["literary"],
        "entries": [{"pos": "Noun", "senses": [
            {"gloss": "A long-bladed weapon with a hilt, used for cutting and thrusting.",
             "labels": [], "examples": ["He drew his sword."]},
            {"gloss": "Military power, or the use of force.",
             "labels": ["literary", "figurative"], "examples": []},
        ]}],
    }


def fake_images(query, limit=4):
    import urllib.parse
    shades = ["#3b4252", "#4c566a", "#5e6779", "#2e3440"]
    out = []
    for c in shades[:limit]:
        svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='148' height='108'>"
               f"<rect width='148' height='108' fill='{c}'/>"
               f"<text x='74' y='58' fill='#ddd9cf' font-family='sans-serif' "
               f"font-size='11' text-anchor='middle'>{query[:18]}</text></svg>")
        uri = "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)
        out.append({"url": uri, "thumb": uri, "title": query,
                    "credit": "demo placeholder \u00b7 CC0",
                    "source": "Wikimedia Commons", "page": ""})
    return out


llm.complete_json = fake_complete_json
llm.free_models = lambda refresh=False: ["demo/mock"]
dictionary.lookup = fake_lookup
images.search = fake_images

import app  # noqa: E402

if __name__ == "__main__":
    app.db.init()
    app.app.run(host="127.0.0.1", port=5058, use_reloader=False)
