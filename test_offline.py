"""Offline checks. No API key, no network.

    python test_offline.py

Everything here exercises real code paths; the only substitution is the
generator, which is a provider rather than a monkeypatch.
"""

import sys

import config

config.DB_PATH = config.DATA_DIR / "test_learner.sqlite3"
config.CONTENT_DB = config.DATA_DIR / "test_content.sqlite3"

from content import probes, riddles, store as cstore, subjects  # noqa: E402
from core import cognates, corpus, ladder, placement, pseudo, vocab  # noqa: E402
from learner import policy, store as lstore  # noqa: E402
from providers.fake import FakeGenerator, ScriptedGenerator  # noqa: E402
from providers.openrouter import extract_json  # noqa: E402


def test_lemmas():
    from core.tokens import lemma_of
    assert lemma_of("ran", "en") == "run"
    assert lemma_of("mice", "en") == "mouse"
    assert lemma_of("gelaufen", "de") == "laufen"
    assert corpus.get("en").rank_of("the") == 0
    print("lemmatisation         ok")


def test_overflow_is_the_new_word_set():
    """KNOWN + N: nothing is forced, the spill is what gets taught."""
    known = vocab.build_known_set("en", 2000)
    r = vocab.analyse("A boy finds a bright sword near Hogwarts.", "en", known)
    kinds = {t["w"]: t["kind"] for t in r["tokens"]}
    assert kinds["Hogwarts"] == vocab.NAME, kinds
    assert kinds["boy"] == vocab.KNOWN
    assert "sword" in r["overflow"]
    assert r["ceiling_rank"] > 0
    print("overflow              ok")


def test_inflection_credit():
    known = vocab.build_known_set("en", 300, extra=["run"])
    r = vocab.analyse("He ran.", "en", known)
    assert [t["kind"] for t in r["tokens"]] == [vocab.KNOWN, vocab.KNOWN]
    print("inflection credit     ok")


def test_verdict_counts_not_identities():
    known = vocab.build_known_set("en", 2000)
    few = vocab.analyse("A boy finds a sword.", "en", known)
    many = vocab.analyse("An inscrutable luminous arid verdant clandestine hermit waited.",
                         "en", known)
    assert vocab.verdict(few, "en", 2000)[0], vocab.verdict(few, "en", 2000)
    ok, reasons, repair = vocab.verdict(many, "en", 2000)
    assert not ok and repair.get("simplify")
    # Feedback names specific words rather than demanding a rewrite.
    assert len(repair["simplify"]) >= 2
    print("verdict               ok")


def test_ceiling_governs_serving():
    assert vocab.servable(1800, ["boy", "sword"], 2000, set())
    assert not vocab.servable(2500, ["boy"], 2000, set())
    assert not vocab.servable(1800, ["boy", "sword"], 2000, {"sword"})
    print("servability           ok")


def test_teachability_filter():
    assert not ladder.assess("overpay", "en")["teachable"]
    assert not ladder.assess("dissatisfy", "en")["teachable"]
    assert ladder.assess("sword", "en")["teachable"]
    assert not ladder.assess("verkaufen", "de")["teachable"]
    print("teachability          ok")


def test_cognates_are_screened_not_guessed():
    assert cognates.is_cognate("información", "es")
    assert cognates.is_cognate("possibilité", "fr")
    assert not cognates.is_cognate("desarrollo", "es")
    # Rare English lookalikes must not count: knowing them would not help.
    score, match, _ = cognates.evidence("travail", "fr")
    assert score < cognates.CLOSE, (score, match)
    print("cognate screen        ok")


def test_placement_catches_the_decoder():
    """The failure that motivated this: a learner who decodes Latinate words
    without knowing them must not be placed high on that basis."""
    import random
    # Transparency is a relation between two languages, so the test has to say
    # which one the learner speaks. Without an L1 there is no control at all —
    # which was the original bug.
    items, key = placement.build("es", seed=5, l1="en")
    rng = random.Random(1)
    truth = {"1K": .95, "2K": .8, "3K": .35, "5K": .15,
             "8K": .05, "12K": .02, "20K": 0., "30K": 0.}
    resp = {}
    for i, m in key.items():
        p = (.08 if m["kind"] == placement.PSEUDO
             else .9 if m["kind"] == placement.COGNATE
             else truth[m["band"]])
        resp[i] = rng.random() < p
    rep = placement.score(key, resp)
    assert rep["cognate_inflated"], rep["transparency_gap"]
    assert rep["cefr"] in ("A1", "A2", "B1"), rep["cefr"]
    assert rep["frontier_rank"] < 4000, rep["frontier_rank"]
    print(f"cognate placement     ok  (B-level not C, gap {rep['transparency_gap']})")


def test_no_l1_means_no_cognate_control():
    """Declaring no first language must not silently produce a confident
    reading: with nothing to compare against there is no transparency axis."""
    _, key = placement.build("es", seed=5, l1=None)
    assert not any(m["kind"] == placement.COGNATE for m in key.values())
    _, key_en = placement.build("en", seed=5, l1="de")
    assert any(m["kind"] == placement.COGNATE for m in key_en.values()), \
        "an English test for a German speaker must still control for cognates"
    print("l1 wiring             ok")


def test_vocabulary_total_cannot_exceed_the_frontier():
    """A learner capped at rank 2000 cannot coherently be told they know 7000
    words. Summing every band let rare-band noise inflate the headline."""
    import random
    items, key = placement.build("en", seed=3)
    rng = random.Random(7)
    truth = {"1K": .9, "2K": .5, "3K": .2, "5K": .1,
             "8K": .05, "12K": .02, "20K": 0., "30K": 0.}
    resp = {i: rng.random() < (.05 if m["kind"] == placement.PSEUDO else truth[m["band"]])
            for i, m in key.items()}
    rep = placement.score(key, resp)
    assert rep["vocab_estimate"] <= rep["frontier_rank"] * 1.15, (
        rep["vocab_estimate"], rep["frontier_rank"])
    assert rep["cefr"] in ("A1", "A2", "B1"), rep["cefr"]
    print(f"vocab coherence       ok  ({rep['vocab_estimate']} words, "
          f"frontier {rep['frontier_rank']}, {rep['cefr']})")


def test_frontier_first_crossing():
    items, key = placement.build("en", seed=11)
    good = {"1K", "2K", "3K", "5K", "12K"}          # 8K deliberately missing
    resp = {i: (m["kind"] == placement.PLAIN and m["band"] in good) for i, m in key.items()}
    rep = placement.score(key, resp)
    assert rep["frontier_rank"] <= 8000, rep["frontier_rank"]
    print("frontier robustness   ok")


def test_pseudowords_are_not_real():
    for lang in ("en", "de"):
        lex = corpus.get(lang)
        words = pseudo.generate(lang, 15)
        assert len(words) == 15 and not (set(words) & lex.lemma_set)
    print("pseudowords           ok")


def test_json_recovery():
    for c in ['{"a":1}', '```json\n{"a":1}\n```', 'Sure:\n{"a":1}\ndone',
              '{"a":"a } brace"}']:
        assert "a" in extract_json(c)
    print("json recovery         ok")


def test_repair_loop_reduces_overflow():
    gen = ScriptedGenerator([
        {"text": "A young man lives on an arid world. He finds a luminous sword "
                 "and meets an inscrutable hermit near a castle.", "emoji": "X"},
        {"text": "A young man lives on a dry world. He finds a bright sword and "
                 "meets an old man near a castle.", "emoji": "X"},
    ])
    p = riddles.generate(gen, "en", "English", "movies", 2000)
    assert p["drafts"] == 2, p["drafts"]
    assert p["accepted_vocab"], p["reject_reason"]
    assert 1 <= len(p["new"]) <= config.MAX_NEW_WORDS, p["new"]
    assert len(p["options"]) == 4 and p["answer"] in p["options"]
    print(f"repair loop           ok  (2 drafts, teaches {p['new']})")


def test_empty_subject_table_says_so():
    """The old message blamed the level for an empty table, which sends you
    looking in the wrong place entirely."""
    from content import subjects as subj
    import sqlite3
    try:
        subj.pick("nonexistent-domain", 1000)
    except subj.NoSubjects as e:
        assert "table is empty" in str(e) and "cli.py seed" in str(e), str(e)
    else:
        raise AssertionError("expected NoSubjects")

    # A populated table with nothing tellable this low is a different problem
    # and must read differently.
    try:
        subj.pick("movies", 100)
    except subj.NoSubjects as e:
        assert "none is tellable" in str(e) and "the easiest needs" in str(e), str(e)
    else:
        raise AssertionError("expected NoSubjects for too-low level")
    print("subject errors        ok")


def test_fake_respects_the_requested_level():
    """A fixed word list cannot satisfy a low ceiling, so offline runs at
    level 1000 produced nothing but rejections and looked broken."""
    gen = FakeGenerator(seed=4)
    made = [riddles.generate(gen, "en", "English", "movies", 1000) for _ in range(6)]
    assert any(p["accepted_vocab"] for p in made), \
        [p["reject_reason"] for p in made]
    print("fake level awareness  ok")


def test_distractors_are_real_and_grouped():
    subj = [s for s in cstore.subjects("movies") if s["title"] == "Star Wars"][0]
    opts = cstore.distractors_for(subj, 3)
    assert len(opts) == 3 and "Star Wars" not in opts
    titles = {s["title"] for s in cstore.subjects("movies")}
    assert set(opts) <= titles, "distractors must be real subjects, never invented"
    print("distractors           ok")


def test_probe_panel_detects_each_failure():
    gen = FakeGenerator(seed=3, solve_rate=0.95)
    p = riddles.generate(gen, "en", "English", "movies", 2000)
    p["id"] = riddles.persist(p)
    r = cstore.get_riddle(p["id"])

    good = probes.run(gen, r, "English", record=False)
    blind = probes.run(FakeGenerator(seed=3, solve_rate=0.0), r, "English", record=False)
    assert "no solver recovered the subject from the text" in blind["reasons"]
    # Success is weak evidence, failure is strong: only the reject path is
    # asserted, which is exactly how the gate is meant to be read.
    assert good["solved_open_of"] >= 2, good["solved_open_of"]
    assert blind["blind_of"] >= 3, "a chance rate needs more than one sample"
    print("probe panel           ok")


def test_answer_matching_is_not_fooled_by_substrings():
    """Flattening titles let a wrong answer score as correct: "E.T." becomes
    "et", which occurs inside "somethingelse". Caught as an intermittent test
    failure; it would have silently inflated every probe score."""
    same = probes._same
    assert not same("Something Else", "E.T.")
    assert not same("Something Else", "Up")
    assert not same("Something Else", "Home Alone")
    assert same("E.T.", "E.T.")
    assert same("The Lion King", "Lion King")      # articles are noise
    assert same("Rocky", "Rocky Balboa")           # genuine shorter form
    assert same("Star Wars", "Star Wars (1977)")
    assert not same("Jaws", "Star Wars")
    print("answer matching       ok")


def test_solver_panel_survives_stale_model_ids():
    """Configured free-model IDs go stale. If the panel passed dead IDs
    through, every probe would fail and every riddle would be rejected for
    what look like content problems."""
    gen = FakeGenerator(seed=1)
    real = config.PROBE_MODELS
    try:
        config.PROBE_MODELS = ["retired/model-a:free", "retired/model-b:free"]
        panel = probes.solver_panel(gen)
        assert panel and all(m in gen.models() for m in panel), panel
    finally:
        config.PROBE_MODELS = real
    assert probes.solver_panel(gen, exclude="fake/alpha") == ["fake/beta", "fake/gamma"]
    print("solver resolution     ok")


def test_corpus_first_serving_and_writeback():
    lstore.init()
    gen = FakeGenerator(seed=9, solve_rate=0.9)
    lid = "test-learner"
    lstore.touch(lid)
    lstore.save_profile(lid, "en", {
        "vocab_estimate": 2000, "frontier_rank": 2000, "cefr": "B1",
        "false_alarm_rate": 0.1, "reliable": True, "consistent": True,
        "transparency_gap": None, "bands": []})

    before = sum(cstore.counts().values())
    out = policy.serve(lid, "en", "movies", generator=gen, lang_name="English")
    assert out and out["round_id"]
    after = sum(cstore.counts().values())

    if out["source"] == "live":
        assert after > before, "live generations must be written back to the corpus"
    # Same riddle must not come round twice.
    seen = lstore.seen_ids(lid, "en")
    assert out["round_id"] in seen
    print(f"corpus serving        ok  (source: {out['source']}, write-back verified)")


def test_frontier_retreats_faster_than_it_advances():
    lid = "test-estimate"
    lstore.touch(lid)
    lstore.save_profile(lid, "en", {
        "vocab_estimate": 2000, "frontier_rank": 2000, "cefr": "B1",
        "false_alarm_rate": 0.1, "reliable": True, "consistent": True,
        "transparency_gap": None, "bands": []})
    for i in range(6):
        lstore.mark_seen(lid, f"r{i}", "en")
        lstore.record_answer(lid, f"r{i}", "x", False)
    down = policy.reestimate(lid, "en")
    assert down < 2000, down

    lstore.save_profile(lid, "en", {
        "vocab_estimate": 2000, "frontier_rank": 2000, "cefr": "B1",
        "false_alarm_rate": 0.1, "reliable": True, "consistent": True,
        "transparency_gap": None, "bands": []})
    for i in range(6):
        lstore.record_answer(lid, f"r{i}", "x", True)
    up = policy.reestimate(lid, "en")
    assert 2000 < up < 2000 * 1.2, up
    assert (2000 - down) > (up - 2000), "retreat must outpace advance"
    print(f"frontier drift        ok  ({down} down vs {up} up)")


def test_studio_api():
    from studio import app as S
    c = S.app.test_client()
    for url in ("/api/overview", "/api/coverage?lang=en&hi=3000", "/api/queue",
                "/api/telemetry", "/api/subjects", "/api/ladder?lang=en&lo=1000&hi=1010"):
        assert c.get(url).status_code == 200, url
    ov = c.get("/api/overview").get_json()
    assert "log" in ov["worker"], "the UI needs the activity log to show progress"
    print("studio api            ok")


def test_generate_one_reports_synchronously():
    """A background job that takes a minute and reports nothing is
    indistinguishable from a broken button."""
    from studio import app as S
    c = S.app.test_client()
    r = c.post("/api/generate/one",
               json={"lang": "en", "domain": "movies", "level": 2000}).get_json()
    assert "ok" in r and "stage" in r and "seconds" in r, r
    if r.get("error"):
        raise AssertionError(r["error"])
    assert r["text"] and r["answer"], r
    # Either it was accepted, or it says why not. Never silent.
    assert r["ok"] or r["reason"], r
    print(f"generate one          ok  ({'accepted' if r['ok'] else 'rejected, reason given'})")


def test_studio_mounts_under_a_prefix():
    """Single-service hosts need both apps in one process; relative asset and
    API paths are what make the mount work."""
    import wsgi
    from werkzeug.test import Client
    c = Client(wsgi.app)
    assert c.get("/studio").status_code == 308
    assert c.get("/studio/").status_code == 200
    assert c.get("/studio/api/overview").status_code == 200
    assert c.get("/api/health").status_code == 200
    js = (config.BASE_DIR / "studio" / "static" / "studio.js").read_text(encoding="utf-8")
    assert "const ROOT" in js, "studio must resolve API calls relative to its mount"
    html = (config.BASE_DIR / "studio" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="studio.css"' in html and 'src="studio.js"' in html, \
        "absolute asset paths break under a prefix mount"
    print("prefix mount          ok")


def test_bootstrap_matches_what_the_ui_reads():
    """The UI reading a field the API does not send fails silently: the key
    warning fired on every load because it checked a renamed field."""
    import re
    from web import app as W
    payload = W.app.test_client().get("/api/bootstrap").get_json()
    js = (config.BASE_DIR / "web" / "static" / "app.js").read_text(encoding="utf-8")
    # Only the function that consumes /api/bootstrap; other handlers read
    # other payloads and would produce false positives.
    start = js.index("async function boot(")
    body = js[start:js.index("\nasync function", start + 10)]
    read = set(re.findall(r"\bdata\.([a-zA-Z_]+)\b", body))
    missing = read - set(payload)
    assert not missing, f"boot() reads {sorted(missing)}, bootstrap does not send them"
    print(f"bootstrap contract    ok  ({len(read)} fields checked)")


def test_web_api():
    from web import app as W
    c = W.app.test_client()
    assert c.get("/api/health").status_code == 200
    start = c.post("/api/placement/start", json={"lang": "en"}).get_json()
    assert len(start["items"]) > 40, len(start["items"])
    resp = {it["id"]: True for it in start["items"][:10]}
    rep = c.post("/api/placement/submit",
                 json={"test_id": start["test_id"], "responses": resp}).get_json()
    assert "frontier_rank" in rep
    assert c.post("/api/placement/submit", json={"test_id": "no"}).status_code == 404
    print("web api               ok")


TESTS = [
    test_lemmas, test_overflow_is_the_new_word_set, test_inflection_credit,
    test_verdict_counts_not_identities, test_ceiling_governs_serving,
    test_teachability_filter, test_cognates_are_screened_not_guessed,
    test_placement_catches_the_decoder, test_no_l1_means_no_cognate_control,
    test_vocabulary_total_cannot_exceed_the_frontier, test_frontier_first_crossing,
    test_pseudowords_are_not_real, test_json_recovery,
    test_repair_loop_reduces_overflow, test_empty_subject_table_says_so,
    test_fake_respects_the_requested_level, test_distractors_are_real_and_grouped,
    test_probe_panel_detects_each_failure, test_answer_matching_is_not_fooled_by_substrings,
    test_solver_panel_survives_stale_model_ids,
    test_corpus_first_serving_and_writeback,
    test_frontier_retreats_faster_than_it_advances,
    test_studio_api, test_generate_one_reports_synchronously,
    test_studio_mounts_under_a_prefix,
    test_bootstrap_matches_what_the_ui_reads, test_web_api,
]

if __name__ == "__main__":
    for f in (config.DB_PATH, config.CONTENT_DB):
        for suffix in ("", "-wal", "-shm"):
            p = f.with_name(f.name + suffix)
            if p.exists():
                p.unlink()
    cstore.init()
    subjects.seed()
    failed = 0
    for t in TESTS:
        try:
            t()
        except AssertionError as e:
            print(f"{t.__name__:22}FAIL  {e}")
            failed += 1
        except Exception as e:
            print(f"{t.__name__:22}ERROR {type(e).__name__}: {e}")
            failed += 1
    print("\n" + ("all offline checks passed" if not failed else f"{failed} failed"))
    sys.exit(1 if failed else 0)
