from __future__ import annotations

import ast
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LOCOMO_DATASET_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DEFAULT_LOCOMO_CACHE_PATH = Path(__file__).resolve().parents[2] / "benchmark-data" / "locomo10.json"
DEFAULT_LONGMEMEVAL_DATASET_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    "resolve/main/longmemeval_s_cleaned.json"
)
DEFAULT_LONGMEMEVAL_CACHE_PATH = Path(__file__).resolve().parents[2] / "benchmark-data" / "longmemeval_s_cleaned.json"
DEFAULT_BEAM_ROWS_API_URL = "https://datasets-server.huggingface.co/rows"
DEFAULT_BEAM_CACHE_DIR = Path(__file__).resolve().parents[2] / "benchmark-data"
BEAM_QUESTION_TYPES = (
    "abstention",
    "contradiction_resolution",
    "event_ordering",
    "information_extraction",
    "instruction_following",
    "knowledge_update",
    "multi_session_reasoning",
    "preference_following",
    "summarization",
    "temporal_reasoning",
)
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


def _parse_serialized_field(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(raw)
        except (TypeError, ValueError, SyntaxError):
            continue
    return value


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


def download_longmemeval_dataset(
    dataset_url: str = DEFAULT_LONGMEMEVAL_DATASET_URL,
    cache_path: str | Path = DEFAULT_LONGMEMEVAL_CACHE_PATH,
) -> Path:
    target = Path(cache_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(dataset_url, timeout=60) as response:
        payload = response.read()
    target.write_bytes(payload)
    return target


def download_beam_rows_dataset(
    beam_size: str = "1M",
    offset: int = 0,
    length: int = 1,
    cache_path: str | Path | None = None,
    rows_api_url: str = DEFAULT_BEAM_ROWS_API_URL,
) -> Path:
    normalized_size = str(beam_size or "1M").strip() or "1M"
    dataset_name = "Mohammadta/BEAM-10M" if normalized_size == "10M" else "Mohammadta/BEAM"
    target = Path(cache_path) if cache_path else DEFAULT_BEAM_CACHE_DIR / f"beam_{normalized_size}_rows_{offset}_{length}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode(
        {
            "dataset": dataset_name,
            "config": "default",
            "split": normalized_size,
            "offset": max(int(offset), 0),
            "length": max(int(length), 1),
        }
    )
    with urllib.request.urlopen(f"{rows_api_url}?{query}", timeout=120) as response:
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


def parse_question_types(value: str | Iterable[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in {"all", "*"}:
            return None
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


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
        available_source_ids: set[str] = set()

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
                    available_source_ids.add(source_id)
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
            evidence = [value for value in _normalize_evidence(question.get("evidence")) if value in available_source_ids]
            questions.append(
                {
                    "question_id": f"{conversation_id}-q-{question_index}",
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "question": question_text,
                    "category": _category_name(question.get("category")),
                    "answer": str(
                        question.get("answer")
                        or question.get("answer_text")
                        or question.get("ground_truth_answer")
                        or ""
                    ),
                    "evidence": evidence,
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


def _longmemeval_date_key(value: Any) -> tuple[int, str]:
    raw = str(value or "").strip()
    return (0, raw) if raw else (1, "")


def _normalize_longmemeval_sessions(question: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sessions = question.get("haystack_sessions") if isinstance(question.get("haystack_sessions"), list) else []
    raw_dates = question.get("haystack_dates") if isinstance(question.get("haystack_dates"), list) else []
    raw_ids = question.get("haystack_session_ids") if isinstance(question.get("haystack_session_ids"), list) else []
    sessions = []
    for index, raw_session in enumerate(raw_sessions):
        if not isinstance(raw_session, list):
            continue
        session_id = str(raw_ids[index] if index < len(raw_ids) else f"session_{index + 1}").strip()
        date = raw_dates[index] if index < len(raw_dates) else None
        messages = []
        for turn in raw_session:
            if not isinstance(turn, dict):
                continue
            content = " ".join(str(turn.get("content") or "").split())
            if not content:
                continue
            messages.append(
                {
                    "role": str(turn.get("role") or "user").strip().lower() or "user",
                    "content": content,
                    "source_id": session_id,
                }
            )
        if messages:
            sessions.append(
                {
                    "session_id": session_id,
                    "date": date,
                    "messages": messages,
                    "source_ids": [session_id],
                }
            )
    sessions.sort(key=lambda item: _longmemeval_date_key(item.get("date")))
    return sessions


def _normalize_real_longmemeval(
    raw_data: Any,
    dataset: str,
    max_questions: int | None,
    question_types: Iterable[str] | None,
) -> dict[str, Any]:
    if not isinstance(raw_data, list):
        raise ValueError("LongMemEval dataset must be a list of questions")

    type_filter = {str(value).strip() for value in (question_types or []) if str(value).strip()}
    question_cap = max_questions if max_questions is not None and max_questions >= 0 else None
    conversations: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []

    for index, row in enumerate(raw_data):
        if question_cap is not None and len(questions) >= question_cap:
            break
        if not isinstance(row, dict):
            continue
        question_type = str(row.get("question_type") or "unknown").strip() or "unknown"
        if type_filter and question_type not in type_filter:
            continue
        question_text = str(row.get("question") or "").strip()
        if not question_text:
            continue
        question_id = str(row.get("question_id") or f"longmemeval-q-{index + 1}").strip()
        user_id = f"benchmark-{dataset}-{question_id}"
        sessions = _normalize_longmemeval_sessions(row)
        if not sessions:
            continue
        conversations.append(
            {
                "conversation_id": question_id,
                "user_id": user_id,
                "sessions": sessions,
            }
        )
        questions.append(
            {
                "question_id": question_id,
                "conversation_id": question_id,
                "user_id": user_id,
                "question": question_text,
                "category": question_type,
                "answer": str(row.get("answer") or ""),
                "question_date": row.get("question_date"),
                "evidence": _normalize_evidence(row.get("answer_session_ids")),
            }
        )

    return {"dataset": dataset, "conversations": conversations, "questions": questions}


def load_longmemeval_real_fixture(
    path: str | Path,
    dataset: str = "longmemeval_s",
    max_questions: int | None = None,
    question_types: Iterable[str] | None = None,
) -> dict[str, Any]:
    return _normalize_real_longmemeval(
        _read_json(path),
        dataset=dataset,
        max_questions=max_questions,
        question_types=question_types,
    )


def _flatten_beam_source_ids(value: Any) -> list[str]:
    flattened: list[str] = []
    seen: set[str] = set()

    def add(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, dict):
            for nested in item.values():
                add(nested)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                add(nested)
            return
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            flattened.append(text)

    add(value)
    return flattened


def _unwrap_beam_turn_batches(batch_dicts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    for batch in batch_dicts:
        turns = batch.get("turns", []) if isinstance(batch, dict) else []
        flat_turns: list[dict[str, Any]] = []
        for item in turns:
            if isinstance(item, list):
                flat_turns.extend(turn for turn in item if isinstance(turn, dict))
            elif isinstance(item, dict):
                flat_turns.append(item)
        if flat_turns:
            batches.append(flat_turns)
    return batches


def _parse_beam_chat(chat_data: Any) -> list[list[dict[str, Any]]]:
    parsed = _parse_serialized_field(chat_data)
    if not parsed:
        return []
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "turns" in parsed[0]:
        return _unwrap_beam_turn_batches(parsed)
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        first = parsed[0]
        sample_value = next(iter(first.values()), None)
        is_plan_format = isinstance(sample_value, list) and sample_value and isinstance(sample_value[0], dict) and "turns" in sample_value[0]
        if is_plan_format:
            batches: list[list[dict[str, Any]]] = []
            for session in parsed:
                if not isinstance(session, dict):
                    continue
                for key in sorted(session):
                    value = session.get(key)
                    if isinstance(value, list):
                        batches.extend(_unwrap_beam_turn_batches(value))
            return batches
        if "role" in first or "content" in first:
            return [parsed]
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
        return [[turn for turn in batch if isinstance(turn, dict)] for batch in parsed]
    return []


def _beam_role(value: Any) -> str:
    role = str(value or "user").strip().lower()
    if role in {"assistant", "ai", "system"}:
        return "assistant"
    return "user"


def _beam_rows(raw_data: Any) -> list[dict[str, Any]]:
    if isinstance(raw_data, dict) and isinstance(raw_data.get("rows"), list):
        rows = raw_data["rows"]
        return [item.get("row", item) for item in rows if isinstance(item, dict)]
    if isinstance(raw_data, list):
        return [item.get("row", item) for item in raw_data if isinstance(item, dict)]
    if isinstance(raw_data, dict):
        return [raw_data]
    raise ValueError("BEAM dataset must be a row object, rows response, or list of conversations")


def _normalize_beam_sessions(row: dict[str, Any]) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for batch_index, turns in enumerate(_parse_beam_chat(row.get("chat", []))):
        messages: list[dict[str, Any]] = []
        source_ids: list[str] = []
        date = None
        for turn_index, turn in enumerate(turns):
            content = " ".join(str(turn.get("content") or "").split())
            if not content:
                continue
            if date is None and turn.get("time_anchor"):
                date = str(turn.get("time_anchor"))
            raw_source_id = turn.get("id")
            if raw_source_id is None or str(raw_source_id).strip() == "":
                raw_source_id = turn.get("index")
            if raw_source_id is None or str(raw_source_id).strip() == "":
                raw_source_id = f"{batch_index}:{turn_index}"
            source_id = str(raw_source_id).strip()
            message = {
                "role": _beam_role(turn.get("role")),
                "content": content,
            }
            if source_id:
                message["source_id"] = source_id
                source_ids.append(source_id)
            messages.append(message)
        if messages:
            sessions.append(
                {
                    "session_id": f"batch_{batch_index}",
                    "date": date,
                    "messages": messages,
                    "source_ids": source_ids,
                }
            )
    return sessions


def _normalize_real_beam(
    raw_data: Any,
    dataset: str,
    conversation_indices: list[int] | None,
    max_questions: int | None,
    question_types: Iterable[str] | None,
) -> dict[str, Any]:
    selected = set(conversation_indices) if conversation_indices is not None else None
    type_filter = {str(value).strip() for value in (question_types or []) if str(value).strip()}
    question_cap = max_questions if max_questions is not None and max_questions >= 0 else None
    conversations: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []

    for index, row in enumerate(_beam_rows(raw_data)):
        if selected is not None and index not in selected:
            continue
        if not isinstance(row, dict):
            continue
        conversation_id = str(row.get("conversation_id") or f"{dataset}-conv-{index}").strip()
        user_id = f"benchmark-{dataset}-{conversation_id}"
        sessions = _normalize_beam_sessions(row)
        conversations.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "sessions": sessions,
            }
        )

        probing = _parse_serialized_field(row.get("probing_questions", {}))
        if not isinstance(probing, dict):
            probing = {}
        ordered_types = [item for item in BEAM_QUESTION_TYPES if item in probing]
        ordered_types.extend(sorted(set(probing) - set(ordered_types)))
        for question_type in ordered_types:
            if type_filter and question_type not in type_filter:
                continue
            raw_questions = probing.get(question_type, [])
            if isinstance(raw_questions, dict):
                raw_questions = [raw_questions]
            if not isinstance(raw_questions, list):
                continue
            for question_index, question in enumerate(raw_questions, start=1):
                if question_cap is not None and len(questions) >= question_cap:
                    break
                if isinstance(question, str):
                    question = {"question": question}
                if not isinstance(question, dict):
                    continue
                question_text = str(question.get("question") or question.get("question_text") or "").strip()
                if not question_text:
                    continue
                answer = str(
                    question.get("answer")
                    or question.get("ideal_answer")
                    or question.get("ideal_response")
                    or question.get("expected_compliance")
                    or ""
                )
                questions.append(
                    {
                        "question_id": f"{conversation_id}-q-{len(questions) + 1}-{question_type}",
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "question": question_text,
                        "category": question_type,
                        "answer": answer,
                        "difficulty": question.get("difficulty"),
                        "rubric": question.get("rubric") if isinstance(question.get("rubric"), list) else [],
                        "evidence": _flatten_beam_source_ids(question.get("source_chat_ids")),
                    }
                )
            if question_cap is not None and len(questions) >= question_cap:
                break

    return {"dataset": dataset, "conversations": conversations, "questions": questions}


def load_beam_real_fixture(
    path: str | Path,
    beam_size: str = "1M",
    dataset: str | None = None,
    conversation_indices: str | Iterable[int] | None = None,
    max_questions: int | None = None,
    question_types: str | Iterable[str] | None = None,
) -> dict[str, Any]:
    normalized_size = str(beam_size or "1M").strip() or "1M"
    return _normalize_real_beam(
        _read_json(path),
        dataset=dataset or f"beam_{normalized_size}",
        conversation_indices=parse_conversation_indices(conversation_indices),
        max_questions=max_questions,
        question_types=parse_question_types(question_types),
    )
