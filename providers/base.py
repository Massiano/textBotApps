"""Provider interfaces. `core` never imports these; everything else goes
through them, so an implementation can be swapped without touching callers."""


import threading
import time

import logs


class Trace:
    """A ring buffer of the last N exchanges with the model.

    Without this a failing run is a silent one: you cannot tell an empty reply
    from a rate limit from a prompt the model ignored. Every implementation
    records into it, so the dashboard can show real traffic.
    """

    def __init__(self, size=40):
        self.size = size
        self.items = []
        self._lock = threading.Lock()

    def add(self, model, purpose, prompt, reply, seconds, error=None):
        log = logs.get("model")
        if error:
            log.warning("%-22s %-16s %5.1fs  FAILED: %s", model, purpose, seconds, error)
        else:
            log.info("%-22s %-16s %5.1fs  %d chars", model, purpose, seconds, len(reply or ""))
        if logs.VERBOSE:
            log.debug("PROMPT >>> %s", (prompt or "")[-1500:])
            log.debug("REPLY  <<< %s", (reply or "")[:1500])
        with self._lock:
            self.items.insert(0, {
                "at": time.strftime("%H:%M:%S"),
                "model": model, "purpose": purpose,
                "prompt": prompt[-4000:] if prompt else "",
                "reply": (reply or "")[:4000],
                "seconds": round(seconds, 2), "error": error,
            })
            del self.items[self.size:]

    def recent(self, n=15):
        with self._lock:
            return list(self.items[:n])


class Generator:
    """Produces structured JSON from a prompt."""

    name = "abstract"
    trace = None

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
