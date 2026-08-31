"""Console logging.

The in-memory trace is only reachable once the app serves a page. When a
deployment misbehaves the logs are the first and sometimes only thing
available, so the interesting events go to stdout as well: what was asked of a
model, what came back, how long it took, and why a draft was rejected.

Hosted runtimes capture stdout, and gunicorn does not buffer it, so no extra
configuration is needed beyond calling `setup()` once.
"""

import logging
import os
import sys

LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
VERBOSE = os.environ.get("LOG_PROMPTS", "0") == "1"

_configured = False


def setup():
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)-10s %(message)s", datefmt="%H:%M:%S"))
    root = logging.getLogger("cinetot")
    root.handlers = [handler]
    root.setLevel(getattr(logging, LEVEL, logging.INFO))
    root.propagate = False
    _configured = True


def get(name):
    setup()
    return logging.getLogger(f"cinetot.{name}")


def banner():
    """One block at start-up answering the questions a broken deploy raises.

    Whether the key was picked up, which databases are in use, and whether they
    sit on a path that survives a redeploy — the last one being how a corpus
    silently disappears.
    """
    import config
    log = get("boot")
    key = config.OPENROUTER_API_KEY
    log.info("=" * 58)
    log.info("CineTot starting")
    log.info("  api key      : %s", f"set ({key[:8]}…, {len(key)} chars)" if key
             else "MISSING — generation will fall back to the fake provider")
    log.info("  learner db   : %s", config.DB_PATH)
    log.info("  content db   : %s", config.CONTENT_DB)
    log.info("  auto probe   : %s", "on" if config.PROBE_AUTOMATICALLY else "off")
    log.info("  studio token : %s",
             "set" if os.environ.get("STUDIO_TOKEN") else "NOT SET — studio is public")
    log.info("  new words    : %d-%d, shell x%.2f +%d",
             config.MIN_NEW_WORDS, config.MAX_NEW_WORDS,
             config.SHELL_FACTOR, config.SHELL_MARGIN)
    for label, path in (("content", config.CONTENT_DB), ("learner", config.DB_PATH)):
        if not str(path).startswith(("/app/data", "/data")) and os.environ.get("RAILWAY_ENVIRONMENT"):
            log.warning("  %s db is not on a mounted volume — it will be lost on redeploy",
                        label)
    log.info("=" * 58)
