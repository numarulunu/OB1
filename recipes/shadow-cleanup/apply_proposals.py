#!/usr/bin/env python3
"""Apply shadow-cleanup proposals to OB1 in a guarded, non-destructive way."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("Missing dependency: requests")
    print("Install with: pip install requests")
    sys.exit(1)

VERSION = "1.0"
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_SYNC_LOG = REPO_ROOT / ".local" / "open-brain-cleanup" / "apply" / "apply-proposals-sync-log.json"
DEFAULT_RUN_LOG = SCRIPT_DIR / "_apply-proposals.log"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


class ApplyProposalsError(RuntimeError):
    """Plain-language failure for proposal application."""


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def batch_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and not key.startswith("#"):
            os.environ[key] = value.strip().strip('"').strip("'")


def normalize_supabase_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def refresh_env() -> None:
    global SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY
    SUPABASE_URL = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def require_env() -> None:
    missing = [
        name
        for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        raise ApplyProposalsError("Missing required environment variables: " + ", ".join(missing))


def text_hash(text: str) -> str:
    normalized = " ".join(str(text or "").strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def is_valid_thought_id(value: str) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def read_sync_log(path: Path) -> dict[str, Any]:
    sync = read_json(path, {"version": VERSION, "packs": {}, "canonical_hashes": {}, "stats": {}})
    sync.setdefault("version", VERSION)
    sync.setdefault("packs", {})
    sync.setdefault("canonical_hashes", {})
    sync.setdefault("stats", {})
    return sync


def processed_pack_ids(sync: dict[str, Any]) -> set[str]:
    packs = sync.get("packs", {})
    if not isinstance(packs, dict):
        return set()
    return {pack_id for pack_id, result in packs.items() if isinstance(result, dict) and result.get("ok") is True}


def load_valid_proposals(paths: list[Path]) -> list[dict[str, Any]]:
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
                if not isinstance(pack_id, str) or pack_id in seen:
                    continue
                if row.get("error") or not isinstance(row.get("proposal"), dict):
                    continue
                seen.add(pack_id)
                rows.append(row)
    return rows


def summarize_proposals(rows: list[dict[str, Any]]) -> dict[str, int]:
    canonical = 0
    archive = 0
    source_ids: set[str] = set()
    for row in rows:
        proposal = row.get("proposal") or {}
        memories = proposal.get("canonical_memories") or []
        candidates = proposal.get("archive_candidates") or []
        if isinstance(memories, list):
            canonical += len(memories)
            for memory in memories:
                if isinstance(memory, dict):
                    for source_id in memory.get("source_ids") or []:
                        if isinstance(source_id, str):
                            source_ids.add(source_id)
        if isinstance(candidates, list):
            archive += len(candidates)
    return {
        "proposal_rows": len(rows),
        "canonical_memories": canonical,
        "archive_candidates": archive,
        "source_ids": len(source_ids),
    }


def clean_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[key] = item
        elif isinstance(item, list):
            cleaned[key] = [entry for entry in item if isinstance(entry, (str, int, float, bool))]
        elif isinstance(item, dict):
            cleaned[key] = clean_json_object(item)
    return cleaned


def build_canonical_metadata(row: dict[str, Any], memory: dict[str, Any], batch: str) -> dict[str, Any]:
    metadata = clean_json_object(memory.get("metadata_patch"))
    source_ids = [str(source_id) for source_id in memory.get("source_ids") or [] if str(source_id).strip()]
    metadata.update(
        {
            "source": "shadow_cleanup",
            "import_mode": "shadow_cleanup_apply_v1",
            "type": metadata.get("type") or "context",
            "topics": metadata.get("topics") if isinstance(metadata.get("topics"), list) else [],
            "shadow_cleanup_pack_id": row.get("pack_id"),
            "shadow_cleanup_pack_kind": row.get("pack_kind"),
            "shadow_cleanup_cluster_key": row.get("cluster_key"),
            "shadow_cleanup_source_ids": source_ids,
            "shadow_cleanup_reason": memory.get("reason", ""),
            "shadow_cleanup_confidence": memory.get("confidence"),
            "shadow_cleanup_model": row.get("model"),
            "shadow_cleanup_batch": batch,
            "created_by": "shadow_cleanup_apply_v1",
        }
    )
    return metadata


def merge_archive_metadata(
    existing: dict[str, Any] | None,
    pack_id: str,
    reason: str,
    canonical_ids: list[str],
    applied_at: str,
    batch: str,
) -> dict[str, Any]:
    metadata = dict(existing or {}) if isinstance(existing, dict) else {}
    metadata["shadow_cleanup"] = {
        "status": "archive_candidate",
        "pack_id": pack_id,
        "reason": reason,
        "canonical_ids": canonical_ids,
        "applied_at": applied_at,
        "batch": batch,
        "mode": "metadata_only_non_destructive",
    }
    return metadata


def http_post_with_retry(url: str, headers: dict[str, str], body: dict[str, Any], retries: int = 2):
    for attempt in range(retries + 1):
        try:
            response = requests.post(url, headers=headers, json=body, timeout=120)
        except requests.RequestException as exc:
            if attempt >= retries:
                raise ApplyProposalsError(f"HTTP request failed: {type(exc).__name__}") from exc
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(3 * (attempt + 1))
            continue
        return response
    raise ApplyProposalsError("HTTP request failed after retries.")


def supabase_headers(prefer: str = "return=minimal") -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Prefer": prefer,
    }


def generate_embedding(text: str) -> list[float]:
    response = http_post_with_retry(
        f"{OPENROUTER_BASE}/embeddings",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        body={"model": EMBEDDING_MODEL, "input": str(text or "")[:8000]},
        retries=2,
    )
    if response.status_code != 200:
        raise ApplyProposalsError(f"Embedding request failed with HTTP {response.status_code}.")
    try:
        embedding = response.json()["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ApplyProposalsError("Embedding response had an unexpected shape.") from exc
    if not isinstance(embedding, list):
        raise ApplyProposalsError("Embedding response had an unexpected shape.")
    return embedding


def insert_canonical_memory(content: str, metadata: dict[str, Any]) -> str:
    embedding = generate_embedding(content)
    response = http_post_with_retry(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts?select=id",
        headers=supabase_headers(prefer="return=representation"),
        body={"content": content, "embedding": embedding, "metadata": metadata},
        retries=2,
    )
    if response.status_code not in (200, 201):
        raise ApplyProposalsError(f"Canonical insert failed with HTTP {response.status_code}.")
    try:
        payload = response.json()
        thought_id = payload[0]["id"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ApplyProposalsError("Canonical insert did not return a thought id.") from exc
    return str(thought_id)


def fetch_thought_metadata(thought_id: str) -> dict[str, Any]:
    encoded = quote(thought_id, safe="")
    response = requests.get(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts?id=eq.{encoded}&select=id,metadata&limit=1",
        headers=supabase_headers(),
        timeout=60,
    )
    if response.status_code != 200:
        raise ApplyProposalsError(f"Metadata fetch failed with HTTP {response.status_code}.")
    try:
        rows = response.json()
    except json.JSONDecodeError as exc:
        raise ApplyProposalsError("Metadata fetch returned invalid JSON.") from exc
    if not rows:
        raise ApplyProposalsError("Archive candidate was not found in thoughts table.")
    metadata = rows[0].get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def patch_thought_metadata(thought_id: str, metadata: dict[str, Any]) -> None:
    encoded = quote(thought_id, safe="")
    response = requests.patch(
        f"{normalize_supabase_url(SUPABASE_URL)}/rest/v1/thoughts?id=eq.{encoded}",
        headers=supabase_headers(),
        json={"metadata": metadata},
        timeout=60,
    )
    if response.status_code not in (200, 204):
        raise ApplyProposalsError(f"Archive metadata patch failed with HTTP {response.status_code}.")


def apply_row(row: dict[str, Any], sync: dict[str, Any], batch: str, mark_archives: bool) -> dict[str, Any]:
    pack_id = str(row.get("pack_id"))
    proposal = row.get("proposal") or {}
    canonical_ids: list[str] = []
    inserted = 0
    reused = 0
    archived = 0
    canonical_hashes = sync.setdefault("canonical_hashes", {})

    for memory in proposal.get("canonical_memories") or []:
        if not isinstance(memory, dict):
            continue
        content = str(memory.get("content", "")).strip()
        if not content:
            continue
        digest = text_hash(content)
        if isinstance(canonical_hashes, dict) and digest in canonical_hashes:
            canonical_ids.append(str(canonical_hashes[digest]))
            reused += 1
            continue
        metadata = build_canonical_metadata(row, memory, batch)
        thought_id = insert_canonical_memory(content, metadata)
        canonical_ids.append(thought_id)
        if isinstance(canonical_hashes, dict):
            canonical_hashes[digest] = thought_id
        inserted += 1

    if mark_archives:
        for candidate in proposal.get("archive_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            thought_id = str(candidate.get("id", "")).strip()
            if not is_valid_thought_id(thought_id):
                continue
            existing = fetch_thought_metadata(thought_id)
            merged = merge_archive_metadata(
                existing,
                pack_id=pack_id,
                reason=str(candidate.get("reason", "")),
                canonical_ids=canonical_ids,
                applied_at=utc_now(),
                batch=batch,
            )
            patch_thought_metadata(thought_id, merged)
            archived += 1

    return {"ok": True, "canonical_inserted": inserted, "canonical_reused": reused, "archive_marked": archived}


def run_plan(rows: list[dict[str, Any]], sync: dict[str, Any], limit_packs: int) -> dict[str, int]:
    done = processed_pack_ids(sync)
    pending = [row for row in rows if row.get("pack_id") not in done]
    if limit_packs:
        pending = pending[:limit_packs]
    summary = summarize_proposals(pending)
    summary["already_applied"] = len(done)
    summary["pending_rows"] = len(pending)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply shadow-cleanup proposal JSONL files to OB1.")
    parser.add_argument("proposals", nargs="+", type=Path, help="Proposal JSONL files to process.")
    parser.add_argument("--apply", action="store_true", help="Mutate Supabase. Without this, only prints a plan.")
    parser.add_argument("--limit-packs", type=int, default=0, help="Max unapplied proposal rows to process.")
    parser.add_argument("--sync-log", type=Path, default=DEFAULT_SYNC_LOG, help="Application sync log path.")
    parser.add_argument("--run-log", type=Path, default=DEFAULT_RUN_LOG, help="Operational log path.")
    parser.add_argument("--skip-archive-marking", action="store_true", help="Insert canonical rows but do not mark originals.")
    parser.add_argument("--max-consecutive-errors", type=int, default=3, help="Stop after this many consecutive pack errors.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.run_log)
    load_env_file(REPO_ROOT / ".env.local")
    load_env_file(REPO_ROOT / ".local" / "model-bakeoff" / ".env")
    refresh_env()

    rows = load_valid_proposals(args.proposals)
    sync = read_sync_log(args.sync_log)
    plan = run_plan(rows, sync, args.limit_packs)
    print(
        "plan "
        f"proposal_rows={plan['proposal_rows']} canonical_memories={plan['canonical_memories']} "
        f"archive_candidates={plan['archive_candidates']} source_ids={plan['source_ids']} "
        f"already_applied={plan['already_applied']} pending_rows={plan['pending_rows']} apply={args.apply}"
    )
    logging.info("plan %s", plan)
    if not args.apply:
        return 0

    require_env()
    done = processed_pack_ids(sync)
    pending = [row for row in rows if row.get("pack_id") not in done]
    if args.limit_packs:
        pending = pending[: args.limit_packs]
    batch = batch_id()
    stats = {"packs": 0, "canonical_inserted": 0, "canonical_reused": 0, "archive_marked": 0, "errors": 0}
    consecutive_errors = 0

    for row in pending:
        pack_id = str(row.get("pack_id"))
        try:
            result = apply_row(row, sync, batch, mark_archives=not args.skip_archive_marking)
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 - batch must preserve resume state.
            logging.exception("apply failed pack_id=%s", pack_id)
            result = {"ok": False, "error": str(exc)}
            stats["errors"] += 1
            consecutive_errors += 1
        else:
            stats["packs"] += 1
            stats["canonical_inserted"] += int(result.get("canonical_inserted", 0))
            stats["canonical_reused"] += int(result.get("canonical_reused", 0))
            stats["archive_marked"] += int(result.get("archive_marked", 0))

        sync.setdefault("packs", {})[pack_id] = {**result, "applied_at": utc_now(), "batch": batch}
        sync["stats"] = stats
        write_json(args.sync_log, sync)
        print(
            f"pack pack_id={pack_id} ok={result.get('ok') is True} "
            f"canonical_inserted={result.get('canonical_inserted', 0)} "
            f"archive_marked={result.get('archive_marked', 0)} errors={stats['errors']}"
        )
        if consecutive_errors >= args.max_consecutive_errors:
            print(f"stopped_after_consecutive_errors={consecutive_errors}")
            break

    print(
        f"stop packs={stats['packs']} canonical_inserted={stats['canonical_inserted']} "
        f"canonical_reused={stats['canonical_reused']} archive_marked={stats['archive_marked']} errors={stats['errors']}"
    )
    logging.info("stop %s", stats)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
