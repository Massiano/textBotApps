"""A generator that needs no API key.

This exists so the pipeline, the worker and the dashboard can be exercised
offline — and so tests stop monkeypatching module globals, which is what v1 had
to do. It writes real sentences from a controlled word pool, and misbehaves in
the specific ways free models misbehave: overshooting the vocabulary on a first
draft, then correcting when told which words to replace.
"""

import random

from providers.base import Generator, Trace

HARD = ["inscrutable", "luminous", "arid", "peculiar", "resolute",
        "clandestine", "tempestuous", "verdant", "obstinate"]
MID = ["sword", "castle", "island", "ghost", "storm", "lion", "ship", "ring",
       "robot", "princess", "shark", "prison", "desert", "mountain"]
PERSON = ["man", "boy", "girl", "woman", "friend", "father", "mother", "king"]
PLACE = ["house", "city", "road", "river", "forest", "town", "sea", "hill"]
THING = ["door", "hand", "box", "letter", "light", "stone", "bird", "horse"]


class FakeGenerator(Generator):
    name = "fake"

    def __init__(self, seed=0, hard_first_draft=True, solve_rate=0.8, oracle=None, cloze_oracle=None):
        self.rng = random.Random(seed)
        self.hard_first_draft = hard_first_draft
        self.solve_rate = solve_rate
        # A fake cannot really identify a film, so tests hand it the answer and
        # it succeeds at the configured rate. This makes both the pass and the
        # fail path of the panel reachable offline.
        self.oracle = oracle
        self.cloze_oracle = cloze_oracle
        self.calls = 0
        self.trace = Trace()
        self._seen = {}

    def models(self, refresh=False):
        return ["fake/alpha", "fake/beta", "fake/gamma"]

    def available(self, candidates=None):
        return list(candidates or self.models())

    def cooldowns(self):
        return {}

    def penalise(self, model, seconds=60):
        pass

    # ------------------------------------------------------------------
    def json(self, messages, schema, schema_name, model=None, temperature=0.8):
        import json as _json
        import time as _time
        t0 = _time.time()
        self.calls += 1
        mid = model or "fake/alpha"
        last = messages[-1]["content"] if messages else ""

        if schema_name == "cinetot_riddle":
            out = self._riddle(messages, last)
        elif schema_name == "probe_open":
            out = self._open(last)
        elif schema_name in ("probe_forced", "probe_blind"):
            out = self._choice(last, schema_name)
        elif schema_name == "probe_cloze":
            out = {"word": self._cloze(last)}
        else:
            out = {}
        flat = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        self.trace.add(mid, schema_name, flat, _json.dumps(out), _time.time() - t0)
        return out, mid

    def text(self, messages, model=None, temperature=0.7, max_tokens=600):
        self.calls += 1
        return "ok", model or "fake/alpha"

    # ------------------------------------------------------------------
    def _title_in(self, text):
        """The generation prompt names the subject; the probe prompts do not.
        Remembering what it wrote is how the fake plays both roles."""
        import re
        m = re.search(r"\*\*(.+?)\*\*", text)
        return m.group(1).strip() if m else None

    def _level_of(self, messages):
        """Infer the ceiling from the same signal a real model gets.

        The prompt no longer states a rank — a model cannot act on one — so the
        fake reads the reading-level phrase instead, exactly as a real model
        would have to. It maps back through the same table the prompt is built
        from, so the two cannot drift apart.
        """
        from content.riddles import READING_LEVEL
        text = " ".join(m.get("content", "") for m in messages)
        for limit, phrase in READING_LEVEL:
            if phrase in text:
                return min(limit, 8000)
        return 2000

    def _pools(self, level):
        """Draw real words from the corpus: easy ones well inside the level,
        shell words just past it, and far words to overshoot with."""
        from core import corpus
        lex = corpus.get("en")
        easy = [w for w in lex.lemmas[300:max(400, int(level * 0.6))] if len(w) > 2]
        lo, hi = level, min(int(level * 1.3) + 200, len(lex.lemmas))
        shell = [w for w in lex.lemmas[lo:hi] if len(w) > 2] or easy[-40:]
        far = [w for w in lex.lemmas[min(hi * 3, len(lex.lemmas) - 400):] if len(w) > 3]
        return easy, shell, far

    def _riddle(self, messages, last):
        # Every repair note ends the same way, so this catches all of them —
        # including "you taught nothing", which must not send the fake back to
        # its overshooting first-draft behaviour.
        followup = "Return the full JSON object again" in last
        n_hard = 0 if followup else (2 if self.hard_first_draft else 0)
        n_mid = 2 if followup else 2

        level = self._level_of(messages)
        easy, shell, far = self._pools(level)
        hard = self.rng.sample(far, min(n_hard, len(far)))
        mid = self.rng.sample(shell, min(n_mid, len(shell)))
        who = self.rng.sample(PERSON, 3)
        where = self.rng.sample(PLACE, 3)
        what = self.rng.sample(THING, 3)

        sentences = [
            f"A young {who[0]} lives near a {where[0]}.",
            f"One night he finds a {mid[0]} behind an old {what[0]}.",
            f"An old {who[1]} tells him to leave the {where[1]}.",
            f"They walk for many days and cross the {where[2]}.",
            f"At the end the {who[2]} is free and goes home.",
        ]
        if len(mid) > 1:
            sentences.insert(3, f"A {mid[1]} waits for them there.")
        for i, h in enumerate(hard):
            sentences.insert(min(1 + i, len(sentences)), f"The {what[i % 3]} is {h}.")
        text = " ".join(sentences)
        title = None
        for m in messages:
            title = title or self._title_in(m.get("content", ""))
        if title:
            self._seen[text.strip()] = title
        return {"text": text, "emoji": "\U0001F3AC"}

    def _recall(self, last):
        """Recover the subject of a text this instance generated earlier."""
        if self.oracle:
            return self.oracle
        for text, title in self._seen.items():
            head = text[:60]
            if head and head in last:
                return title
        return None

    def _open(self, last):
        answer = self._recall(last)
        if answer is None or self.rng.random() > self.solve_rate:
            answer = "Something Else"
        return {"answer": answer, "cues": "a sword, a castle, a night journey",
                "confidence": 0.7}

    def _choice(self, last, kind):
        options = [l[2:].strip() for l in last.splitlines() if l.startswith("- ")]
        if not options:
            return {"answer": ""}
        if kind == "probe_blind":
            return {"answer": self.rng.choice(options)}
        hint = self._recall(last)
        if hint in options and self.rng.random() <= self.solve_rate:
            return {"answer": hint}
        return {"answer": self.rng.choice(options)}

    def _cloze(self, last):
        """Recover the masked word by diffing against the text it wrote."""
        if self.cloze_oracle:
            return self.cloze_oracle if self.rng.random() < self.solve_rate else "banana"
        if self.rng.random() > self.solve_rate:
            return "banana"
        i = last.find("____")
        if i < 0:
            return "banana"
        prefix = last[max(0, i - 40):i]
        for text in self._seen:
            j = text.find(prefix)
            if prefix and j >= 0:
                rest = text[j + len(prefix):].split()
                if rest:
                    return rest[0].strip(".,!?;:")
        return "banana"

    def _subject_hint(self, text):
        """The fake cannot really solve riddles, so tests inject the expected
        answer through a marker the real prompt never contains."""
        for line in text.splitlines():
            if line.startswith("- "):
                return line[2:].strip()
        return "Unknown"


class ScriptedGenerator(Generator):
    """Returns a fixed queue of replies. For exact-behaviour tests."""

    name = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def models(self, refresh=False):
        return ["scripted/1"]

    def available(self, candidates=None):
        return ["scripted/1"]

    def cooldowns(self):
        return {}

    def json(self, messages, schema, schema_name, model=None, temperature=0.8):
        self.calls += 1
        i = min(self.calls - 1, len(self.replies) - 1)
        return dict(self.replies[i]), model or "scripted/1"

    def text(self, messages, model=None, temperature=0.7, max_tokens=600):
        self.calls += 1
        return "", "scripted/1"
