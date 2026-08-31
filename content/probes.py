"""Testing riddles by handing them to fresh contexts on other models.

Four probes, each isolating a different failure:

    open           text only, no options    -> is the subject recoverable at all
    forced         text plus four options   -> are the distractors doing work
    options_only   four options, no text    -> does the option set leak
    cloze          a new word masked        -> does the context support the word

Two rules govern the whole design.

**Fresh context, different model.** A model solving its own output measures
self-consistency, not solvability. Cross-model agreement is the signal.

**The asymmetry.** A solver has unlimited vocabulary and world knowledge; a
learner at 800 words does not. Models recognise films from cues no learner would
catch. So failure is strong evidence of a bad riddle, and success is only weak
evidence of a good one. This is a high-recall reject filter and never a
certificate. Two things claw back signal: the *weakest* model that succeeds is
a better difficulty estimate than the pass rate, and asking solvers which cues
they used shows whether the riddle works on visible content.
"""

import re

import config
import logs
from content import store

log = logs.get("probe")
from core.tokens import tokenize

OPEN_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "cues": {"type": "string"},
                   "confidence": {"type": "number"}},
    "required": ["answer", "cues", "confidence"], "additionalProperties": False,
}
CHOICE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"], "additionalProperties": False,
}
CLOZE_SCHEMA = {
    "type": "object",
    "properties": {"word": {"type": "string"}},
    "required": ["word"], "additionalProperties": False,
}


ARTICLES = {"the", "a", "an", "le", "la", "les", "el", "los", "las",
            "der", "die", "das", "il", "lo", "un", "une", "een"}


def _tokens(s):
    """Title reduced to comparable words, articles dropped.

    Comparing flattened strings let a wrong answer be scored correct: "E.T."
    flattens to "et", which occurs inside "somethingelse". Keeping word
    boundaries makes containment mean what it looks like it means.
    """
    words = re.findall(r"[a-z0-9]+", (s or "").lower())
    kept = [w for w in words if w not in ARTICLES]
    return kept or words


def _same(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # A shorter title may name the same thing ("Rocky" / "Rocky Balboa"), but
    # only if every one of its words appears in the other.
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return set(short) <= set(long)


# ------------------------------------------------------------------ probes

def open_recall(gen, riddle, model):
    msgs = [
        {"role": "system", "content": "You identify what a description refers to. "
                                      "Answer with the best known title, and name the "
                                      "specific details you used."},
        {"role": "user", "content": f"What does this describe?\n\n{riddle['text']}"},
    ]
    out, _ = gen.json(msgs, OPEN_SCHEMA, "probe_open", model=model, temperature=0.2)
    return out.get("answer", ""), out.get("cues", "")


def forced_choice(gen, riddle, model):
    opts = "\n".join(f"- {o}" for o in riddle["options"])
    msgs = [
        {"role": "system", "content": "Pick exactly one option. Reply with its text."},
        {"role": "user", "content": f"{riddle['text']}\n\nWhich is it?\n{opts}"},
    ]
    out, _ = gen.json(msgs, CHOICE_SCHEMA, "probe_forced", model=model, temperature=0.2)
    return out.get("answer", "")


def options_only(gen, riddle, model):
    """No text at all. Should be at chance — anything better means the option
    set gives the answer away by fame, formatting or era clustering."""
    opts = "\n".join(f"- {o}" for o in riddle["options"])
    msgs = [
        {"role": "system", "content": "Pick exactly one option. Reply with its text."},
        {"role": "user", "content": "One of these was described in a text you have not "
                                    f"seen. Guess which.\n{opts}"},
    ]
    out, _ = gen.json(msgs, CHOICE_SCHEMA, "probe_blind", model=model, temperature=1.0)
    return out.get("answer", "")


def cloze(gen, riddle, model, lang_name):
    """Mask a new word and see whether the surroundings recover it.

    Comprehensible input requires the new item to be inferable from context. A
    riddle that drops an unknown word into unsupportive surroundings is not
    teaching it, only using it — and nothing else in the pipeline notices.
    """
    if not riddle["new"]:
        return None, None
    target = riddle["new"][0]
    masked, hit = [], False
    from core.tokens import lemma_of
    last = 0
    for surface, s, e in tokenize(riddle["text"]):
        if lemma_of(surface, riddle["lang"]) == target and not hit:
            masked.append(riddle["text"][last:s] + "____")
            last = e
            hit = True
    if not hit:
        return None, None
    masked.append(riddle["text"][last:])
    text = "".join(masked)

    msgs = [
        {"role": "system", "content": f"You fill a single missing {lang_name} word. "
                                      "Reply with the word alone, in its dictionary form."},
        {"role": "user", "content": f"{text}\n\nWhat word belongs in ____ ?"},
    ]
    out, _ = gen.json(msgs, CLOZE_SCHEMA, "probe_cloze", model=model, temperature=0.3)
    guess = (out.get("word") or "").strip()
    return target, lemma_of(guess, riddle["lang"]) == target


# ------------------------------------------------------------------- panel

def solver_panel(gen, exclude=None):
    """Pick solvers that actually exist right now.

    Free model IDs churn, so the configured panel is treated as a preference
    rather than a fact. If none of them are live the panel falls back to
    whatever the catalogue offers — otherwise every probe fails and every
    riddle gets rejected for what look like content problems.
    """
    try:
        catalogue = gen.models()
    except Exception:
        catalogue = []
    available = [m for m in config.PROBE_MODELS if m in catalogue]
    if not available:
        available = [m for m in catalogue if m not in (config.PROBE_MODELS or [])]
        available = available[:len(config.PROBE_MODELS) or 3]
    if not available:
        available = list(config.PROBE_MODELS)
    # Never let the generating model judge its own output.
    return [m for m in available if m != exclude] or available


def run(gen, riddle, lang_name, models=None, record=True):
    """Run the panel over one riddle and return a verdict."""
    models = models or solver_panel(gen, exclude=riddle.get("model"))

    result = {"open": [], "forced": [], "blind": [], "cloze": [], "cues": []}

    for model in models:
        try:
            answer, cues = open_recall(gen, riddle, model)
            ok = _same(answer, riddle["answer"])
            result["open"].append({"model": model, "answer": answer, "correct": ok})
            result["cues"].append({"model": model, "cues": cues})
            if record:
                store.add_probe(riddle["id"], "open", model, answer, ok, cues)
        except Exception as e:
            result["open"].append({"model": model, "error": str(e)})

        try:
            answer = forced_choice(gen, riddle, model)
            ok = _same(answer, riddle["answer"])
            result["forced"].append({"model": model, "answer": answer, "correct": ok})
            if record:
                store.add_probe(riddle["id"], "forced", model, answer, ok)
        except Exception as e:
            result["forced"].append({"model": model, "error": str(e)})

    # The blind probe estimates a chance rate, so one sample cannot say
    # anything: with four options a single hit is 25% likely by luck. Take
    # several before drawing a conclusion.
    probe_model = models[0]
    for _ in range(config.PROBE_BLIND_SAMPLES):
        try:
            answer = options_only(gen, riddle, probe_model)
            ok = _same(answer, riddle["answer"])
            result["blind"].append({"model": probe_model, "answer": answer, "correct": ok})
            if record:
                store.add_probe(riddle["id"], "options_only", probe_model, answer, ok)
        except Exception as e:
            result["blind"].append({"model": probe_model, "error": str(e)})
            break

    try:
        word, ok = cloze(gen, riddle, probe_model, lang_name)
        if word is not None:
            result["cloze"].append({"model": probe_model, "word": word, "correct": ok})
            if record:
                store.add_probe(riddle["id"], "cloze", probe_model, word, ok)
    except Exception as e:
        result["cloze"].append({"model": probe_model, "error": str(e)})

    verdict = judge(result, models)
    log.info("%-24s %s  open %d/%d  forced %d/%d  blind %d/%d  cloze %d/%d",
             riddle["answer"][:24], "PASS  " if verdict["pass"] else "REJECT",
             verdict["solved_open"], verdict["solved_open_of"],
             verdict["solved_forced"], verdict["solved_forced_of"],
             verdict["blind_correct"], verdict["blind_of"],
             verdict["cloze_correct"], verdict["cloze_of"])
    if not verdict["pass"]:
        log.info("       %s", "; ".join(verdict["reasons"]))
    return {**result, **verdict}


def judge(result, models):
    """Turn probe outcomes into a verdict, respecting the asymmetry."""
    def hits(key):
        got = [r for r in result[key] if "correct" in r]
        return sum(1 for r in got if r["correct"]), len(got)

    open_ok, open_n = hits("open")
    forced_ok, forced_n = hits("forced")
    blind_ok, blind_n = hits("blind")
    cloze_ok, cloze_n = hits("cloze")

    reasons = []
    if open_n and open_ok < config.PROBE_MIN_SOLVERS:
        reasons.append("no solver recovered the subject from the text")
    if forced_n and forced_ok == 0:
        reasons.append("no solver picked it even with the options")
    if blind_n >= 3 and blind_ok / blind_n > config.PROBE_OPTIONS_ONLY_MAX:
        reasons.append("the option set gives the answer away without the text")
    if cloze_n and cloze_ok == 0:
        reasons.append("the context does not support the new word")

    # The weakest model that still solves it estimates difficulty better than a
    # pass rate does: if only the strongest gets it, it is too oblique.
    weakest = None
    for r in reversed(result["open"]):
        if r.get("correct"):
            weakest = r["model"]
            break

    return {
        "solved_open": open_ok, "solved_open_of": open_n,
        "solved_forced": forced_ok, "solved_forced_of": forced_n,
        "blind_correct": blind_ok, "blind_of": blind_n,
        "cloze_correct": cloze_ok, "cloze_of": cloze_n,
        "weakest_solver": weakest,
        "difficulty": None if not open_n else round(1 - open_ok / open_n, 2),
        "pass": not reasons,
        "reasons": reasons,
    }


def repair_note(verdict):
    """Specific feedback for a riddle that failed the panel."""
    notes = []
    if "no solver recovered the subject from the text" in verdict["reasons"]:
        notes.append("The description is not identifiable. Add one concrete "
                     "distinguishing detail, still using easy words.")
    if "the context does not support the new word" in verdict["reasons"]:
        notes.append("The unfamiliar word appears without support. Rewrite the "
                     "sentence around it so its meaning can be guessed.")
    if "the option set gives the answer away without the text" in verdict["reasons"]:
        notes.append("(option set problem — regenerate distractors, not the text)")
    return " ".join(notes)
