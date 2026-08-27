"""The subject table, and a starting set of films.

Letting the model pick its own subject produces obscure choices, repeated
choices, and films whose plots cannot be told in simple language at all. So
subjects are curated.

Two ratings, and they are independent:

    recognizability   would a person anywhere name this film?
    retellability     can its plot be told in ~70 words of concrete events?

Pulp Fiction scores 5 and 1: globally famous, nearly untellable — nonlinear,
dialogue-driven, no through line. A boy finds a ring and walks to a mountain
is tellable at 800 words. Fame alone is the wrong filter, which is exactly why
free model choice produces weak rounds.

`min_frontier` exists because retellability is not absolute: some plots only
become tellable above a vocabulary threshold.

`distractor_group` clusters films so wrong options are drawn from real
neighbours rather than invented — which removes fabricated titles, duplicates
of the answer, and wildly uneven difficulty in one move.

Ratings here are a starting point. The probe panel measures retellability
empirically and the dashboard writes back over these numbers; recognizability
stays hand-curated, because models know far more films than people do.
"""

from content import store

# (title, year, recognizability, retellability, min_frontier, concreteness, group)
SEED_MOVIES = [
    # --- animation: highest retellability, simple physical plots -----------
    ("The Lion King",            1994, 5, 5,  600, 5, "animation"),
    ("Finding Nemo",             2003, 5, 5,  600, 5, "animation"),
    ("Toy Story",                1995, 5, 5,  600, 5, "animation"),
    ("Frozen",                   2013, 5, 5,  600, 5, "animation"),
    ("Shrek",                    2001, 5, 4,  800, 5, "animation"),
    ("Up",                       2009, 4, 4,  900, 4, "animation"),
    ("Ratatouille",              2007, 4, 5,  800, 5, "animation"),
    ("The Jungle Book",          1967, 4, 5,  600, 5, "animation"),
    ("Cinderella",               1950, 5, 5,  500, 5, "animation"),
    ("Snow White",               1937, 5, 5,  500, 5, "animation"),
    ("Beauty and the Beast",     1991, 5, 5,  600, 5, "animation"),
    ("Spirited Away",            2001, 4, 4, 1200, 4, "animation"),

    # --- adventure and fantasy -------------------------------------------
    ("Star Wars",                1977, 5, 5,  700, 5, "space"),
    ("The Lord of the Rings",    2001, 5, 4,  900, 5, "fantasy"),
    ("Harry Potter",             2001, 5, 5,  700, 5, "fantasy"),
    ("Jurassic Park",            1993, 5, 5,  700, 5, "creature"),
    ("Indiana Jones",            1981, 4, 5,  800, 5, "adventure"),
    ("Pirates of the Caribbean", 2003, 4, 4,  900, 5, "adventure"),
    ("The Wizard of Oz",         1939, 5, 5,  600, 5, "fantasy"),
    ("Avatar",                   2009, 5, 4,  900, 5, "space"),
    ("E.T.",                     1982, 5, 5,  600, 5, "creature"),
    ("Back to the Future",       1985, 5, 4,  900, 4, "adventure"),

    # --- creature and disaster: very concrete ----------------------------
    ("Jaws",                     1975, 5, 5,  600, 5, "creature"),
    ("King Kong",                1933, 4, 5,  600, 5, "creature"),
    ("Titanic",                  1997, 5, 5,  700, 5, "disaster"),
    ("Godzilla",                 1954, 4, 5,  600, 5, "creature"),

    # --- famous but harder to tell simply --------------------------------
    ("The Matrix",               1999, 5, 3, 1800, 3, "scifi"),
    ("Forrest Gump",             1994, 5, 3, 1600, 3, "drama"),
    ("The Terminator",           1984, 5, 4, 1200, 4, "scifi"),
    ("Rocky",                    1976, 4, 5,  800, 5, "sport"),
    ("Home Alone",               1990, 5, 5,  600, 5, "comedy"),
    ("Mrs. Doubtfire",           1993, 4, 4, 1000, 4, "comedy"),
    ("The Sixth Sense",          1999, 4, 3, 1600, 3, "thriller"),
    ("Gladiator",                2000, 4, 4, 1200, 4, "historical"),
    ("Schindler's List",         1993, 4, 3, 1800, 3, "historical"),
    ("Cast Away",                2000, 4, 5,  700, 5, "survival"),
    ("The Truman Show",          1998, 4, 4, 1400, 3, "drama"),
    ("Groundhog Day",            1993, 4, 4, 1200, 3, "comedy"),
    ("Life of Pi",               2012, 3, 4, 1200, 4, "survival"),
    ("The Revenant",             2015, 3, 4, 1400, 5, "survival"),

    # --- famous, low retellability. Kept, gated high, as honest negatives.
    ("Pulp Fiction",             1994, 5, 1, 4000, 2, "crime"),
    ("Inception",                2010, 5, 2, 3000, 2, "scifi"),
    ("Fight Club",               1999, 4, 2, 3000, 2, "drama"),
    ("The Godfather",            1972, 5, 2, 2500, 3, "crime"),
    ("Interstellar",             2014, 4, 2, 2500, 3, "space"),
    ("The Shawshank Redemption", 1994, 4, 3, 2000, 3, "drama"),
]


def seed(force=False):
    """Insert the starting subjects. Idempotent."""
    if not force and store.subjects(domain="movies", status=None):
        return 0
    n = 0
    for title, year, rec, ret, minf, conc, group in SEED_MOVIES:
        store.add_subject("movies", title, year=year, recognizability=rec,
                          retellability=ret, min_frontier=minf,
                          concreteness=conc, distractor_group=group)
        n += 1
    return n


class NoSubjects(RuntimeError):
    """Raised with a message that says which of the two problems it is."""


def pick(domain, frontier, avoid=(), rng=None):
    """Choose a subject tellable at this level, avoiding recent answers.

    Weighted by recognizability × retellability, because a round fails if
    either is low: an unknown film cannot be guessed, and an untellable one
    cannot be described.
    """
    import random
    rng = rng or random.Random()

    tellable = store.subjects(domain=domain, min_frontier=frontier)
    if not tellable:
        # Two very different failures, and the old message conflated them:
        # an empty table sends you looking at levels for no reason.
        import config
        total = store.subjects(domain=domain, status=None)
        if not total:
            raise NoSubjects(
                f"no subjects in domain '{domain}' at all — the table is empty. "
                f"Run: python cli.py seed   (content db: {config.CONTENT_DB})")
        lowest = min(s["min_frontier"] for s in total)
        raise NoSubjects(
            f"{len(total)} subjects exist in '{domain}' but none is tellable at "
            f"level {frontier}; the easiest needs {lowest}. Generate at a higher "
            f"level, or lower a subject's min level in the studio.")

    pool = [s for s in tellable if s["title"] not in set(avoid)] or tellable
    weights = [max(1, s["recognizability"] * s["retellability"]) for s in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def record_retellability(subject_id, solved_fraction, weight=0.3):
    """Fold a probe-panel result back into the rating.

    The panel measures retellability directly: if independent solvers cannot
    recover a film from its riddles, that film is hard to retell. It cannot
    measure recognizability, and must not be allowed to try — models know
    vastly more films than people do.
    """
    s = store.one("SELECT retellability FROM subject WHERE id=?", (subject_id,))
    if not s:
        return
    observed = 1 + 4 * max(0.0, min(1.0, solved_fraction))
    blended = (1 - weight) * s["retellability"] + weight * observed
    store.update_subject(subject_id, retellability=int(round(blended)))
