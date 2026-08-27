#!/usr/bin/env python3
"""Headless precompute, for running the corpus without the dashboard.

    python cli.py seed
    python cli.py generate --lang en --domain movies --levels 1000,2000 --want 20
    python cli.py coverage --lang en
    python cli.py accept --all-passing
    python cli.py export corpus.json
"""

import argparse
import json
import sys

import config
from content import jobs, probes, riddles, store, subjects
from core import ladder
from providers.fake import FakeGenerator
from providers.openrouter import OpenRouter


def generator():
    return OpenRouter() if config.OPENROUTER_API_KEY else FakeGenerator(seed=7)


def cmd_seed(a):
    store.init()
    print(f"subjects added: {subjects.seed(force=a.force)}")


def cmd_generate(a):
    store.init()
    # A fresh database has no subjects, and generation cannot invent them.
    if not store.subjects(domain=a.domain, status=None):
        added = subjects.seed()
        print(f"empty subject table, seeded {added} subjects first")
    gen = generator()
    lang_name = config.ENGLISH_NAME.get(a.lang, "English")
    levels = [int(x) for x in a.levels.split(",")] if a.levels else ladder.levels(a.lang)[:3]
    made = 0
    for level in levels:
        for _ in range(a.want):
            try:
                p = riddles.generate(gen, a.lang, lang_name, a.domain, level)
            except Exception as e:
                print(f"  error: {e}")
                continue
            rid = riddles.persist(p)
            if not p["accepted_vocab"]:
                print(f"  [{level}] rejected: {p['reject_reason']}")
                continue
            r = store.get_riddle(rid)
            v = probes.run(gen, r, lang_name)
            store.set_probe_result(rid, v)
            if not v["pass"]:
                store.set_status(rid, "rejected", "; ".join(v["reasons"]))
                print(f"  [{level}] probe failed: {v['reasons'][0]}")
                continue
            made += 1
            print(f"  [{level}] {p['answer']}: teaches {', '.join(p['new'])} "
                  f"({p['drafts']} drafts, open {v['solved_open']}/{v['solved_open_of']})")
    print(f"\n{made} riddles now awaiting review. Run the studio to accept them.")


def cmd_coverage(a):
    store.init()
    overrides = store.teachability_overrides(a.lang)
    cov = {r["lemma"]: r["n"] for r in store.coverage(a.lang, a.domain, 0, a.hi)}
    print(f"{'band':<6}{'teachable':>11}{'1+':>8}{'3+':>8}")
    for label, lo, hi in config.BANDS:
        if lo >= a.hi:
            break
        teach = ladder.ladder(a.lang, lo, min(hi, a.hi), overrides)
        one = sum(1 for w in teach if cov.get(w, 0) >= 1)
        three = sum(1 for w in teach if cov.get(w, 0) >= 3)
        print(f"{label:<6}{len(teach):>11}{one:>8}{three:>8}")


def cmd_accept(a):
    store.init()
    n = 0
    for r in store.queue("candidate", limit=1000):
        if a.all_passing and not (r.get("probe") or {}).get("pass"):
            continue
        store.set_status(r["id"], "accepted", note="accepted via cli")
        n += 1
    print(f"accepted {n}")


def cmd_export(a):
    store.init()
    out = {"riddles": [store.get_riddle(r["id"]) for r in
                       store.rows("SELECT id FROM riddle WHERE status='accepted'")],
           "subjects": store.subjects(domain=None, status=None)}
    with open(a.path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(out['riddles'])} riddles to {a.path}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_seed)

    g = sub.add_parser("generate")
    g.add_argument("--lang", default="en"); g.add_argument("--domain", default="movies")
    g.add_argument("--levels", default=""); g.add_argument("--want", type=int, default=5)
    g.set_defaults(fn=cmd_generate)

    c = sub.add_parser("coverage")
    c.add_argument("--lang", default="en"); c.add_argument("--domain", default=None)
    c.add_argument("--hi", type=int, default=8000)
    c.set_defaults(fn=cmd_coverage)

    a = sub.add_parser("accept"); a.add_argument("--all-passing", action="store_true")
    a.set_defaults(fn=cmd_accept)

    e = sub.add_parser("export"); e.add_argument("path")
    e.set_defaults(fn=cmd_export)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
