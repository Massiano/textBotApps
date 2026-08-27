# CineTot

A language-learning game that writes riddles you can almost read. It measures
your vocabulary, then shows you a description of a film using only words you
know — plus one to three you don't.

Most content is **precomputed offline**, reviewed in a dashboard, and served by
query. Live generation is a fallback, not the main path.

---

## The two ideas that shape everything

### KNOWN + N

The rule is: every word is known, except N of them, where N is 1–3.

*Which* words is not essential. Earlier versions picked three target words and
forced the model to use them, which bent the story around arbitrary vocabulary
and created a failure mode (required word missing) that only existed because we
invented the requirement.

So the loop measures instead of dictating:

```
constrain to known vocabulary
  → model writes freely about a chosen subject
  → verifier measures the overflow
  → the overflow IS the new-word set
```

Accepted when the overflow is 1–3 lemmas, all inside the next shell out.
Rejected otherwise, with feedback that names the specific offending words —
usually one word to replace, not a rewrite. A text with *zero* overflow is not
a failure either; it is a consolidation round, and worth serving occasionally.

### Precompute is exact

A riddle reduces to one integer:

```
ceiling_rank = highest rank among the words a reader must already know
```

Any learner whose frontier clears that number can be served that riddle. It is
an integer comparison, indexable in SQL, and the *same verifier computes it
offline and online* — which is why `core/` imports nothing from `providers/`.

The only per-learner refinement is intersecting the riddle's lemma list against
the handful of common words that particular learner marked unknown.

**The corpus is indexed by word, not by riddle.** The product is teaching a
word, so `riddle_new(lemma → riddle)` is the primary key and coverage is the
real quality metric: not how many riddles exist, but how many words can be
taught.

---

## The studio

`studio/` is where content is made and reviewed. Five views:

**Coverage** — how many teachable lemmas have at least one, or at least three,
accepted riddles reachable at their rank. Click a band to see what is missing
and queue generation against it.

**Review** — keyboard-driven (`A` accept, `R` reject, `S` skip, `P` probe).
Vocabulary and solvability are already machine-checked and shown inline, so a
reviewer judges only what models cannot: whether it reads naturally, whether it
is interesting, whether it is appropriate. Probed riddles surface first.

**Yield** — drafts per acceptance by model, probe outcomes, and a ranked list of
why drafts are rejected. This is what refinement means: the numbers here should
drive which models are preferred.

**Subjects** — recognizability is hand-curated; retellability is overwritten by
the probe panel as evidence arrives.

**Ladder** — the frequency list minus what is not worth a riddle, with every
heuristic verdict overridable. An override always wins.

The worker runs in a background thread, and jobs are database rows, so a restart
resumes rather than losing work.

---

## Automated quality probes

Every riddle that passes the free local vocabulary check is handed to **fresh
contexts on models other than the generator**. Four probes:

| Probe | Input | Detects |
|---|---|---|
| open recall | text only | under-determined riddle |
| forced choice | text + options | weak or over-close distractors |
| options only | options, no text | option-set leakage — must sit at chance |
| cloze | a new word masked | whether context supports the new word |

**Cloze is the pedagogically important one.** Comprehensible input requires the
new item to be inferable from its surroundings. A riddle that drops an unknown
word into unsupportive context is not teaching it, only using it, and nothing
else in the pipeline notices.

**Options-only is the cheapest and most overlooked.** If a model beats chance
from four titles alone, the option set gives the answer away by fame, formatting
or era clustering. It needs several samples — with four options, one hit is 25%
likely by luck.

### How to read the results

> Failure is strong evidence of a bad riddle. Success is only weak evidence of
> a good one.

A solver has unlimited vocabulary and world knowledge; a learner at 800 words
does not. Models identify films from cues no learner would catch. This is a
high-recall reject filter, never a certificate. Two things claw back signal: the
**weakest** model that still solves it estimates difficulty better than a pass
rate, and solvers are asked which cues they used.

It also **measures retellability** — if a film's riddles repeatedly fail open
recall, that film is hard to retell — and writes the rating back. It cannot
measure recognizability and is not allowed to try.

---

## Assessment

The yes/no test measures recognition of *form*. It cannot tell "I know this
word" from "I can decode this word", and Latinate vocabulary is transparent to
anyone with English. The error is one-directional: cognates only inflate. An
earlier version placed a tester at C1 purely on decoded Latin analogues.

So there are **two control item types**, measuring two ways of being wrong:

```
pseudowords   catch yes-saying           -> false alarm rate
cognates      catch decode-without-know  -> transparency gap
```

Transparency is a relation **between two languages**, so the reference is the
learner's declared first language, not English. Getting this wrong is not a
small error: a German speaker learning English decodes most of the Latinate
vocabulary in the upper bands, and treating English as the reference leaves
exactly that learner with no control at all. An early version placed such a
learner at C1 on decoded cognates alone.

The frontier is computed from opaque words only; cognate performance is reported
separately. Detection is offline — orthographic distance to *frequent* lemmas of
the first language, plus international affixes — and the matched word is exposed
so it can be overruled. (A resemblance to a rare word like *travail* tells us
nothing, so those are screened out.)

The vocabulary total is also **capped at the frontier**. Summing every band let
noise and transparent words in the rare bands inflate the headline far past the
point where knowledge demonstrably broke down; a learner capped at rank 2000
cannot coherently be told they know 7000 words.

Three further consequences:

- **The test is a prior, play is the likelihood.** Wrong answers and words
  marked unknown continuously correct the frontier.
- **The initial estimate is biased low** (`FRONTIER_BIAS`). Under-estimating
  costs a few easy rounds; over-estimating costs comprehension failure, which is
  what makes learners quit.
- **The frontier retreats faster than it advances**, for the same reason.

---

## Authoring

The studio is not only a monitor. **Write** is a composer: pick a subject and a
level, type a retelling, and the same verifier the generator is held to runs on
every keystroke. Words outside the level turn amber, the readout shows the new
word count, ceiling rank, sentence length and name count, and the save button
stays disabled until the text actually fits KNOWN + N. Hand-written riddles land
in the review queue like generated ones.

**Subjects** takes your own films. The 46 seeded titles are a starting point,
not the list — add, rate and retire freely. Recognizability is yours to judge;
retellability gets overwritten by the probe panel as evidence arrives.

**Ladder** overrides the teachability heuristic word by word, and an override
always wins.

### If generation produces nothing

`no subjects in domain 'movies' at all` means the content database is fresh.
`cli.py generate` now seeds it automatically, but if you see this, run
`python cli.py seed`. Check the path in the message matches the database you
expect — setting `CINETOT_CONTENT_DB` in one shell and not another gives you two
separate databases and a confusing empty one.

`none is tellable at level N` is the opposite problem: subjects exist, but every
one has a `min_frontier` above N. Generate higher, or lower a film's minimum in
the studio.

### The corpus starts empty

Nothing ships pre-generated. On a fresh clone there are subjects but no riddles,
so the learner app has nothing to serve until you either run generation with an
API key or write some by hand in **Write**. Without a key the generator falls
back to `providers.fake`, which produces structurally valid but meaningless
sentences — fine for exercising the pipeline, useless as content.

## Running it

```bash
pip install -r requirements.txt --break-system-packages
cp .env.example .env       # OPENROUTER_API_KEY optional
export $(cat .env | xargs)

python test_offline.py     # 18 checks, no key and no network
python run_studio.py       # dashboard on :5100
python -m web.app          # learner app on :5000
```

Without an API key everything still runs on `providers.fake`, which writes real
sentences and misbehaves the way free models do — overshooting the vocabulary on
a first draft, then correcting when told which words to replace. That is how the
UI and the pipeline were verified.

Headless precompute:

```bash
python cli.py seed
python cli.py generate --lang en --domain movies --levels 1000,2000 --want 20
python cli.py coverage --lang en
python cli.py accept --all-passing
python cli.py export corpus.json
```

---

## Deploying

`wsgi.py` serves both apps from one process, which is what single-web-service
hosts like Railway need:

```
/          the learner app
/studio/   the dashboard
```

The `Procfile` points at it. Set these:

| Variable | Why |
|---|---|
| `OPENROUTER_API_KEY` | without it the generator falls back to `fake` |
| `STUDIO_TOKEN` | **set this.** The studio creates and deletes content; unset, it is world-writable to anyone who guesses the path. Visit `/studio/?token=…` once and it sets a cookie. |
| `CINETOT_CONTENT_DB` | point at a mounted volume, e.g. `/app/data/content.sqlite3` |
| `CINETOT_DB` | likewise for learner data |

**Attach a volume.** Container filesystems reset on deploy, so without one every
riddle you generate and review is lost on the next push. Mount it at `/app/data`
and point both database variables inside it. `data/lex/*.json` is a rebuildable
cache and does not need to persist.

Confirm a deployment with `/api/health`: `"generator":"openrouter"` means the key
was picked up, and `"corpus"` shows how many riddles exist by status.

## Layout

```
core/          pure: no network, no database, no Flask
  tokens       tokenise, lemmatise
  corpus       frequency-ranked lemma lists
  vocab        known sets, analyse(), ceiling_rank, KNOWN+N verdict
  ladder       teachability filter, level quantisation
  cognates     transparency screening
  placement    test construction, three-way scoring
  pseudo       Markov control items

providers/     base interfaces + openrouter, wiktionary, imagery, fake
content/       offline production: riddles, subjects, probes, jobs, store
learner/       profiles, per-lemma knowledge, serving policy, estimation
web/           the learner app
studio/        the dashboard
cli.py         headless precompute
```

`core/` importing nothing from `providers/` is the rule that keeps the offline
and online verifiers provably identical. `providers/fake.py` exists so tests and
demos configure a provider rather than monkeypatching module globals.

---

## Dependencies, and why these

| Package | Version | Why |
|---|---|---|
| `wordfreq` | 3.1.1 | corpus frequency, 42 languages, data bundled, no network |
| `simplemma` | 2.0.0 | lemmatiser, 54 languages, pure Python, lazy per language |
| `Flask` | 3.1.3 | |
| `requests` | 2.33.1 | |
| `gunicorn` | 23.0.0 | |

**No spaCy** — a model per language is 15–50 MB and will not fit a free tier
alongside everything else.

**33 languages**, the intersection of the two libraries, which conveniently
excludes CJK: neither segments it without `jieba` or `mecab`.

**One worker.** `wordfreq` bundles ~57 MB and `simplemma` loads a dictionary per
active language; several workers multiply that.

---

## Known limits

- **Proper nouns.** No POS tagger, so a capitalised word absent from the
  frequency list is treated as a name. Works for *Hogwarts* and *Skywalker*.
  Fails for *Vader*, a genuine rank-16,413 word in German (Dutch for *father*),
  which is flagged as rare vocabulary instead. Costs an occasional extra draft.
- **Cognate screening is a screen, not philology.** `perro` matches `perry`.
  The matched word is exposed so it can be overruled, and the bins only need to
  be enriched for the aggregate statistic to work.
- **Short cognates are missed.** `haus`/`house` falls under the 5-character
  minimum, because short words collide by chance too often.
- **The probe gate needs per-language solver calibration** before it is trusted
  outside English. A failure on a Hungarian riddle may be the solver's weakness
  rather than the riddle's, and without calibration the gate would silently
  reject good content in exactly the languages with the thinnest coverage.
- **Without a declared first language there is no cognate control**, and an
  English test for a European speaker will read high. The learner app asks, but
  the question is skippable.
- **No accounts.** Learners are a cookie.

---

## Not yet built

In the order the design calls for them:

1. **Defining dictionary** — an entry per lemma defined only in high-frequency
   words, giving `explicability_rank`. This replaces frequency proximity as the
   test for "the next shell out", and makes the word panel's readability a
   checked property rather than a request in a prompt.
2. **L2-only interface** — a fixed string set per language plus icons, with no
   English anywhere. Enforced structurally: no literal text in templates.
3. **Visual dictionary** — concept-keyed rather than word-keyed, so one curated
   image set serves all 33 languages. Prioritised by `explicability_rank`: the
   words that need pictures are the ones that cannot be defined within reach.
