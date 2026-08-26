"""What the learner is asked to identify.

The app is named for films, but nothing in the pipeline is film-specific: a
domain is just a subject pool plus the shape of a plausible wrong answer.
Adding one is a dict entry.
"""

DOMAINS = {
    "movies": {
        "label": "Movies",
        "subject": "a widely known feature film",
        "clue": "a spoiler-light plot summary",
        "distractor": "other real films sharing a genre, era or central character type",
        "avoid": "Do not name the film, its director, or any of its characters.",
    },
    "books": {
        "label": "Books",
        "subject": "a widely read novel or work of literature",
        "clue": "a summary of the plot and setting",
        "distractor": "other real books of similar genre or period",
        "avoid": "Do not name the book, its author, or any of its characters.",
    },
    "history": {
        "label": "Historical events",
        "subject": "a well known historical event",
        "clue": "an account of what happened and why it mattered",
        "distractor": "other real events from a comparable era or region",
        "avoid": "Do not name the event, its date, or the people involved.",
    },
    "people": {
        "label": "Famous people",
        "subject": "a globally famous person, living or historical",
        "clue": "a description of what they did and what they are remembered for",
        "distractor": "other real people from a similar field or period",
        "avoid": "Do not give the person's name or any name that identifies them.",
    },
    "songs": {
        "label": "Songs",
        "subject": "a very well known popular song",
        "clue": "a description of what the song is about and its mood",
        "distractor": "other real songs of similar era or genre",
        "avoid": "Do not quote the lyrics, name the song, or name the artist.",
    },
    "games": {
        "label": "Video games",
        "subject": "a widely played video game",
        "clue": "a description of the world, the goal and how it is played",
        "distractor": "other real games of similar genre or platform",
        "avoid": "Do not name the game, its studio, or its main character.",
    },
    "animals": {
        "label": "Animals",
        "subject": "a distinctive animal species",
        "clue": "a description of how it looks, where it lives and how it behaves",
        "distractor": "other real animals that share a habitat or body plan",
        "avoid": "Do not give the animal's common or scientific name.",
    },
    "inventions": {
        "label": "Inventions",
        "subject": "an everyday object or important invention",
        "clue": "a description of what it does and how people use it",
        "distractor": "other real objects with a related purpose",
        "avoid": "Do not name the object.",
    },
}

DEFAULT = "movies"


def get(name):
    return DOMAINS.get(name or DEFAULT, DOMAINS[DEFAULT])


def listing():
    return [{"id": k, "label": v["label"]} for k, v in DOMAINS.items()]
