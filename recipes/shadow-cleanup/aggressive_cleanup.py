#!/usr/bin/env python3
"""Aggressive second-brain cleanup proposal pipeline for OB1 thoughts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests

VERSION = "1.2"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / ".local" / "open-brain-cleanup" / "aggressive"
LOG_PATH = Path(__file__).resolve().parent / "_aggressive-cleanup.log"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_COMPRESSION_SYNC_LOG = OUTPUT_ROOT / "apply" / "compression-sync-log.json"

SUPABASE_URL = ""
SUPABASE_SERVICE_ROLE_KEY = ""
OPENROUTER_API_KEY = ""

NOISE_PATTERNS: tuple[tuple[str, int], ...] = (
    ("speaker diarization", 9),
    ("speaker identification", 9),
    ("conversational cues", 7),
    ("canonical names", 6),
    ("alias resolved", 6),
    ("sync log", 7),
    ("chunk", 5),
    ("importer", 6),
    ("parser", 6),
    ("jsonl", 6),
    ("stderr", 7),
    ("stdout", 7),
    ("exit code", 7),
    ("pytest", 6),
    ("tests passed", 6),
    ("smoke test", 5),
    ("process is running", 6),
    ("worker", 5),
    ("subagent", 5),
    ("function", 4),
    ("line number", 5),
    ("cli flag", 5),
    ("parameter", 3),
    ("schema", 3),
    ("this session", 5),
    ("the script", 5),
)

DURABLE_PATTERNS: tuple[tuple[str, int], ...] = (
    ("user prefers", 8),
    ("user wants", 7),
    ("long-running", 6),
    ("goal", 5),
    ("preference", 5),
    ("constraint", 5),
    ("architecture", 5),
    ("decision", 4),
    ("workflow", 4),
    ("project", 3),
    ("business", 4),
    ("health", 5),
    ("tax", 5),
    ("fiscal", 5),
    ("self-host", 4),
)

TEMP_MARKERS = ("\\temp", "/tmp", "appdata\\local\\temp", "codex", "claude-code")
DROP_LABELS = {"DROP_NOISE", "DROP_STALE", "DROP_IMPLEMENTATION", "DROP_DUPLICATE"}
KEEP_LABELS = {"KEEP_CORE", "KEEP_REFERENCE"}
VALID_LABELS = KEEP_LABELS | DROP_LABELS | {"COMPRESS", "ESCALATE"}
DEFAULT_MIN_DROP_CONFIDENCE = 0.92
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
GOLDLIST_TERMS = (
    "vocality",
    "skool",
    "opera",
    "audition",
    "stripe",
    "preply migration",
    "eur pivot",
    "sepa",
    "fidelis",
    "mrr",
    "copyright restructuring",
    "cass plateau",
    "e-factura",
    "melocchi",
    "lamperti",
    "passaggio",
    "vocalis",
    "thyroarytenoid",
    "cricothyroid",
    "vowel migration",
    "sovt",
    "glottal seal",
    "sas framework",
    "banned lexicon",
    "mechanical voice os",
    "savior reflex",
    "hypervigilance",
    "parentification",
    "70% rule",
    "future-self",
    "medical noir",
    "void strategy",
    "pushback protocol",
    "flywheel",
    "kontext",
    "tokenomy",
    "mastermind",
    "smac",
    "transcriptor v2",
    "omniroute",
    "convertor",
    "finance app",
    "pfa contabilitate",
)


class AggressiveCleanupError(RuntimeError):
    """Plain-language failure for CLI output."""


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def setup_logging(path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def normalize_supabase_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    if value.endswith("/rest/v1"):
        value = value[: -len("/rest/v1")]
    return value.rstrip("/")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def refresh_env() -> None:
    global SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY
    SUPABASE_URL = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def require_supabase_env() -> None:
    missing = [name for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not os.environ.get(name)]
    if missing:
        raise AggressiveCleanupError("Missing required environment variables: " + ", ".join(missing))


def require_openrouter_env() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise AggressiveCleanupError("Missing required environment variable: OPENROUTER_API_KEY")


def ensure_output_dirs(root: Path) -> None:
    for name in ("snapshots", "packs", "proposals", "reports"):
        (root / name).mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, dict) else default


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def text_hash(text: str) -> str:
    normalized = " ".join(str(text or "").strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit is not None and len(records) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def count_jsonl(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def supabase_headers(prefer: str = "return=representation") -> dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def fetch_thoughts(source: str, page_size: int, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    offset = 0
    while True:
        if limit is not None and len(rows) >= limit:
            break
        take = page_size
        if limit is not None:
            take = min(take, limit - len(rows))
        params = {"select": "id,content,metadata,created_at,updated_at"}
        if source != "all":
            params["metadata->>source"] = f"eq.{source}"
        response = requests.get(
            base,
            headers={**supabase_headers(), "Range": f"{offset}-{offset + take - 1}"},
            params=params,
            timeout=120,
        )
        if response.status_code not in (200, 206):
            raise AggressiveCleanupError(f"Supabase snapshot request failed with HTTP {response.status_code}.")
        batch = response.json()
        if not isinstance(batch, list):
            raise AggressiveCleanupError("Supabase snapshot response had an unexpected shape.")
        rows.extend(batch)
        if len(batch) < take:
            break
        offset += take
    return rows


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_thoughts_by_ids(ids: list[str], chunk_size: int = 100) -> list[dict[str, Any]]:
    valid_ids = [thought_id for thought_id in dict.fromkeys(ids) if is_valid_thought_id(thought_id)]
    rows: list[dict[str, Any]] = []
    if not valid_ids:
        return rows
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    for batch_ids in chunked(valid_ids, chunk_size):
        response = requests.get(
            base,
            headers=supabase_headers(),
            params={"select": "id,content,metadata,created_at,updated_at", "id": f"in.({','.join(batch_ids)})"},
            timeout=120,
        )
        if response.status_code != 200:
            raise AggressiveCleanupError(f"Supabase fetch-by-id request failed with HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, list):
            raise AggressiveCleanupError("Supabase fetch-by-id response had an unexpected shape.")
        rows.extend(payload)
    return rows


def delete_thoughts_by_ids(ids: list[str], chunk_size: int = 100) -> int:
    valid_ids = [thought_id for thought_id in dict.fromkeys(ids) if is_valid_thought_id(thought_id)]
    deleted = 0
    if not valid_ids:
        return deleted
    base = f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts"
    for batch_ids in chunked(valid_ids, chunk_size):
        response = requests.delete(
            base,
            headers=supabase_headers(prefer="return=representation"),
            params={"select": "id", "id": f"in.({','.join(batch_ids)})"},
            timeout=120,
        )
        if response.status_code not in (200, 204):
            raise AggressiveCleanupError(f"Supabase delete request failed with HTTP {response.status_code}.")
        if response.status_code == 204 or not response.text.strip():
            deleted += len(batch_ids)
            continue
        payload = response.json()
        if isinstance(payload, list):
            deleted += len(payload)
    return deleted


def http_post_with_retry(url: str, headers: dict[str, str], body: dict[str, Any], retries: int = 2) -> requests.Response:
    for attempt in range(retries + 1):
        response = requests.post(url, headers=headers, json=body, timeout=120)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(3 * (attempt + 1))
            continue
        return response
    raise AggressiveCleanupError("HTTP request failed after retries.")


def generate_embedding(text: str) -> list[float]:
    response = http_post_with_retry(
        f"{OPENROUTER_BASE}/embeddings",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        body={"model": EMBEDDING_MODEL, "input": str(text or "")[:8000]},
        retries=2,
    )
    if response.status_code != 200:
        raise AggressiveCleanupError(f"Embedding request failed with HTTP {response.status_code}.")
    try:
        embedding = response.json()["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AggressiveCleanupError("Embedding response had an unexpected shape.") from exc
    if not isinstance(embedding, list):
        raise AggressiveCleanupError("Embedding response had an unexpected shape.")
    return embedding


def insert_thought(content: str, metadata: dict[str, Any]) -> str:
    embedding = generate_embedding(content)
    response = requests.post(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts?select=id",
        headers=supabase_headers(prefer="return=representation"),
        json={"content": content, "embedding": embedding, "metadata": metadata},
        timeout=120,
    )
    if response.status_code not in (200, 201):
        raise AggressiveCleanupError(f"Compressed memory insert failed with HTTP {response.status_code}.")
    try:
        payload = response.json()
        thought_id = payload[0]["id"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AggressiveCleanupError("Compressed memory insert did not return a thought id.") from exc
    return str(thought_id)


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def normalized_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def is_valid_thought_id(value: str) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def contains_goldlist_term(record: dict[str, Any]) -> bool:
    text = normalized_text(str(record.get("content") or ""))
    meta = metadata(record)
    meta_text = normalized_text(json.dumps(meta, ensure_ascii=False))
    combined = f"{text} {meta_text}"
    return any(term in combined for term in GOLDLIST_TERMS)


def noise_score(record: dict[str, Any]) -> int:
    text = normalized_text(str(record.get("content") or ""))
    meta = metadata(record)
    score = 0
    for pattern, weight in NOISE_PATTERNS:
        if pattern in text:
            score += weight
    for pattern, weight in DURABLE_PATTERNS:
        if pattern in text:
            score -= weight
    row_type = str(meta.get("type") or "").lower()
    if row_type in {"decision", "context", "learning", "preference"}:
        score += 1
    if row_type in {"goal", "project"}:
        score -= 4
    pathish = str(meta.get("cwd") or meta.get("claude_project") or "").lower()
    if any(marker in pathish for marker in TEMP_MARKERS):
        score += 3
    if len(text) < 90:
        score += 2
    return score


def clean_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return cleaned[:80] or "missing"


def project_key(meta: dict[str, Any]) -> str:
    value = str(meta.get("claude_project") or meta.get("cwd") or "missing")
    lower = value.lower()
    if any(marker in lower for marker in TEMP_MARKERS):
        return "temp-working-dir"
    if "subagent" in lower or "agent" in lower:
        return "generic-agent-work"
    return clean_key(value)


def pack_id_for(kind: str, cluster_key: str, items: list[dict[str, Any]]) -> str:
    ids = [str(item.get("id", "")) for item in items]
    raw = json.dumps([kind, cluster_key, ids], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_candidate_packs(
    records: list[dict[str, Any]],
    source: str = "claude_history",
    limit_rows: int = 500,
    max_items: int = 50,
    project_filter: str | None = None,
) -> list[dict[str, Any]]:
    candidates = [
        record
        for record in records
        if (source == "all" or metadata(record).get("source") == source)
        and (not project_filter or project_key(metadata(record)) == project_filter)
    ]
    candidates.sort(key=lambda record: (noise_score(record), str(record.get("updated_at") or "")), reverse=True)
    selected = candidates[:limit_rows]
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in selected:
        meta = metadata(record)
        key = f"project:{project_key(meta)}|type:{clean_key(str(meta.get('type') or 'missing'))}"
        groups.setdefault(key, []).append(record)
    packs: list[dict[str, Any]] = []
    for key, items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        for index in range(0, len(items), max_items):
            chunk = items[index : index + max_items]
            compact_items = []
            for item in chunk:
                meta = metadata(item)
                compact_items.append(
                    {
                        "id": item.get("id"),
                        "content": item.get("content") or "",
                        "type": meta.get("type"),
                        "topics": meta.get("topics") if isinstance(meta.get("topics"), list) else [],
                        "project": meta.get("claude_project") or meta.get("cwd"),
                        "noise_score": noise_score(item),
                    }
                )
            packs.append(
                {
                    "pack_id": pack_id_for("aggressive_cleanup", key, compact_items),
                    "kind": "aggressive_cleanup",
                    "cluster_key": key,
                    "record_count": len(compact_items),
                    "items": compact_items,
                }
            )
    return packs


def cleanup_policy() -> str:
    return """
You are cleaning a personal/project second-brain database. The final product is a small, useful working memory, not an archive.

Core policy:
- Current truth wins. Outdated decisions are junk unless the pivot itself prevents a future mistake.
- Apply the same rules across all sources. Source is provenance only.
- Backlog cleanup should be aggressive after backup. No manual row-by-row review.
- Hard-delete backlog junk after backup. No soft archive table.
- Keep useful entries. Delete junk. Create compressed summaries only when clearly useful.

KEEP_CORE or KEEP_REFERENCE when the row is current and useful for:
- Business/branding: current positioning, offer, audience, tone, strategy, active assets.
- North-star engine: opera funding, travel/auditions/competitions, income engine, semi-passive assets, time freedom.
- Systems: saves time, reduces friction, automates work, improves memory/retrieval, or improves content/business operations.
- Current project architecture/workflow purpose, not implementation minutiae.
- Self-model: life decisions, psychology, blind spots, how the user works/is, recurring behavior patterns, constraints.
- Relationships: recurring close/influential people, their impact, how the user changed, why the relationship matters.
- Parked-strategic plans that serve ultimate goals.
- Romanian fiscal/payment architecture and other durable reference constraints.
- Kontext log tags: SCAR, ARCH, EVO, PERF, OPEN.

DROP labels when the row is:
- Raw voice/vocal lesson chatter, unless it hits a current brand/method/north-star goldlist term.
- Diarization, speaker labeling, transcript cleanup, parser/import/sync/status/tool noise.
- Implementation detail recoverable from repo/logs: function names, line numbers, flags, tests, regex/lint trivia, one-off refactors.
- Old positioning, discarded angle, stale strategy, historical draft, abandoned low-importance plan.
- One-off people, transcript-local names, casual mentions, single-incident logistics.
- Redundant repeat of a better current memory.

Goldlist override: do not drop rows containing current load-bearing terms unless clearly duplicated by a better row. Protect Vocality, Skool, opera, audition, Stripe, Preply migration, EUR pivot, SEPA, Fidelis, MRR, copyright restructuring, CASS plateau, e-Factura, Melocchi, Lamperti, passaggio, vocalis, thyroarytenoid, cricothyroid, vowel migration, SOVT, glottal seal, SAS framework, banned lexicon, mechanical voice OS, savior reflex, hypervigilance, parentification, 70% rule, future-self, Medical Noir, Void Strategy, Pushback Protocol, flywheel, Kontext, Tokenomy, Mastermind, SMAC, Transcriptor v2, OmniRoute, Convertor, finance app, PFA contabilitate.

When uncertain, use ESCALATE sparingly. ESCALATE must stay under 2% of a batch.
""".strip()


def build_deepseek_prompt(pack: dict[str, Any]) -> str:
    public_pack = {
        "pack_id": pack.get("pack_id"),
        "cluster_key": pack.get("cluster_key"),
        "items": pack.get("items") or [],
    }
    labels = sorted(VALID_LABELS)
    return f"""
{cleanup_policy()}

Return strict JSON only. Schema:
{{
  "pack_id": "same pack id",
  "decisions": [
    {{"id": "source id", "label": "one of {labels}", "confidence": 0.0, "reason": "short non-private category reason", "canonical_group": "optional stable group name"}}
  ],
  "compressed_memories": [
    {{"content": "durable compressed memory", "source_ids": ["ids"], "type": "decision|preference|context|learning|reference|goal|project|workflow|pattern", "topics": ["short topics"], "confidence": 0.0, "reason": "short reason"}}
  ],
  "risk_notes": ["short risk notes, no raw source quotes"]
}}

Rules:
- Do not quote raw private text in reasons or risk notes.
- Every input id must have exactly one decision.
- Use DROP_NOISE for process/import/parser/status/transcript-cleanup trivia.
- Use DROP_IMPLEMENTATION for code or command detail recoverable from repo/logs.
- Use DROP_STALE for old positioning, abandoned plans, old drafts, or outdated choices.
- Use DROP_DUPLICATE when a row repeats a better current memory.
- Use COMPRESS only when several rows should become one durable summary later.
- Use KEEP_CORE for current identity, north-star, psychology, relationship, business, branding, or active project memory.
- Use KEEP_REFERENCE for durable constraints, architecture intent, systems, workflows, finance/tax/payment references.
- Use ESCALATE only for genuinely risky rows; keep it under 2%.
- compressed_memories source_ids must reference input ids only. Leave empty unless COMPRESS is necessary.

Pack:
{json.dumps(public_pack, ensure_ascii=False)}
""".strip()


def parse_deepseek_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise AggressiveCleanupError("DeepSeek response was not valid JSON.")
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise AggressiveCleanupError("DeepSeek response had an unexpected shape.")
    return parsed


def openrouter_chat(
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    retries: int,
    retry_seconds: float,
    fallback_models: list[str] | None = None,
) -> tuple[str, str]:
    models = [model] + [item for item in (fallback_models or []) if item and item != model]
    last_error = "unknown"
    for candidate in models:
        for attempt in range(retries + 1):
            response = requests.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": candidate,
                    "messages": [
                        {"role": "system", "content": "You produce strict JSON for database cleanup. No prose."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                },
                timeout=240,
            )
            if response.status_code == 200:
                try:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise AggressiveCleanupError("OpenRouter response had an unexpected shape.") from exc
                if not isinstance(content, str) or not content.strip():
                    raise AggressiveCleanupError("OpenRouter response was empty.")
                return candidate, content
            last_error = f"HTTP {response.status_code}"
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(retry_seconds)
                continue
            break
    raise AggressiveCleanupError(f"OpenRouter request failed after retries: {last_error}.")


def read_processed_pack_ids(path: Path) -> set[str]:
    processed: set[str] = set()
    if not path.exists():
        return processed
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            pack_id = row.get("pack_id")
            if isinstance(pack_id, str) and "error" not in row:
                processed.add(pack_id)
    return processed


def run_deepseek_for_pack(
    pack: dict[str, Any],
    model: str,
    max_tokens: int,
    temperature: float,
    retries: int,
    retry_seconds: float,
    fallback_models: list[str] | None,
) -> dict[str, Any]:
    actual_model, content = openrouter_chat(
        model,
        build_deepseek_prompt(pack),
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
        retry_seconds=retry_seconds,
        fallback_models=fallback_models,
    )
    proposal = parse_deepseek_json(content)
    return {
        "pack_id": pack.get("pack_id"),
        "pack_kind": pack.get("kind"),
        "cluster_key": pack.get("cluster_key"),
        "record_count": pack.get("record_count"),
        "model": actual_model,
        "requested_model": model,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "proposal": proposal,
    }


def normalize_decision_label(decision: dict[str, Any]) -> str:
    label = str(decision.get("label") or "").upper().strip()
    if label in VALID_LABELS:
        return label
    legacy_action = str(decision.get("action") or "").lower().strip()
    if legacy_action == "delete":
        return "DROP_NOISE"
    if legacy_action == "keep":
        return "KEEP_REFERENCE"
    if legacy_action == "merge":
        return "COMPRESS"
    return "ESCALATE"


def proposal_decisions(row: dict[str, Any]) -> list[dict[str, Any]]:
    proposal = row.get("proposal") or {}
    decisions = proposal.get("decisions") or []
    return [decision for decision in decisions if isinstance(decision, dict)] if isinstance(decisions, list) else []


def summarize_proposals(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"proposal_rows": len(rows), "compressed_memories": 0}
    for label in sorted(VALID_LABELS):
        summary[label] = 0
    for row in rows:
        for decision in proposal_decisions(row):
            label = normalize_decision_label(decision)
            summary[label] += 1
        proposal = row.get("proposal") or {}
        memories = proposal.get("compressed_memories") or proposal.get("canonical_memories") or []
        if isinstance(memories, list):
            summary["compressed_memories"] += len(memories)
    return summary


def drop_decision_map(rows: list[dict[str, Any]], min_confidence: float) -> dict[str, dict[str, Any]]:
    decisions_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        for decision in proposal_decisions(row):
            thought_id = str(decision.get("id") or "").strip()
            if not is_valid_thought_id(thought_id):
                continue
            label = normalize_decision_label(decision)
            try:
                confidence = float(decision.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            if label not in DROP_LABELS or confidence < min_confidence:
                continue
            existing = decisions_by_id.get(thought_id)
            if not existing or confidence > float(existing.get("confidence") or 0):
                decisions_by_id[thought_id] = {
                    "id": thought_id,
                    "label": label,
                    "confidence": confidence,
                    "reason": str(decision.get("reason") or ""),
                    "pack_id": row.get("pack_id"),
                    "model": row.get("model"),
                    "created_at": row.get("created_at"),
                }
    return decisions_by_id


def split_delete_rows(rows: list[dict[str, Any]], allow_goldlist_delete: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    to_delete: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    for row in rows:
        if contains_goldlist_term(row) and not allow_goldlist_delete:
            protected.append(row)
        else:
            to_delete.append(row)
    return to_delete, protected


def compression_decision_map(row: dict[str, Any], min_decision_confidence: float) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for decision in proposal_decisions(row):
        thought_id = str(decision.get("id") or "").strip()
        if not is_valid_thought_id(thought_id):
            continue
        if normalize_decision_label(decision) != "COMPRESS":
            continue
        try:
            confidence = float(decision.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_decision_confidence:
            continue
        decisions[thought_id] = {
            "id": thought_id,
            "confidence": confidence,
            "reason": str(decision.get("reason") or ""),
        }
    return decisions


def compressed_memory_candidates(
    rows: list[dict[str, Any]],
    min_confidence: float,
    min_decision_confidence: float,
    min_source_ids: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in rows:
        proposal = row.get("proposal") or {}
        memories = proposal.get("compressed_memories") or []
        if not isinstance(memories, list):
            continue
        compress_decisions = compression_decision_map(row, min_decision_confidence=min_decision_confidence)
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            content = str(memory.get("content") or "").strip()
            if not content:
                continue
            try:
                confidence = float(memory.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            source_ids = [str(item).strip() for item in memory.get("source_ids") or [] if is_valid_thought_id(str(item).strip())]
            delete_source_ids = [source_id for source_id in dict.fromkeys(source_ids) if source_id in compress_decisions]
            if confidence < min_confidence or len(delete_source_ids) < min_source_ids:
                continue
            digest = text_hash(content)
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            candidates.append(
                {
                    "content": content,
                    "content_hash": digest,
                    "source_ids": list(dict.fromkeys(source_ids)),
                    "delete_source_ids": delete_source_ids,
                    "type": str(memory.get("type") or "context"),
                    "topics": [str(topic) for topic in memory.get("topics") or [] if str(topic).strip()],
                    "confidence": confidence,
                    "reason": str(memory.get("reason") or ""),
                    "pack_id": row.get("pack_id"),
                    "cluster_key": row.get("cluster_key"),
                    "model": row.get("model"),
                }
            )
    return candidates


def build_compressed_metadata(candidate: dict[str, Any], batch: str) -> dict[str, Any]:
    return {
        "source": "shadow_cleanup",
        "import_mode": "aggressive_compression_apply_v1",
        "type": candidate.get("type") or "context",
        "topics": candidate.get("topics") if isinstance(candidate.get("topics"), list) else [],
        "aggressive_cleanup_pack_id": candidate.get("pack_id"),
        "aggressive_cleanup_cluster_key": candidate.get("cluster_key"),
        "aggressive_cleanup_source_ids": candidate.get("source_ids") or [],
        "aggressive_cleanup_delete_source_ids": candidate.get("delete_source_ids") or [],
        "aggressive_cleanup_reason": candidate.get("reason", ""),
        "aggressive_cleanup_confidence": candidate.get("confidence"),
        "aggressive_cleanup_model": candidate.get("model"),
        "aggressive_cleanup_batch": batch,
        "created_by": "aggressive_compression_apply_v1",
    }


def valid_proposal_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pack_id = row.get("pack_id")
                if not isinstance(pack_id, str) or pack_id in seen or row.get("error"):
                    continue
                if isinstance(row.get("proposal"), dict):
                    seen.add(pack_id)
                    rows.append(row)
    return rows


def cmd_status(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    snapshot = latest_file(args.output_root / "snapshots", "*.jsonl")
    pack_file = latest_file(args.output_root / "packs", "*.jsonl")
    proposal = latest_file(args.output_root / "proposals", "*.jsonl")
    print(f"aggressive_cleanup_version={VERSION}")
    print(f"output_root={args.output_root}")
    print(f"latest_snapshot={snapshot if snapshot else 'none'}")
    print(f"latest_snapshot_rows={count_jsonl(snapshot)}")
    print(f"latest_pack_file={pack_file if pack_file else 'none'}")
    print(f"latest_pack_count={count_jsonl(pack_file)}")
    print(f"latest_proposal_file={proposal if proposal else 'none'}")
    print(f"latest_proposal_count={count_jsonl(proposal)}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    rows = fetch_thoughts(args.source, args.page_size, args.limit_rows)
    output = args.output or args.output_root / "snapshots" / f"{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, rows)
    logging.info("snapshot source=%s rows=%s output=%s", args.source, count, output)
    print(f"snapshot_rows={count}")
    print(f"snapshot_file={output}")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    snapshot = args.snapshot or latest_file(args.output_root / "snapshots", "*.jsonl")
    if not snapshot:
        raise AggressiveCleanupError("No snapshot file found. Run snapshot first.")
    records = read_jsonl(snapshot)
    packs = build_candidate_packs(
        records,
        source=args.source,
        limit_rows=args.limit_rows,
        max_items=args.max_items,
        project_filter=args.project_filter,
    )
    output = args.output or args.output_root / "packs" / f"packs-{args.source}-{utc_stamp()}.jsonl"
    count = write_jsonl(output, packs)
    logging.info("pack source=%s records=%s packs=%s output=%s", args.source, len(records), count, output)
    print(f"pack_count={count}")
    print(f"pack_file={output}")
    return 0


def cmd_run_deepseek(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    pack_file = args.pack_file or latest_file(args.output_root / "packs", "*.jsonl")
    if not pack_file:
        raise AggressiveCleanupError("No pack file found. Run pack first.")
    output = args.output or args.output_root / "proposals" / f"proposals-{args.model.replace('/', '-')}-{utc_stamp()}.jsonl"
    processed = read_processed_pack_ids(output)
    packs = read_jsonl(pack_file)
    written = 0
    attempted = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for pack in packs:
            pack_id = pack.get("pack_id")
            if isinstance(pack_id, str) and pack_id in processed:
                continue
            if args.limit_packs and attempted >= args.limit_packs:
                break
            attempted += 1
            try:
                fallback_models = [item.strip() for item in args.fallback_model if item.strip()]
                row = run_deepseek_for_pack(
                    pack,
                    args.model,
                    args.max_tokens,
                    args.temperature,
                    args.retries,
                    args.retry_seconds,
                    fallback_models,
                )
            except Exception as exc:  # noqa: BLE001 - preserve resumable batch output.
                logging.exception("deepseek failed pack_id=%s", pack_id)
                row = {
                    "pack_id": pack_id,
                    "pack_kind": pack.get("kind"),
                    "cluster_key": pack.get("cluster_key"),
                    "record_count": pack.get("record_count"),
                    "model": args.model,
                    "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    "error": str(exc),
                }
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            written += 1
            time.sleep(args.sleep_seconds)
    logging.info("run-deepseek pack_file=%s output=%s written=%s", pack_file, output, written)
    print(f"proposal_records_written={written}")
    print(f"proposal_file={output}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    paths = args.proposals or sorted((args.output_root / "proposals").glob("*.jsonl"))
    rows = valid_proposal_rows(paths)
    summary = summarize_proposals(rows)
    label_counts = " ".join(f"{label}={summary[label]}" for label in sorted(VALID_LABELS))
    print(
        "summary "
        f"proposal_rows={summary['proposal_rows']} {label_counts} "
        f"compressed_memories={summary['compressed_memories']}"
    )
    return 0


def cmd_apply_drops(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    (args.output_root / "backups").mkdir(parents=True, exist_ok=True)
    paths = args.proposals or sorted((args.output_root / "proposals").glob("*.jsonl"))
    rows = valid_proposal_rows(paths)
    decisions = drop_decision_map(rows, min_confidence=args.min_confidence)
    candidate_ids = list(decisions.keys())
    fetched_rows = fetch_thoughts_by_ids(candidate_ids)
    fetched_ids = {str(row.get("id")) for row in fetched_rows}
    missing = len(candidate_ids) - len(fetched_ids)
    to_delete, protected = split_delete_rows(fetched_rows, allow_goldlist_delete=args.allow_goldlist_delete)
    if args.max_delete and len(to_delete) > args.max_delete:
        raise AggressiveCleanupError(
            f"Delete plan has {len(to_delete)} rows, above --max-delete {args.max_delete}. Run a smaller batch."
        )
    backup_file = args.backup_output or args.output_root / "backups" / f"drop-backup-{utc_stamp()}.jsonl"
    deleted = 0
    if args.apply and to_delete:
        backed_up = write_jsonl(backup_file, to_delete)
        if backed_up != len(to_delete):
            raise AggressiveCleanupError("Backup row count did not match delete row count.")
        deleted = delete_thoughts_by_ids([str(row.get("id")) for row in to_delete])
    logging.info(
        "apply-drops proposals=%s candidates=%s fetched=%s protected=%s delete=%s deleted=%s apply=%s backup=%s",
        len(paths),
        len(candidate_ids),
        len(fetched_rows),
        len(protected),
        len(to_delete),
        deleted,
        args.apply,
        backup_file if args.apply else "none",
    )
    print(
        "drop_plan "
        f"proposal_rows={len(rows)} candidates={len(candidate_ids)} fetched={len(fetched_rows)} "
        f"missing={missing} goldlist_skipped={len(protected)} delete_ready={len(to_delete)} "
        f"deleted={deleted} apply={args.apply} "
        f"backup_file={backup_file if args.apply else 'none'}"
    )
    return 0


def cmd_apply_compressions(args: argparse.Namespace) -> int:
    ensure_output_dirs(args.output_root)
    (args.output_root / "backups").mkdir(parents=True, exist_ok=True)
    rows = valid_proposal_rows(args.proposals or sorted((args.output_root / "proposals").glob("*.jsonl")))
    candidates = compressed_memory_candidates(
        rows,
        min_confidence=args.min_confidence,
        min_decision_confidence=args.min_decision_confidence,
        min_source_ids=args.min_source_ids,
    )
    if args.limit_memories:
        candidates = candidates[: args.limit_memories]

    requested_delete_ids = list(
        dict.fromkeys(source_id for candidate in candidates for source_id in candidate.get("delete_source_ids", []))
    )
    fetched_rows = fetch_thoughts_by_ids(requested_delete_ids)
    fetched_by_id = {str(row.get("id")): row for row in fetched_rows}
    ready_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        live_delete_ids = [source_id for source_id in candidate["delete_source_ids"] if source_id in fetched_by_id]
        if len(live_delete_ids) < args.min_source_ids:
            continue
        ready = dict(candidate)
        ready["delete_source_ids"] = live_delete_ids
        ready_candidates.append(ready)

    final_delete_ids = list(
        dict.fromkeys(source_id for candidate in ready_candidates for source_id in candidate.get("delete_source_ids", []))
    )
    final_delete_rows = [fetched_by_id[source_id] for source_id in final_delete_ids if source_id in fetched_by_id]
    if args.max_delete and len(final_delete_rows) > args.max_delete:
        raise AggressiveCleanupError(
            f"Compression delete plan has {len(final_delete_rows)} rows, above --max-delete {args.max_delete}."
        )

    sync = read_json(args.sync_log, {"version": VERSION, "compressed_hashes": {}, "batches": {}})
    compressed_hashes = sync.setdefault("compressed_hashes", {})
    backup_file = args.backup_output or args.output_root / "backups" / f"compression-backup-{utc_stamp()}.jsonl"
    inserted = 0
    reused = 0
    deleted = 0
    batch = utc_stamp()

    if args.apply and ready_candidates:
        require_openrouter_env()
        backed_up = write_jsonl(backup_file, final_delete_rows)
        if backed_up != len(final_delete_rows):
            raise AggressiveCleanupError("Compression backup row count did not match delete row count.")
        for candidate in ready_candidates:
            digest = str(candidate["content_hash"])
            if digest in compressed_hashes:
                reused += 1
                continue
            metadata = build_compressed_metadata(candidate, batch=batch)
            thought_id = insert_thought(str(candidate["content"]), metadata)
            compressed_hashes[digest] = thought_id
            inserted += 1
            write_json(args.sync_log, sync)
        deleted = delete_thoughts_by_ids(final_delete_ids)
        sync.setdefault("batches", {})[batch] = {
            "compressed_inserted": inserted,
            "compressed_reused": reused,
            "source_deleted": deleted,
            "backup_file": str(backup_file),
            "applied_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        write_json(args.sync_log, sync)

    missing = len(requested_delete_ids) - len(fetched_rows)
    logging.info(
        "apply-compressions proposals=%s candidates=%s ready=%s requested_delete=%s fetched=%s delete=%s inserted=%s reused=%s deleted=%s apply=%s backup=%s",
        len(rows),
        len(candidates),
        len(ready_candidates),
        len(requested_delete_ids),
        len(fetched_rows),
        len(final_delete_rows),
        inserted,
        reused,
        deleted,
        args.apply,
        backup_file if args.apply else "none",
    )
    print(
        "compression_plan "
        f"proposal_rows={len(rows)} candidates={len(candidates)} ready={len(ready_candidates)} "
        f"requested_delete_ids={len(requested_delete_ids)} fetched={len(fetched_rows)} missing={missing} "
        f"delete_ready={len(final_delete_rows)} compressed_inserted={inserted} compressed_reused={reused} "
        f"deleted={deleted} apply={args.apply} backup_file={backup_file if args.apply else 'none'}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggressive OB1 second-brain cleanup proposal pipeline.")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--version", action="version", version=f"aggressive-cleanup {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show local aggressive cleanup artifacts.")
    status.set_defaults(func=cmd_status)

    snapshot = subparsers.add_parser("snapshot", help="Export source thoughts from Supabase.")
    snapshot.add_argument("--source", default="claude_history")
    snapshot.add_argument("--limit-rows", type=int)
    snapshot.add_argument("--page-size", type=int, default=1000)
    snapshot.add_argument("--output", type=Path)
    snapshot.set_defaults(func=cmd_snapshot)

    pack = subparsers.add_parser("pack", help="Build high-noise candidate packs from a snapshot.")
    pack.add_argument("--snapshot", type=Path)
    pack.add_argument("--source", default="claude_history")
    pack.add_argument("--limit-rows", type=int, default=500)
    pack.add_argument("--max-items", type=int, default=50)
    pack.add_argument("--project-filter", help="Only pack one normalized project key, e.g. temp-working-dir.")
    pack.add_argument("--output", type=Path)
    pack.set_defaults(func=cmd_pack)

    run = subparsers.add_parser("run-deepseek", help="Generate local cleanup proposals with DeepSeek.")
    run.add_argument("--pack-file", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--limit-packs", type=int, default=0)
    run.add_argument("--max-tokens", type=int, default=5000)
    run.add_argument("--temperature", type=float, default=0.1)
    run.add_argument("--sleep-seconds", type=float, default=0.2)
    run.add_argument("--retries", type=int, default=2)
    run.add_argument("--retry-seconds", type=float, default=20.0)
    run.add_argument("--fallback-model", action="append", default=[])
    run.set_defaults(func=cmd_run_deepseek)

    summarize = subparsers.add_parser("summarize", help="Summarize proposal label counts.")
    summarize.add_argument("proposals", nargs="*", type=Path)
    summarize.set_defaults(func=cmd_summarize)

    apply_drops = subparsers.add_parser("apply-drops", help="Backup and hard-delete high-confidence drop-label rows.")
    apply_drops.add_argument("proposals", nargs="*", type=Path)
    apply_drops.add_argument("--apply", action="store_true", help="Mutate Supabase. Without this, only prints a plan.")
    apply_drops.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_DROP_CONFIDENCE)
    apply_drops.add_argument("--max-delete", type=int, default=500)
    apply_drops.add_argument("--backup-output", type=Path)
    apply_drops.add_argument("--allow-goldlist-delete", action="store_true")
    apply_drops.set_defaults(func=cmd_apply_drops)

    apply_compressions = subparsers.add_parser(
        "apply-compressions", help="Insert compressed canonical memories, back up originals, then hard-delete compressed source rows."
    )
    apply_compressions.add_argument("proposals", nargs="*", type=Path)
    apply_compressions.add_argument("--apply", action="store_true", help="Mutate Supabase. Without this, only prints a plan.")
    apply_compressions.add_argument("--min-confidence", type=float, default=0.85)
    apply_compressions.add_argument("--min-decision-confidence", type=float, default=0.85)
    apply_compressions.add_argument("--min-source-ids", type=int, default=2)
    apply_compressions.add_argument("--limit-memories", type=int, default=0)
    apply_compressions.add_argument("--max-delete", type=int, default=1000)
    apply_compressions.add_argument("--backup-output", type=Path)
    apply_compressions.add_argument("--sync-log", type=Path, default=DEFAULT_COMPRESSION_SYNC_LOG)
    apply_compressions.set_defaults(func=cmd_apply_compressions)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_logging()
    load_env_file(REPO_ROOT / ".env.local")
    load_env_file(REPO_ROOT / ".local" / "model-bakeoff" / ".env")
    refresh_env()
    if args.command in {"snapshot", "apply-drops", "apply-compressions"}:
        require_supabase_env()
    if args.command in {"run-deepseek"}:
        require_openrouter_env()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AggressiveCleanupError as exc:
        logging.error("failed %s", exc)
        print(f"error={exc}", file=sys.stderr)
        raise SystemExit(1)
