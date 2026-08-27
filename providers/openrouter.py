"""OpenRouter generator.

Free model IDs churn, so the catalogue is discovered at runtime and filtered by
whether a model actually advertises structured output. Rate limits, not money,
are the binding constraint on batch work, so per-model cooldowns are tracked
here and exposed for the worker to schedule around.
"""

import json
import re
import threading
import time

import requests

import config
from providers.base import Generator

API = "https://openrouter.ai/api/v1"
CATALOG_TTL = 60 * 30

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text):
    """Recover a JSON object from a reply that may be fenced or prefaced."""
    text = (text or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in model reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object in model reply")


class OpenRouter(Generator):
    name = "openrouter"

    def __init__(self, api_key=None):
        self.api_key = api_key if api_key is not None else config.OPENROUTER_API_KEY
        self._catalog = {"at": 0.0, "models": []}
        self._cooldown = {}          # model -> unix time it becomes usable again
        self._lock = threading.Lock()
        self.calls = 0

    # -- plumbing --------------------------------------------------------
    def _headers(self):
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": config.SITE_URL,
            "X-Title": config.SITE_NAME,
        }

    def available(self, candidates=None):
        """Candidate models not currently in cooldown, best first."""
        now = time.time()
        with self._lock:
            return [m for m in (candidates or self.models())
                    if self._cooldown.get(m, 0) <= now]

    def penalise(self, model, seconds=60):
        with self._lock:
            self._cooldown[model] = time.time() + seconds

    def cooldowns(self):
        now = time.time()
        with self._lock:
            return {m: round(t - now) for m, t in self._cooldown.items() if t > now}

    def models(self, refresh=False):
        with self._lock:
            fresh = time.time() - self._catalog["at"] < CATALOG_TTL
            if self._catalog["models"] and fresh and not refresh:
                return self._catalog["models"]
        try:
            data = requests.get(f"{API}/models", timeout=20).json().get("data", [])
        except Exception:
            return list(config.PREFERRED_MODELS)

        structured, plain = [], []
        for m in data:
            mid = m.get("id", "")
            pricing = m.get("pricing", {}) or {}
            try:
                free = mid.endswith(":free") or (
                    float(pricing.get("prompt", 1)) == 0
                    and float(pricing.get("completion", 1)) == 0)
            except (TypeError, ValueError):
                free = mid.endswith(":free")
            if not free:
                continue
            params = m.get("supported_parameters") or []
            (structured if {"structured_outputs", "response_format"} & set(params)
             else plain).append(mid)

        ordered = [m for m in config.PREFERRED_MODELS if m in structured]
        ordered += [m for m in structured if m not in ordered]
        ordered += [m for m in plain if m not in ordered]
        with self._lock:
            self._catalog = {"at": time.time(),
                             "models": ordered or list(config.PREFERRED_MODELS)}
            return self._catalog["models"]

    # -- interface -------------------------------------------------------
    def json(self, messages, schema, schema_name, model=None, temperature=0.8):
        candidates = [model] if model else self.available()
        last = None
        for mid in candidates[:6]:
            payload = {
                "model": mid, "messages": messages, "temperature": temperature,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": schema_name, "strict": True, "schema": schema}},
            }
            for attempt in (0, 1):
                try:
                    self.calls += 1
                    r = requests.post(f"{API}/chat/completions", headers=self._headers(),
                                      json=payload, timeout=config.HTTP_TIMEOUT)
                    if r.status_code == 429:
                        self.penalise(mid, 120)
                        last = RuntimeError(f"{mid}: rate limited")
                        break
                    r.raise_for_status()
                    body = r.json()
                    if "error" in body and not body.get("choices"):
                        raise RuntimeError(body["error"].get("message", "model error"))
                    return extract_json(body["choices"][0]["message"]["content"]), mid
                except Exception as e:
                    last = e
                    if attempt == 0:
                        # Some free models reject json_schema outright. Retry
                        # the same model plainly, with the schema inlined.
                        payload.pop("response_format", None)
                        payload["messages"] = messages + [{
                            "role": "system",
                            "content": "Reply with a single JSON object and nothing "
                                       "else. No markdown fence, no commentary. "
                                       "Schema: " + json.dumps(schema)}]
                    else:
                        break
        raise RuntimeError(f"all candidate models failed; last error: {last}")

    def text(self, messages, model=None, temperature=0.7, max_tokens=600):
        candidates = [model] if model else self.available()
        last = None
        for mid in candidates[:5]:
            try:
                self.calls += 1
                r = requests.post(f"{API}/chat/completions", headers=self._headers(),
                                  json={"model": mid, "messages": messages,
                                        "temperature": temperature,
                                        "max_tokens": max_tokens},
                                  timeout=config.HTTP_TIMEOUT)
                if r.status_code == 429:
                    self.penalise(mid, 120)
                    last = RuntimeError(f"{mid}: rate limited")
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"], mid
            except Exception as e:
                last = e
        raise RuntimeError(f"all candidate models failed; last error: {last}")
