"""Offline checks that need no API key and no network.

The point of interest is make_round's feedback loop: a model that ignores the
vocabulary ceiling on its first attempt should be corrected by the second.

    python test_offline.py
"""

import json
import sys

import config
import lexicon
from services import generate, llm


def test_lexicon():
    lex = lexicon.get_lexicon("en")
    assert lex.rank_of("the") == 0, "most frequent English lemma should rank first"
    assert lexicon.lemma_of("ran", "en") == "run"
    assert lexicon.lemma_of("mice", "en") == "mouse"
    assert lexicon.describe_lemma("en", "running")["lemma"] == "run"
    print("lexicon               ok")


def test_verification():
    allowed = lexicon.build_allowed_set("en", 2000, targets=["sword"])
    text = "The boy has a sword. He fights an intransigent wizard near Hogwarts."
    r = lexicon.verify_text(text, "en", allowed, ["sword"])
    kinds = {t["w"]: t["kind"] for t in r["tokens"]}
    assert kinds["sword"] == "target"
    assert kinds["intransigent"] == "stray"
    assert kinds["Hogwarts"] == "name", kinds
    assert kinds["boy"] == "known"
    assert r["targets_missing"] == []
    print("verification          ok")


def test_inflected_forms_count_as_known():
    """The reason lemmas matter: knowing 'run' should license 'ran'."""
    allowed = lexicon.build_allowed_set("en", 300, known_extra=["run"])
    r = lexicon.verify_text("He ran.", "en", allowed)
    assert [t["kind"] for t in r["tokens"]] == ["known", "known"], r["tokens"]
    print("inflection credit     ok")


def test_scoring_rejects_yea_saying():
    import assessment
    items, key = assessment.build_placement_test("en", seed=1)
    honest = {i: m["real"] and m["band"] in ("1K", "2K") for i, m in key.items()}
    lazy = {i: True for i in key}
    a = assessment.score_placement(key, honest)
    b = assessment.score_placement(key, lazy)
    assert a["reliable"] and not b["reliable"]
    assert a["vocab_estimate"] < b["vocab_estimate"]
    print("placement scoring     ok  "
          f"(honest {a['vocab_estimate']} words / yes-to-all flagged unreliable)")


def test_generation_loop():
    """Mock a model that overshoots the vocabulary limit until told twice."""
    drafts = [
        {"text": "A young man leaves his arid homeworld. He obtains a luminous sword "
                 "and joins an inscrutable old teacher. Together they confront Vader.",
         "answer": "Star Wars", "distractors": ["Dune", "Flash Gordon", "The Matrix"], "emoji": "🚀"},
        {"text": "A young man leaves his dry home world. He gets a bright sword and "
                 "joins a strange old teacher. Together they fight Vader.",
         "answer": "Star Wars", "distractors": ["Dune", "Flash Gordon", "The Matrix"], "emoji": "🚀"},
    ]
    calls = {"n": 0}

    def fake(messages, schema, name, models=None, temperature=0.8):
        i = min(calls["n"], len(drafts) - 1)
        calls["n"] += 1
        return json.loads(json.dumps(drafts[i])), "mock/model"

    original = llm.complete_json
    llm.complete_json = fake
    try:
        targets = ["sword"]
        allowed = lexicon.build_allowed_set("en", 2500, targets=targets)
        first = lexicon.verify_text(drafts[0]["text"], "en", allowed, targets)
        out = generate.make_round("en", "English", "movies",
                                  {"cefr": "A2", "vocab_estimate": 2500},
                                  allowed, targets)
    finally:
        llm.complete_json = original

    assert calls["n"] >= 2, "the loop should have asked the model to try again"
    assert len(out["meta"]["violations"]) < len(first["violations"]), (
        first["violations"], out["meta"]["violations"])
    assert out["meta"]["targets_used"] == ["sword"]
    assert len(out["options"]) == 4 and out["answer"] in out["options"]
    print(f"generation loop       ok  (draft 1 broke on {first['violations']}, "
          f"draft {calls['n']} left {out['meta']['violations'] or 'nothing'})")


def test_frontier_takes_the_first_crossing():
    """A band that scores well after knowledge has already collapsed is noise,
    and must not push the ceiling back out."""
    import assessment
    items, key = assessment.build_placement_test("en", seed=11)
    good = {"1K", "2K", "3K", "5K", "12K"}          # 8K deliberately missing
    resp = {i: (m["real"] and m["band"] in good) for i, m in key.items()}
    rep = assessment.score_placement(key, resp)
    assert rep["frontier_rank"] <= 8000, rep["frontier_rank"]
    print(f"frontier robustness   ok  (noisy 12K band ignored, frontier {rep['frontier_rank']})")


def test_json_recovery():
    """Free models fence, preface and trail their JSON. All of it must parse."""
    cases = [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure! Here you go:\n{"a": 1}\nHope that helps.',
        '{"a": "a } brace in a string"}',
    ]
    for c in cases:
        assert "a" in llm._extract_json(c), c
    print("json recovery         ok")


def test_pseudowords_are_not_real():
    import pseudowords
    for lang in ("en", "de", "fr"):
        lex = lexicon.get_lexicon(lang)
        words = pseudowords.generate(lang, 20)
        assert len(words) == 20
        assert not (set(words) & lex.lemma_set)
    print("pseudowords           ok")


def test_taught_words_reach_the_queue():
    """A target the model actually used must show up as 'learning', which is
    what the rail queue and future target selection both read."""
    import app
    c = app.app.test_client()
    c.get("/api/bootstrap")                       # get a learner cookie

    drafts = [{"text": "The knight lifted his sword and rode to the castle.",
               "answer": "A Knight's Tale",
               "distractors": ["Excalibur", "Braveheart", "Ivanhoe"], "emoji": "\u2694"}]

    def fake(messages, schema, name, models=None, temperature=0.8):
        return json.loads(json.dumps(drafts[0])), "mock/model"

    original_json, original_targets = llm.complete_json, lexicon.pick_targets
    llm.complete_json = fake
    lexicon.pick_targets = lambda *a, **k: ["sword", "knight", "castle"]
    key_was = config.OPENROUTER_API_KEY
    config.OPENROUTER_API_KEY = "test"
    try:
        r = c.post("/api/round", json={"lang": "en", "domain": "movies"}).get_json()
        assert "error" not in r, r
        assert set(r["quality"]["targets_used"]) == {"sword", "knight", "castle"}, r["quality"]
        prof = c.get("/api/profile?lang=en").get_json()
        queue = {q["lemma"] for q in prof["queue"] if q["met"]}
        assert {"sword", "knight", "castle"} <= queue, queue
        assert prof["counts"]["learning"] >= 3
    finally:
        llm.complete_json, lexicon.pick_targets = original_json, original_targets
        config.OPENROUTER_API_KEY = key_was
    print("teaching queue        ok  (three targets used, three queued as learning)")


def test_targets_sit_just_past_the_frontier():
    """i+1 means the next useful word, not an arbitrary rare one."""
    lex = lexicon.get_lexicon("en")
    for frontier in (1000, 3000):
        for _ in range(12):
            for t in lexicon.pick_targets("en", frontier, 3):
                rank = lex.rank_of(t)
                assert rank is not None and rank >= frontier, (t, rank, frontier)
                assert rank < frontier * 1.4 + 260, (t, rank, frontier)
    print("target selection      ok")


def test_api_surface():
    import app
    c = app.app.test_client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/bootstrap").status_code == 200
    start = c.post("/api/placement/start", json={"lang": "en"}).get_json()
    responses = {it["id"]: True for it in start["items"][:10]}
    rep = c.post("/api/placement/submit",
                 json={"test_id": start["test_id"], "responses": responses}).get_json()
    assert "vocab_estimate" in rep
    assert c.post("/api/placement/submit", json={"test_id": "nope"}).status_code == 404
    print("api surface           ok")


if __name__ == "__main__":
    config.DB_PATH = config.BASE_DIR / "data" / "test.sqlite3"
    for f in (test_lexicon, test_verification, test_inflected_forms_count_as_known,
              test_scoring_rejects_yea_saying, test_frontier_takes_the_first_crossing,
              test_json_recovery,
              test_pseudowords_are_not_real, test_targets_sit_just_past_the_frontier,
              test_generation_loop, test_taught_words_reach_the_queue, test_api_surface):
        try:
            f()
        except AssertionError as e:
            print(f"{f.__name__:22}FAIL  {e}")
            sys.exit(1)
    print("\nall offline checks passed")
