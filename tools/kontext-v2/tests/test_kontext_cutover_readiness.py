from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'kontext_cutover_readiness.py'


def load_module():
    spec = importlib.util.spec_from_file_location('kontext_cutover_readiness', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def service(cases: int, passed: int, mrr: float, latency: float) -> dict:
    return {
        'eval': {'summary': {'cases': cases, 'passed': passed, 'failed': cases - passed, 'first_satisfying_mrr': mrr}},
        'latency': {'avg_ms': latency},
        'tools': ['search'] * 16,
        'errors': [],
        'cases': [{'text': 'private raw memory text'}],
    }


def reliability_payload(cases: int = 12) -> dict:
    return {
        'profiles': {
            'codex': {
                'services': {
                    'kontext': service(cases, cases, 1.0, 700),
                    'mem0': service(cases, cases - (1 if cases > 12 else 0), 0.75, 900),
                },
                'comparison': {
                    'shared_any_rate': 0.75,
                    'shared_first_rate': 0.25,
                    'mean_first_overlap_mrr': 0.58,
                    'left_better_satisfying_cases': ['case-a'],
                    'right_better_satisfying_cases': [],
                    'equal_satisfying_cases': cases - 1,
                    'no_overlap_cases': ['case-b'],
                },
            }
        }
    }


def shadow_payload() -> dict:
    payload = reliability_payload(12)
    payload.update(
        {
            'ok': True,
            'mode': 'shadow_read_only_mem0_source_of_truth',
            'sync_dry_run': {
                'ok': True,
                'dry_run': True,
                'report': {'created': 0, 'updated': 0},
                'refresh_existing': {'fetched': 250, 'stale': 0},
            },
            'exact_id_freshness': {'ok': True, 'checked': 250, 'fresh': 250, 'stale': 0, 'scan_offset': 2750, 'next_scan_offset': 3000, 'total_rows': 4003},
        }
    )
    return {'mcp_reliability_eval': payload, **payload}


def shadow_payload_with_mem0_exact_fetch_failures() -> dict:
    payload = shadow_payload()
    payload["ok"] = False
    payload["sync_dry_run"] = {
        "ok": False,
        "dry_run": True,
        "error": "sync_dry_run_failed",
        "report": {"created": None, "updated": None},
        "refresh_existing": {"fetched": 214, "stale": None},
    }
    payload["exact_id_freshness"] = {
        "ok": False,
        "checked": 250,
        "fresh": 214,
        "stale": 0,
        "error": "exact_id_freshness_failed",
        "scan_offset": 0,
        "next_scan_offset": 250,
        "total_rows": 4238,
    }
    payload["mcp_reliability_eval"] = payload
    return payload


def id_set_sync_payload() -> dict:
    return {
        "ok": True,
        "mem0_visible_ids": 4238,
        "kontext_matching": 4238,
        "missing_in_kontext": 0,
        "kontext_total": 12066,
        "backup_exists": True,
        "backup_basename": "kontext_v2_before_chatgpt_gap_sync.dump",
        "latest_sync_source": "mem0-chatgpt-gap-apply",
        "latest_sync_dry_run": False,
        "mem0_exact_fetch_error_count": 36,
        "private_missing_ids": ["must-not-render"],
    }


def canary_payload(writes_applied: int = 0) -> dict:
    return {
        'ok': True,
        'initialized': True,
        'tool_count': 16,
        'search_result_count': 3,
        'fetch_checked': True,
        'project_result_count': 2,
        'project_fetch_checked': True,
        'project_timeline_checked': True,
        'category_count': 6,
        'category_memories_checked': True,
        'status_ok': True,
        'write_modes': {'save': 'dry_run', 'update': 'dry_run', 'delete': 'dry_run'},
        'writes_applied': {'save': writes_applied, 'update': 0, 'delete': 0},
        'delete_flag_type': 'delete_candidate',
        'payload_bytes': {'search': 1400, 'fetch': 1700},
    }


def judged_verification_payload(ok: bool = True) -> dict:
    return {
        'ok': ok,
        'mode': 'judged-batch-verification',
        'runs_model_calls': False,
        'actual_judged_accuracy_proven': ok,
        'summary': {
            'suite_count': 3,
            'suites_verified': 3 if ok else 2,
            'total_questions': 30 if ok else 3,
            'total_passed': 30 if ok else 2,
            'weighted_accuracy': 1.0 if ok else 0.6667,
            'total_cost_usd': 0.004605 if ok else 0.003,
            'total_usage_tokens': 25300 if ok else 19000,
        },
        'blocked_by': [] if ok else ['failed suite verification: beam1'],
    }


def test_readiness_passes_and_omits_raw_case_text():
    module = load_module()
    report = module.build_readiness_report(shadow_payload(), reliability_payload(36), canary_payload(), judged_verification_payload())
    rendered = json.dumps(report)

    assert report['ok'] is True
    assert report['generated_at'].endswith('Z')
    assert report['ready_for_user_cutover_review'] is True
    assert report['production_cutover_enabled'] is False
    assert report['status_counts'] == {'pass': 8, 'fail': 0, 'missing': 0}
    assert 'private raw memory text' not in rendered


def test_full_id_set_sync_supersedes_mem0_exact_fetch_failures_without_raw_ids():
    module = load_module()
    report = module.build_readiness_report(
        shadow_payload_with_mem0_exact_fetch_failures(),
        reliability_payload(36),
        canary_payload(),
        judged_verification_payload(),
        id_set_sync_payload(),
    )
    gates = {item["name"]: item for item in report["gates"]}
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["status_counts"] == {"pass": 9, "fail": 0, "missing": 0}
    assert gates["mem0_visible_id_set_sync"]["status"] == "pass"
    assert gates["sync_dry_run_safe"]["evidence"]["superseded_by_full_id_set_sync"] is True
    assert gates["exact_id_freshness"]["evidence"]["superseded_by_full_id_set_sync"] is True
    assert gates["mem0_visible_id_set_sync"]["evidence"]["missing_in_kontext"] == 0
    assert "must-not-render" not in rendered


def test_missing_canary_keeps_readiness_incomplete():
    module = load_module()
    report = module.build_readiness_report(shadow_payload(), reliability_payload(36), judged_verification=judged_verification_payload())
    statuses = {item['name']: item['status'] for item in report['gates']}

    assert report['ok'] is False
    assert statuses['public_mcp_canary_dry_run'] == 'missing'


def test_retrieval_gate_tracks_latency_without_making_it_a_cutover_blocker():
    module = load_module()
    payload = reliability_payload(12)
    services = payload["profiles"]["codex"]["services"]
    services["kontext"]["latency"]["avg_ms"] = 1200
    services["mem0"]["latency"]["avg_ms"] = 900

    gate = module.retrieval_gate("base_live_mcp_retrieval", payload, "codex", 12)

    assert gate["status"] == "pass"
    assert gate["evidence"]["latency_within_mem0"] is False
    assert gate["evidence"]["latency_delta_ms"] == 300


def test_missing_judged_verification_keeps_readiness_incomplete():
    module = load_module()
    report = module.build_readiness_report(shadow_payload(), reliability_payload(36), canary_payload())
    statuses = {item['name']: item['status'] for item in report['gates']}

    assert report['ok'] is False
    assert report['ready_for_user_cutover_review'] is False
    assert statuses['official_judged_accuracy'] == 'missing'


def test_failed_judged_verification_fails_readiness():
    module = load_module()
    report = module.build_readiness_report(
        shadow_payload(),
        reliability_payload(36),
        canary_payload(),
        judged_verification_payload(ok=False),
    )
    gate = {item['name']: item for item in report['gates']}['official_judged_accuracy']

    assert report['ok'] is False
    assert gate['status'] == 'fail'
    assert gate['evidence']['actual_judged_accuracy_proven'] is False
    assert gate['evidence']['blocked_by'] == ['failed suite verification: beam1']


def test_judged_micro_sample_fails_readiness_even_with_true_boolean():
    module = load_module()
    judged = judged_verification_payload(ok=True)
    judged["summary"]["total_questions"] = 3
    judged["summary"]["total_passed"] = 3
    judged["summary"]["weighted_accuracy"] = 1.0

    report = module.build_readiness_report(
        shadow_payload(),
        reliability_payload(36),
        canary_payload(),
        judged,
    )
    gate = {item['name']: item for item in report['gates']}['official_judged_accuracy']

    assert report['ok'] is False
    assert gate['status'] == 'fail'
    assert gate['evidence']['min_total_questions'] == 30


def test_canary_write_application_fails_gate():
    module = load_module()
    gate = module.canary_gate(canary_payload(writes_applied=1))

    assert gate['status'] == 'fail'
    assert gate['evidence']['writes_applied_total'] == 1


def test_health_probe_gate_blocks_unhealthy_services_without_raw_payload():
    module = load_module()

    gate = module.health_probe_gate(
        {
            "mem0": {"ok": True, "service": "ionut-memory-mcp", "secret": "must-not-render"},
            "kontext": {"ok": False, "service": "kontext-v2", "mode": "mirror_read_only", "raw": "must-not-render"},
        }
    )
    rendered = json.dumps(gate, sort_keys=True)

    assert gate["name"] == "service_health_probe"
    assert gate["status"] == "fail"
    assert gate["evidence"]["mem0"]["ok"] is True
    assert gate["evidence"]["kontext"]["ok"] is False
    assert "must-not-render" not in rendered


def test_readiness_report_can_include_health_probe_gate():
    module = load_module()

    report = module.build_readiness_report(
        shadow_payload(),
        reliability_payload(36),
        canary_payload(),
        judged_verification_payload(),
        health_probe={"mem0": {"ok": True}, "kontext": {"ok": True, "mode": "mirror_read_only"}},
    )
    gates = {gate["name"]: gate for gate in report["gates"]}

    assert report["ok"] is True
    assert report["status_counts"] == {"pass": 9, "fail": 0, "missing": 0}
    assert gates["service_health_probe"]["status"] == "pass"
