"""Provider interfaces. `core` never imports these; everything else goes
through them, so an implementation can be swapped without touching callers."""


class Generator:
    """Produces structured JSON from a prompt."""

    name = "abstract"

    def json(self, messages, schema, schema_name, model=None, temperature=0.8):
        raise NotImplementedError

    def text(self, messages, model=None, temperature=0.7, max_tokens=600):
        raise NotImplementedError

    def models(self):
        raise NotImplementedError


class DictionaryProvider:
    def lookup(self, word, lang):
        raise NotImplementedError


class ImageProvider:
    def search(self, query, limit=4):
        raise NotImplementedError
