"""Pluggable LLM client for summarization jobs.

Two backends:
  - OpencodeZen: OpenAI-compatible chat completions against the OpenCode Zen
    gateway (https://opencode.ai/zen/v1/chat/completions). Cloud inference,
    zero local CPU. Key is read from OPENCODE_API_KEY env or the local
    opencode auth.json (opencode.key). Never logged.
  - Ollama: local inference via http://localhost:11434/api/generate.

Selection via CONCALL_LLM_BACKEND env var ("opencode" default | "ollama").
"""

import json
import os
import time
from pathlib import Path

ZEN_URL = "https://opencode.ai/zen/v1/chat/completions"
ZEN_MODEL = os.environ.get("ZEN_MODEL", "deepseek-v4-flash-free")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")


def _load_zen_key():
    key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("ZEN_API_KEY")
    if key:
        return key
    auth = Path.home() / ".local/share/opencode/auth.json"
    try:
        data = json.loads(auth.read_text())
        key = data.get("opencode", {}).get("key")
        if key:
            return key
    except Exception:
        pass
    return None


def _call_ollama(prompt, max_tokens=700):
    import httpx
    resp = httpx.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
              "options": {"temperature": 0.2, "num_predict": max_tokens}},
        timeout=180.0,
    )
    return resp.json().get("response", "")


def _call_zen(system, user, max_tokens=1600):
    import httpx
    key = _load_zen_key()
    if not key:
        raise RuntimeError("No Zen API key found (OPENCODE_API_KEY or auth.json)")
    payload = {
        "model": ZEN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    resp = httpx.post(
        ZEN_URL,
        json=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=180.0,
    )
    if resp.status_code == 429:
        raise RuntimeError("Zen rate limited (429)")
    resp.raise_for_status()
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def generate(system, user, max_tokens=1600, retries=3):
    """Generate text. Backend chosen by CONCALL_LLM_BACKEND. Returns text."""
    backend = os.environ.get("CONCALL_LLM_BACKEND", "opencode").lower()
    for attempt in range(retries):
        try:
            if backend == "ollama":
                return _call_ollama(f"{system}\n\n{user}", max_tokens)
            return _call_zen(system, user, max_tokens)
        except Exception as e:
            if attempt >= retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")
