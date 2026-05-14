from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LOCOMO_DATASET_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DEFAULT_LOCOMO_CACHE_PATH = Path(__file__).resolve().parents[2] / "benchmark-data" / "locomo10.json"
CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
_SESSION_RE = re.compile(r"^session_(\d+)$")


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_locomo_tiny_fixture(path: str | Path) -> dict[str, Any]:
    data = _read_json(path)
    if data.get("dataset") != "locomo_tiny":
        raise ValueError("Expected locomo_tiny fixture")
    if not isinstance(data.get("conversations"), list):
        raise ValueError("Fixture conversations must be a list")
    if not isinstance(data.get("questions"), list):
        raise ValueError("Fixture questions must be a list")
    return data


def download_locomo_dataset(dataset_url: str = DEFAULT_LOCOMO_DATASET_URL, cache_path: str | Path = DEFAULT_LOCOMO_CACHE_PATH) -> Path:
    target = Path(cache_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(dataset_url, timeout=60) as response:
        payload = response.read()
    target.write_bytes(payload)
    return target


def parse_conversation_indices(value: str | Iterable[int] | None) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in {"all", "*"}:
            return None
        return [int(part.strip()) for part in raw.split(",") if part.strip()]
    return [int(item) for item in value]


def _category_name(value: Any) -> str:
    try:
        return CATEGORY_NAMES.get(int(value), f"category-{value}")
    except (TypeError, ValueError):
        return str(value or "unknown")


def _session_sort_key(key: str) -> int:
    match = _SESSION_RE.match(key)
    return int(match.group(1)) if match else 0


def _speaker_role(speaker: str, conversation: dict[str, Any]) -> str:
    normalized = str(speaker or "").strip().lower()
    speaker_a = str(conversation.get("speaker_a") or "").strip().lower()
    speaker_b = str(conversation.get("speaker_b") or "").strip().lower()
    if normalized in {"speaker_a", "a", "user"} or (speaker_a and normalized == speaker_a):
        return "user"
    if normalized in {"speaker_b", "b", "assistant"} or (speaker_b and normalized == speaker_b):
        return "assistant"
    return "user"


def _normalize_evidence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_real_locomo(raw_data: Any, dataset: str, conversation_indices: list[int] | None, max_questions: int | None) -> dict[str, Any]:
    if not isinstance(raw_data, list):
        raise ValueError("LoCoMo dataset must be a list of conversations")

    selected = set(conversation_indices) if conversation_indices is not None else None
    conversations: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    question_cap = max_questions if max_questions is not None and max_questions >= 0 else None

    for index, entry in enumerate(raw_data):
        if selected is not None and index not in selected:
            continue
        if not isinstance(entry, dict):
            continue
        conversation = entry.get("conversation") if isinstance(entry.get("conversation"), dict) else {}
        conversation_id = str(entry.get("sample_id") or f"locomo-conv-{index}")
        user_id = f"benchmark-{dataset}-{index}"
        sessions: list[dict[str, Any]] = []

        for session_id in sorted((key for key in conversation if _SESSION_RE.match(key)), key=_session_sort_key):
            raw_turns = conversation.get(session_id)
            if not isinstance(raw_turns, list):
                continue
            messages: list[dict[str, Any]] = []
            source_ids: list[str] = []
            for turn in raw_turns:
                if not isinstance(turn, dict):
                    continue
                text = " ".join(str(turn.get("text") or "").split())
                if not text:
                    continue
                speaker = str(turn.get("speaker") or "").strip()
                source_id = str(turn.get("dia_id") or "").strip()
                message = {
                    "role": _speaker_role(speaker, conversation),
                    "content": f"{speaker}: {text}" if speaker else text,
                }
                if source_id:
                    message["source_id"] = source_id
                    source_ids.append(source_id)
                messages.append(message)
            if messages:
                sessions.append(
                    {
                        "session_id": session_id,
                        "date": conversation.get(f"{session_id}_date_time"),
                        "messages": messages,
                        "source_ids": source_ids,
                    }
                )

        conversations.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "sessions": sessions,
            }
        )

        raw_questions = entry.get("qa") if isinstance(entry.get("qa"), list) else entry.get("qa_pairs")
        if not isinstance(raw_questions, list):
            raw_questions = []
        for question_index, question in enumerate(raw_questions, start=1):
            if question_cap is not None and len(questions) >= question_cap:
                break
            if not isinstance(question, dict):
                continue
            question_text = str(question.get("question") or "").strip()
            if not question_text:
                continue
            questions.append(
                {
                    "question_id": f"{conversation_id}-q-{question_index}",
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "question": question_text,
                    "category": _category_name(question.get("category")),
                    "evidence": _normalize_evidence(question.get("evidence")),
                }
            )

    return {"dataset": dataset, "conversations": conversations, "questions": questions}


def load_locomo_real_fixture(
    path: str | Path,
    dataset: str = "locomo10",
    conversation_indices: str | Iterable[int] | None = None,
    max_questions: int | None = None,
) -> dict[str, Any]:
    return _normalize_real_locomo(
        _read_json(path),
        dataset=dataset,
        conversation_indices=parse_conversation_indices(conversation_indices),
        max_questions=max_questions,
    )
