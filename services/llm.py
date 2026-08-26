"""OpenRouter access.

Free model IDs churn constantly, so the catalogue is fetched at runtime rather
than hard-coded, and models are filtered by whether they actually advertise
structured-output support. Preference order from config is honoured where those
models are still available; anything else free is a fallback.
"""

import json
import re
import threading
import time

import requests

import config

API = "https://openrouter.ai/api/v1"
_catalog_lock = threading.Lock()
_catalog = {"at": 0.0, "models": []}
CATALOG_TTL = 60 * 30


def _headers():
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": config.SITE_NAME,
    }


def free_models(refresh=False):
    """Free models that support structured output, best first."""
    with _catalog_lock:
        fresh = time.time() - _catalog["at"] < CATALOG_TTL
        if _catalog["models"] and fresh and not refresh:
            return _catalog["models"]
        try:
            data = requests.get(f"{API}/models", timeout=20).json().get("data", [])
        except Exception:
            return list(config.PREFERRED_MODELS)

        structured, plain = [], []
        for m in data:
            mid = m.get("id", "")
            pricing = m.get("pricing", {}) or {}
            try:
                is_free = mid.endswith(":free") or (
                    float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0)
            except (TypeError, ValueError):
                is_free = mid.endswith(":free")
            if not is_free:
                continue
            params = m.get("supported_parameters") or []
            (structured if "structured_outputs" in params or "response_format" in params
             else plain).append(mid)

        ordered = [m for m in config.PREFERRED_MODELS if m in structured]
        ordered += [m for m in structured if m not in ordered]
        ordered += [m for m in config.PREFERRED_MODELS if m in plain and m not in ordered]
        ordered += [m for m in plain if m not in ordered]

        _catalog["models"] = ordered or list(config.PREFERRED_MODELS)
        _catalog["at"] = time.time()
        return _catalog["models"]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_json(text):
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


def complete_json(messages, schema, schema_name, models=None, temperature=0.8):
    """Call models in order until one returns JSON matching *schema*'s shape."""
    candidates = models or free_models()
    last = None
    for model in candidates[:6]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        for attempt in (0, 1):
            try:
                r = requests.post(f"{API}/chat/completions", headers=_headers(),
                                  json=payload, timeout=config.HTTP_TIMEOUT)
                if r.status_code == 429:
                    last = RuntimeError(f"{model}: rate limited")
                    break
                r.raise_for_status()
                body = r.json()
                if "error" in body and not body.get("choices"):
                    raise RuntimeError(body["error"].get("message", "model error"))
                content = body["choices"][0]["message"]["content"]
                result = _extract_json(content)
                return result, model
            except Exception as e:
                last = e
                if attempt == 0:
                    # Some free models reject json_schema outright. Retry the
                    # same model in plain mode with the schema inlined.
                    payload.pop("response_format", None)
                    payload["messages"] = messages + [{
                        "role": "system",
                        "content": "Reply with a single JSON object and nothing else. "
                                   "No markdown fence, no commentary. Schema: "
                                   + json.dumps(schema),
                    }]
                else:
                    break
    raise RuntimeError(f"all candidate models failed; last error: {last}")


def complete_text(messages, models=None, temperature=0.7, max_tokens=600):
    candidates = models or free_models()
    last = None
    for model in candidates[:5]:
        try:
            r = requests.post(f"{API}/chat/completions", headers=_headers(), json={
                "model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
            }, timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"], model
        except Exception as e:
            last = e
    raise RuntimeError(f"all candidate models failed; last error: {last}")
