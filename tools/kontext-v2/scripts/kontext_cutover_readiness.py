from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

READ_ONLY_MARKERS = ('read_only', 'source_of_truth')
MAX_PAYLOAD_BYTES = 20_000
MIN_JUDGED_TOTAL_QUESTIONS = 30
MIN_JUDGED_WEIGHTED_ACCURACY = 0.80


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return value


def eval_summary(service: dict[str, Any]) -> dict[str, Any]:
    direct = service.get('eval_summary') if isinstance(service, dict) else None
    if isinstance(direct, dict):
        return direct
    nested = service.get('eval') if isinstance(service, dict) else None
    summary = nested.get('summary') if isinstance(nested, dict) else None
    return summary if isinstance(summary, dict) else {}


def avg_latency(service: dict[str, Any]) -> float | None:
    latency = service.get('latency') if isinstance(service, dict) else None
    value = latency.get('avg_ms') if isinstance(latency, dict) else service.get('avg_latency_ms')
    return float(value) if isinstance(value, (int, float)) else None


def profile_services(payload: dict[str, Any], profile: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    section = payload.get('mcp_reliability_eval') or payload.get('mcp_reliability') or payload
    profiles = section.get('profiles') if isinstance(section, dict) else None
    report = profiles.get(profile) if isinstance(profiles, dict) else None
    services = report.get('services') if isinstance(report, dict) else None
    if not isinstance(services, dict):
        return {}, {}, {}
    comparison = report.get('comparison') if isinstance(report.get('comparison'), dict) else {}
    return services.get('kontext') or {}, services.get('mem0') or {}, comparison


def make_gate(name: str, ok: bool, evidence: dict[str, Any], missing: bool = False) -> dict[str, Any]:
    return {'name': name, 'status': 'missing' if missing else 'pass' if ok else 'fail', 'evidence': evidence}


def full_id_set_sync_gate(id_set_sync_audit: dict[str, Any] | None) -> dict[str, Any]:
    if not id_set_sync_audit:
        return make_gate('mem0_visible_id_set_sync', False, {}, True)
    mem0_visible = int(id_set_sync_audit.get('mem0_visible_ids') or id_set_sync_audit.get('mem0_ids') or 0)
    matching = int(id_set_sync_audit.get('kontext_matching') or 0)
    missing = int(id_set_sync_audit.get('missing_in_kontext') or id_set_sync_audit.get('missing') or 0)
    total = int(id_set_sync_audit.get('kontext_total') or 0)
    backup_exists = id_set_sync_audit.get('backup_exists') is True
    exact_fetch_failures = int(id_set_sync_audit.get('mem0_exact_fetch_error_count') or 0)
    ok = (
        id_set_sync_audit.get('ok') is True
        and mem0_visible > 0
        and matching == mem0_visible
        and missing == 0
        and total >= matching
        and backup_exists
    )
    return make_gate('mem0_visible_id_set_sync', ok, {
        'mem0_visible_ids': mem0_visible,
        'kontext_matching': matching,
        'missing_in_kontext': missing,
        'kontext_total': total,
        'backup_exists': backup_exists,
        'backup_basename': id_set_sync_audit.get('backup_basename'),
        'latest_sync_source': id_set_sync_audit.get('latest_sync_source'),
        'latest_sync_dry_run': id_set_sync_audit.get('latest_sync_dry_run'),
        'mem0_exact_fetch_error_count': exact_fetch_failures,
        'proof_type': 'full_mem0_visible_id_set',
    })


def sync_gates(shadow: dict[str, Any], id_set_sync_audit: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    mode = str(shadow.get('mode') or '')
    sync = shadow.get('sync_dry_run') if isinstance(shadow.get('sync_dry_run'), dict) else {}
    report = sync.get('report') if isinstance(sync.get('report'), dict) else sync
    refresh = sync.get('refresh_existing') if isinstance(sync.get('refresh_existing'), dict) else {}
    fresh = shadow.get('exact_id_freshness') if isinstance(shadow.get('exact_id_freshness'), dict) else {}
    checked = int(fresh.get('checked') or 0)
    fresh_count = int(fresh.get('fresh') or 0)
    stale = int(fresh.get('stale') or 0)
    id_gate = full_id_set_sync_gate(id_set_sync_audit) if id_set_sync_audit else None
    id_set_ok = bool(id_gate and id_gate.get('status') == 'pass')
    exact_fetch_blocked = str(fresh.get('error') or '').strip() in {'exact_id_freshness_failed', 'mem0_fetch_unreliable'}
    sync_fetch_blocked = str(sync.get('error') or '').strip() in {'sync_dry_run_failed', 'mem0_fetch_unreliable'}
    gates = [
        make_gate('shadow_report_ok', shadow.get('ok') is True or id_set_ok, {'ok': shadow.get('ok'), 'superseded_by_full_id_set_sync': id_set_ok and shadow.get('ok') is not True}),
        make_gate('shadow_mode_read_only', bool(mode) and any(m in mode for m in READ_ONLY_MARKERS), {'mode': mode or None}, not mode),
        make_gate(
            'sync_dry_run_safe',
            (
                sync.get('ok') is True
                and sync.get('dry_run') is True
                and int(report.get('created') or 0) == 0
                and int(report.get('updated') or 0) == 0
                and int(refresh.get('stale') or 0) == 0
            )
            or (id_set_ok and sync_fetch_blocked),
            {'dry_run': sync.get('dry_run'), 'created': report.get('created'), 'updated': report.get('updated'), 'refreshed_existing_rows': refresh.get('fetched'), 'refreshed_stale_rows': refresh.get('stale'), 'superseded_by_full_id_set_sync': id_set_ok and sync_fetch_blocked},
            not sync,
        ),
        make_gate(
            'exact_id_freshness',
            fresh.get('ok') is True and checked > 0 and fresh_count == checked and stale == 0 or (id_set_ok and exact_fetch_blocked),
            {'checked': checked, 'fresh': fresh_count, 'stale': stale, 'scan_offset': fresh.get('scan_offset'), 'next_scan_offset': fresh.get('next_scan_offset'), 'total_rows': fresh.get('total_rows'), 'superseded_by_full_id_set_sync': id_set_ok and exact_fetch_blocked},
            not fresh and not id_set_ok,
        ),
    ]
    if id_gate:
        gates.append(id_gate)
    return gates


def retrieval_gate(name: str, payload: dict[str, Any], profile: str, min_cases: int) -> dict[str, Any]:
    kontext, mem0, comparison = profile_services(payload, profile)
    if not kontext or not mem0:
        return make_gate(name, False, {'profile': profile}, True)
    ks, ms = eval_summary(kontext), eval_summary(mem0)
    kl, ml = avg_latency(kontext), avg_latency(mem0)
    kp, mp = int(ks.get('passed') or 0), int(ms.get('passed') or 0)
    kc, mc = int(ks.get('cases') or 0), int(ms.get('cases') or 0)
    km = float(ks.get('first_satisfying_mrr') or 0)
    mm = float(ms.get('first_satisfying_mrr') or 0)
    ok = kc >= min_cases and mc >= min_cases and kp == kc and kp >= mp and km >= mm and kl is not None and ml is not None
    return make_gate(name, ok, {
        'profile': profile,
        'kontext': {'cases': kc, 'passed': kp, 'failed': ks.get('failed'), 'first_satisfying_mrr': km, 'avg_latency_ms': kl, 'tool_count': len(kontext.get('tools') or []), 'error_count': len(kontext.get('errors') or [])},
        'mem0': {'cases': mc, 'passed': mp, 'failed': ms.get('failed'), 'first_satisfying_mrr': mm, 'avg_latency_ms': ml, 'tool_count': len(mem0.get('tools') or []), 'error_count': len(mem0.get('errors') or [])},
        'comparison': {
            'shared_any_rate': comparison.get('shared_any_rate'),
            'shared_first_rate': comparison.get('shared_first_rate'),
            'mean_first_overlap_mrr': comparison.get('mean_first_overlap_mrr'),
            'left_better_satisfying_count': len(comparison.get('left_better_satisfying_cases') or []),
            'right_better_satisfying_count': len(comparison.get('right_better_satisfying_cases') or []),
            'equal_satisfying_cases': comparison.get('equal_satisfying_cases'),
            'no_overlap_count': len(comparison.get('no_overlap_cases') or []),
        },
        'latency_within_mem0': kl <= ml if kl is not None and ml is not None else None,
        'latency_delta_ms': round(kl - ml, 3) if kl is not None and ml is not None else None,
    })


def canary_gate(canary: dict[str, Any] | None) -> dict[str, Any]:
    if not canary:
        return make_gate('public_mcp_canary_dry_run', False, {}, True)
    modes = canary.get('write_modes') if isinstance(canary.get('write_modes'), dict) else {}
    writes = canary.get('writes_applied') if isinstance(canary.get('writes_applied'), dict) else {}
    sizes = canary.get('payload_bytes') if isinstance(canary.get('payload_bytes'), dict) else {}
    all_dry = bool(modes) and all(v == 'dry_run' for v in modes.values())
    no_writes = bool(writes) and all(v in (0, None) for v in writes.values())
    bounded = all(int(v or 0) <= MAX_PAYLOAD_BYTES for v in sizes.values())
    ok = canary.get('ok') is True and canary.get('initialized') is True and int(canary.get('tool_count') or 0) >= 16 and int(canary.get('search_result_count') or 0) > 0 and canary.get('fetch_checked') is True and canary.get('project_fetch_checked') is True and canary.get('project_timeline_checked') is True and int(canary.get('category_count') or 0) > 0 and canary.get('category_memories_checked') is True and canary.get('status_ok') is True and all_dry and no_writes and canary.get('delete_flag_type') == 'delete_candidate' and bounded
    return make_gate('public_mcp_canary_dry_run', ok, {
        'initialized': canary.get('initialized'),
        'tool_count': canary.get('tool_count'),
        'search_result_count': canary.get('search_result_count'),
        'fetch_checked': canary.get('fetch_checked'),
        'project_result_count': canary.get('project_result_count'),
        'project_fetch_checked': canary.get('project_fetch_checked'),
        'project_timeline_checked': canary.get('project_timeline_checked'),
        'category_count': canary.get('category_count'),
        'category_memories_checked': canary.get('category_memories_checked'),
        'status_ok': canary.get('status_ok'),
        'all_write_modes_dry_run': all_dry,
        'writes_applied_total': sum(int(v or 0) for v in writes.values()),
        'delete_flag_type': canary.get('delete_flag_type'),
        'payload_max_bytes': max([int(v or 0) for v in sizes.values()] or [0]),
    })


def judged_accuracy_gate(
    judged_verification: dict[str, Any] | None,
    *,
    min_total_questions: int = MIN_JUDGED_TOTAL_QUESTIONS,
    min_weighted_accuracy: float = MIN_JUDGED_WEIGHTED_ACCURACY,
) -> dict[str, Any]:
    if not judged_verification:
        return make_gate('official_judged_accuracy', False, {}, True)
    summary = judged_verification.get('summary') if isinstance(judged_verification.get('summary'), dict) else {}
    blocked_by = judged_verification.get('blocked_by') if isinstance(judged_verification.get('blocked_by'), list) else []
    total_questions = int(summary.get('total_questions') or 0)
    weighted_accuracy = float(summary.get('weighted_accuracy') or 0.0)
    ok = (
        judged_verification.get('ok') is True
        and judged_verification.get('mode') == 'judged-batch-verification'
        and judged_verification.get('actual_judged_accuracy_proven') is True
        and int(summary.get('suite_count') or 0) > 0
        and int(summary.get('suites_verified') or 0) == int(summary.get('suite_count') or 0)
        and total_questions >= int(min_total_questions or MIN_JUDGED_TOTAL_QUESTIONS)
        and weighted_accuracy >= float(min_weighted_accuracy or MIN_JUDGED_WEIGHTED_ACCURACY)
        and not blocked_by
    )
    return make_gate('official_judged_accuracy', ok, {
        'mode': judged_verification.get('mode'),
        'actual_judged_accuracy_proven': judged_verification.get('actual_judged_accuracy_proven'),
        'suite_count': summary.get('suite_count'),
        'suites_verified': summary.get('suites_verified'),
        'total_questions': summary.get('total_questions'),
        'min_total_questions': min_total_questions,
        'total_passed': summary.get('total_passed'),
        'weighted_accuracy': summary.get('weighted_accuracy'),
        'min_weighted_accuracy': min_weighted_accuracy,
        'total_cost_usd': summary.get('total_cost_usd'),
        'total_usage_tokens': summary.get('total_usage_tokens'),
        'blocked_by': blocked_by,
    })


def _health_section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    section = payload.get(name) if isinstance(payload.get(name), dict) else {}
    return {
        'ok': section.get('ok') is True,
        'service': str(section.get('service') or '')[:80],
        'mode': str(section.get('mode') or '')[:80],
    }


def health_probe_gate(health_probe: dict[str, Any] | None) -> dict[str, Any]:
    if not health_probe:
        return make_gate('service_health_probe', False, {}, True)
    mem0 = _health_section(health_probe, 'mem0')
    kontext = _health_section(health_probe, 'kontext')
    ok = mem0['ok'] and kontext['ok']
    return make_gate(
        'service_health_probe',
        ok,
        {
            'mem0': mem0,
            'kontext': kontext,
        },
    )


def build_readiness_report(
    shadow_report: dict[str, Any],
    expanded_eval: dict[str, Any] | None = None,
    canary_smoke: dict[str, Any] | None = None,
    judged_verification: dict[str, Any] | None = None,
    id_set_sync_audit: dict[str, Any] | None = None,
    profile: str = 'codex',
    min_expanded_cases: int = 36,
    min_judged_questions: int = MIN_JUDGED_TOTAL_QUESTIONS,
    min_judged_weighted_accuracy: float = MIN_JUDGED_WEIGHTED_ACCURACY,
    health_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gates = sync_gates(shadow_report, id_set_sync_audit)
    gates.append(retrieval_gate('base_live_mcp_retrieval', shadow_report, profile, 12))
    gates.append(retrieval_gate('expanded_live_mcp_retrieval', expanded_eval, profile, min_expanded_cases) if expanded_eval else make_gate('expanded_live_mcp_retrieval', False, {'profile': profile}, True))
    gates.append(canary_gate(canary_smoke))
    gates.append(
        judged_accuracy_gate(
            judged_verification,
            min_total_questions=min_judged_questions,
            min_weighted_accuracy=min_judged_weighted_accuracy,
        )
    )
    if health_probe is not None:
        gates.append(health_probe_gate(health_probe))
    counts = {'pass': 0, 'fail': 0, 'missing': 0}
    for item in gates:
        counts[item['status']] = counts.get(item['status'], 0) + 1
    ok = counts.get('fail', 0) == 0 and counts.get('missing', 0) == 0
    return {
        'ok': ok,
        'generated_at': utc_now(),
        'ready_for_user_cutover_review': ok,
        'production_cutover_enabled': False,
        'profile': profile,
        'status_counts': counts,
        'gates': gates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Build a sanitized Kontext V2 cutover-readiness audit.')
    parser.add_argument('--shadow-report', required=True)
    parser.add_argument('--expanded-eval')
    parser.add_argument('--canary-smoke')
    parser.add_argument('--judged-verification')
    parser.add_argument('--id-set-sync-audit')
    parser.add_argument('--health-probe')
    parser.add_argument('--profile', default='codex')
    parser.add_argument('--min-expanded-cases', type=int, default=36)
    parser.add_argument('--min-judged-questions', type=int, default=MIN_JUDGED_TOTAL_QUESTIONS)
    parser.add_argument('--min-judged-weighted-accuracy', type=float, default=MIN_JUDGED_WEIGHTED_ACCURACY)
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    report = build_readiness_report(
        shadow_report=load_json(args.shadow_report),
        expanded_eval=load_json(args.expanded_eval) if args.expanded_eval else None,
        canary_smoke=load_json(args.canary_smoke) if args.canary_smoke else None,
        judged_verification=load_json(args.judged_verification) if args.judged_verification else None,
        id_set_sync_audit=load_json(args.id_set_sync_audit) if args.id_set_sync_audit else None,
        profile=args.profile,
        min_expanded_cases=max(args.min_expanded_cases, 1),
        min_judged_questions=max(args.min_judged_questions, 1),
        min_judged_weighted_accuracy=max(args.min_judged_weighted_accuracy, 0.0),
        health_probe=load_json(args.health_probe) if args.health_probe else None,
    )
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
