"""
One LLM entry point for the whole app: `chat_json(messages, schema)`.

Backend chain (ARCHITECTURE.md §6): local llama-server -> Fireworks -> None.
- Each backend is tried in turn; the first that returns parseable JSON wins.
- If none is reachable/configured, returns None and the CALLER degrades
  gracefully (Signal 2 skips its filter; the Arbiter abstains). The pipeline
  must never crash because the LLM is down.

JSON is requested via OpenAI `response_format=json_schema` (llama-server enforces
it at the sampler as a grammar; Fireworks honours it too). We ALSO parse
defensively so a backend that only supports `json_object` still works.
"""
import json
import re

from config import LLM, FIREWORKS, LLM_REASONING_EFFORT, LLM_MAX_OUTPUT_TOKENS

# openai SDK is imported lazily so importing this module never hard-fails on a
# box without the package (e.g. running Signal 1 only).
_openai_import_error = None
try:
    from openai import OpenAI
except Exception as e:                                   # pragma: no cover
    OpenAI = None
    _openai_import_error = e


def _backends():
    """Yield (label, base_url, api_key, model, timeout, max_retries) in priority
    order, skipping any that are disabled or unconfigured."""
    if LLM.ENABLED:
        yield ("local", LLM.BASE_URL, LLM.API_KEY, LLM.MODEL, LLM.TIMEOUT,
               LLM.MAX_RETRIES)
    if FIREWORKS.API_KEY:                                # only if a key is set
        yield ("fireworks", FIREWORKS.BASE_URL, FIREWORKS.API_KEY,
               FIREWORKS.MODEL, FIREWORKS.TIMEOUT, FIREWORKS.MAX_RETRIES)


def configured():
    """True if at least one LLM backend would be tried (cheap, no network call).
    False means the LLM is effectively OFF (no local, no Fireworks key)."""
    return OpenAI is not None and any(True for _ in _backends())


# Label of the backend that produced the most recent chat_json() answer.
_last_backend = None


def last_backend():
    """'local' | 'fireworks' if the last chat_json() call was answered by that
    backend; None if none answered (or it wasn't called). Not thread-safe —
    intended for single-threaded callers like evaluate.py."""
    return _last_backend


def _extract_json(text):
    """Best-effort: parse text as JSON, else pull the first {...} block."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _call(base_url, api_key, model, messages, schema, timeout, max_retries):
    client = OpenAI(base_url=base_url, api_key=api_key or "sk-noauth",
                    timeout=timeout, max_retries=max_retries)
    kwargs = dict(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=LLM_MAX_OUTPUT_TOKENS,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema, "strict": True},
        },
    )
    # Reasoning models (gpt-oss) must be told to think briefly, or they burn the
    # whole token budget before emitting the JSON. Omitted for non-reasoning models.
    if LLM_REASONING_EFFORT:
        kwargs["reasoning_effort"] = LLM_REASONING_EFFORT
    resp = client.chat.completions.create(**kwargs)
    return _extract_json(resp.choices[0].message.content)


def chat_json(messages, schema, _debug=False):
    """Run `messages` through the first working backend and return a dict
    validated to be non-None, or None if every backend fails/absent.

    `schema` is a flat JSON Schema (enums + short strings — see ARCHITECTURE §3).
    """
    global _last_backend
    _last_backend = None
    if OpenAI is None:
        if _debug:
            print(f"[llm] openai SDK unavailable: {_openai_import_error}")
        return None

    for label, base_url, api_key, model, timeout, max_retries in _backends():
        try:
            out = _call(base_url, api_key, model, messages, schema, timeout,
                        max_retries)
            if out is not None:
                _last_backend = label
                return out
            if _debug:
                print(f"[llm] {label}: empty/unparseable response")
        except Exception as e:                           # connection, timeout, 4xx/5xx
            if _debug:
                print(f"[llm] {label} failed: {type(e).__name__}: {e}")
            continue
    return None


def backend_status():
    """Cheap introspection for /health and demos — does NOT make a network call
    beyond a 2s reachability probe of the local server."""
    status = {
        "openai_sdk": OpenAI is not None,
        "local_enabled": LLM.ENABLED,
        "local_url": LLM.BASE_URL,
        "local_reachable": None,
        "fireworks_configured": bool(FIREWORKS.API_KEY),
    }
    if OpenAI is not None and LLM.ENABLED:
        try:
            import urllib.request
            base = LLM.BASE_URL.rsplit("/v1", 1)[0]
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                status["local_reachable"] = (r.status == 200)
        except Exception:
            status["local_reachable"] = False
    return status
