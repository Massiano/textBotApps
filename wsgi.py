"""One process serving both apps, for hosts that run a single web service.

    /          the learner app
    /studio/   the content dashboard

The studio can create and delete content, so it is gated behind STUDIO_TOKEN
when one is set. Leaving that unset in a public deployment leaves the corpus
open to anyone who guesses the path.
"""

import os

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.wrappers import Request, Response

from studio.app import app as studio_app
from web.app import app as learner_app

STUDIO_TOKEN = os.environ.get("STUDIO_TOKEN", "")
COOKIE = "cinetot_studio"


class TokenGate:
    """?token=... once, then a cookie. Crude, but it keeps the dashboard from
    being world-writable, which matters more than the elegance of the scheme."""

    def __init__(self, wrapped, token):
        self.wrapped = wrapped
        self.token = token

    def __call__(self, environ, start_response):
        if not self.token:
            return self.wrapped(environ, start_response)
        request = Request(environ)
        if request.cookies.get(COOKIE) == self.token:
            return self.wrapped(environ, start_response)
        if request.args.get("token") == self.token:
            def _set_cookie(status, headers, exc_info=None):
                headers = list(headers) + [
                    ("Set-Cookie",
                     f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")]
                return start_response(status, headers, exc_info)
            return self.wrapped(environ, _set_cookie)
        return Response("Studio locked. Append ?token=… to the URL.",
                        status=401, mimetype="text/plain")(environ, start_response)


def _redirect_bare_prefix(wrapped):
    """/studio must become /studio/ or every relative asset resolves to the
    domain root and loads the learner app's files instead."""
    def middleware(environ, start_response):
        if environ.get("PATH_INFO", "") in ("", None) and environ.get("SCRIPT_NAME"):
            target = environ["SCRIPT_NAME"] + "/"
            qs = environ.get("QUERY_STRING")
            if qs:
                target += "?" + qs
            return Response("", status=308, headers={"Location": target})(
                environ, start_response)
        return wrapped(environ, start_response)
    return middleware


app = DispatcherMiddleware(learner_app, {
    "/studio": _redirect_bare_prefix(TokenGate(studio_app, STUDIO_TOKEN)),
})

if __name__ == "__main__":
    from werkzeug.serving import run_simple
    run_simple("127.0.0.1", 5000, app, use_reloader=False)
