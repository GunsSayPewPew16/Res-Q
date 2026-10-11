"""The one model call Res-Q makes, in the shape every chat API speaks.

`rationing.py` is this module's only caller, and what it asks for is the half of a claim
that is written down rather than measured: what a shop's own words for a quantity come
to, how perishable the item is, and how pressing a shelter's need looks from the little
that is known about it. Everything the backend can compute exactly — distance, chiefly —
it computes itself, because a number that can be calculated should not be guessed at.

A key is optional, and that is the design rather than a fallback nobody tests:

- with no key configured, `available()` is False and `complete_json()` answers None
  without opening a connection, so the pass scores the claim with its own reading of the
  same fields;
- a call that fails — no network, a refused key, a body that is not JSON, a model the
  endpoint does not have — answers None the same way, so a ration is never *withheld*
  because a model was unavailable.

The key travels in one header and is never printed, written to a file or echoed back, and
a failure is reported as a bare None rather than as the endpoint's own error text, which
can name the key it rejected. It is read from `RESQ_LLM_API_KEY`, or from
`OPENAI_API_KEY` so a key already in the environment is used as it is.
"""

import json
import urllib.error
import urllib.request

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
)


def available():
    """Whether a model is configured at all, which decides how a pass reads its inputs."""
    return bool(LLM_API_KEY)


def describe():
    """How a pass read its inputs, for the answer to say out loud."""
    return {
        "mode": "llm" if available() else "local",
        "model": LLM_MODEL if available() else None,
        "note": ("quantities, perishability and need were read by " + LLM_MODEL
                 if available() else
                 "no model configured: quantities, perishability and need were read by"
                 " the pass's own tables"),
    }


def complete_json(system, user, timeout=LLM_TIMEOUT_SECONDS):
    """One chat completion, asked for as JSON, answered as a dict — or None.

    None is every failure this can have, and the caller answers all of them the same way,
    so one unreadable answer costs a single refinement rather than a request. Nothing here
    raises: a rationing pass runs inside a normal request, and a request that failed while
    asking a model a question would be a worse outcome than an answer without the model.
    """
    if not available():
        return None

    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # JSON mode: the reply *is* the object, with no prose around it to strip.
        "response_format": {"type": "json_object"},
        "temperature": LLM_TEMPERATURE,
    }).encode("utf-8")

    request = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The one place the key appears, and it is never logged.
            "Authorization": "Bearer " + LLM_API_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
