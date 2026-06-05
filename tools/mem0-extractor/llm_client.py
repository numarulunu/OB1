from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 120


def build_chat_body(prompt: str, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }


def cache_key(prompt: str, config: LLMConfig) -> str:
    identity = json.dumps(
        {"model": config.model, "base_url": config.base_url.rstrip("/"), "prompt": prompt},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def read_cache(path: Path) -> dict[str, str]:
    cache: dict[str, str] = {}
    if not path.exists():
        return cache
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("key") or "")
            content = row.get("content")
            if key and isinstance(content, str):
                cache[key] = content
    return cache


def append_cache(path: Path, key: str, config: LLMConfig, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"key": key, "model": config.model, "base_url": config.base_url.rstrip("/"), "content": content}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def post_openai_compatible(config: LLMConfig, prompt: str) -> str:
    url = config.base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(build_chat_body(prompt, config.model), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM response did not contain choices[0].message.content") from exc


def extract_with_cache(
    prompt: str,
    config: LLMConfig,
    cache_path: str | Path,
    post_fn: Callable[[LLMConfig, str], str] = post_openai_compatible,
) -> str:
    path = Path(cache_path)
    key = cache_key(prompt, config)
    cache = read_cache(path)
    if key in cache:
        return cache[key]
    content = post_fn(config, prompt)
    append_cache(path, key, config, content)
    return content
