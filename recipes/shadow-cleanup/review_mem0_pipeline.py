#!/usr/bin/env python3
"""Local review and Mem0 export tools for OB1 Human Memory V2 cards.

This script is local-only by default. It reads curated V2 card JSONL artifacts and
writes review/import files without mutating Supabase or Mem0 unless import-mem0 is
run with --execute.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

POLICY_VERSION = 'human-memory-v2-formative-dossiers'
MEM0_SOURCE = 'ob1-human-memory-v2'
DEFAULT_AGENT_ID = 'ob1-human-memory-v2'
DEFAULT_APP_ID = 'ob1-curated-memory'

DOMAIN_PRIORITY = {
    'family_origin': 44,
    'psychology': 42,
    'relationships': 40,
    'shadow_motives': 38,
    'identity': 36,
    'ai': 34,
    'opera': 32,
    'voice': 30,
    'vocality': 26,
    'money_execution': 24,
    'business': 22,
    'workflow': 20,
    'systems': 20,
}

TYPE_PRIORITY = {
    'identity_shaping': 32,
    'shadow_motive': 28,
    'person': 26,
    'event': 22,
    'ai_breakthrough': 22,
    'trigger': 18,
    'lesson': 14,
    'pattern': 12,
}

EMOTIONAL_PRIORITY = {
    'identity_shaping': 22,
    'formative': 18,
    'strong': 10,
    'mild': 2,
}

STATUS_PRIORITY = {
    'active': 10,
    'dormant': 6,
    'unknown': 4,
    'resolved': 2,
    'superseded': 0,
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def read_jsonl(path):
    rows = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')


def normalize_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def numeric(value, default=0):
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def stable_card_id(card, index):
    identity = {
        'index': index,
        'content': card.get('content') or '',
        'source_ids': normalize_list(card.get('source_ids')),
        'memory_type': card.get('memory_type') or '',
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    return f'ob1-v2-card-{digest[:16]}'


def card_priority(card):
    domains = [domain.lower() for domain in normalize_list(card.get('domains'))]
    domain_score = max((DOMAIN_PRIORITY.get(domain, 0) for domain in domains), default=0)
    memory_type = str(card.get('memory_type') or '').lower()
    emotional_intensity = str(card.get('emotional_intensity') or '').lower()
    current_status = str(card.get('current_status') or '').lower()
    signal_score = numeric(card.get('signal_strength')) * 10
    evidence_score = min(numeric(card.get('evidence_count')), 10)
    return (
        signal_score
        + domain_score
        + TYPE_PRIORITY.get(memory_type, 0)
        + EMOTIONAL_PRIORITY.get(emotional_intensity, 0)
        + STATUS_PRIORITY.get(current_status, 0)
        + evidence_score
    )


def select_pilot_cards(cards, limit=300, min_signal=0):
    filtered = [card for card in cards if numeric(card.get('signal_strength')) >= min_signal]
    indexed = list(enumerate(filtered, start=1))
    ranked = sorted(indexed, key=lambda pair: (-card_priority(pair[1]), pair[0]))
    if limit and limit > 0:
        ranked = ranked[:limit]
    return [card for _, card in ranked]


def card_to_mem0_request(card, index, user_id, agent_id=DEFAULT_AGENT_ID, app_id=DEFAULT_APP_ID, target='oss'):
    content = str(card.get('content') or '').strip()
    card_id = stable_card_id(card, index)
    source_ids = normalize_list(card.get('source_ids'))
    domains = normalize_list(card.get('domains'))
    people = normalize_list(card.get('people'))
    provenance = card.get('provenance') if isinstance(card.get('provenance'), dict) else {}
    metadata = {
        'source': MEM0_SOURCE,
        'ob1_card_id': card_id,
        'policy_version': POLICY_VERSION,
        'memory_type': card.get('memory_type') or '',
        'signal_strength': numeric(card.get('signal_strength')),
        'emotional_intensity': card.get('emotional_intensity') or '',
        'current_status': card.get('current_status') or '',
        'evidence_count': int(numeric(card.get('evidence_count'))),
        'domains': domains,
        'people': people,
        'source_ids': source_ids,
        'source_id_count': len(source_ids),
        'node_ids': normalize_list(provenance.get('node_ids')),
        'source_summary': provenance.get('source_summary') or '',
    }
    body = {
        'messages': [{'role': 'user', 'content': content}],
        'user_id': user_id,
        'agent_id': agent_id,
        'app_id': app_id,
        'metadata': metadata,
        'infer': False,
        'version': 'v2',
        'immutable': True,
    }
    path = '/memories' if target == 'oss' else '/v1/memories/'
    return {'method': 'POST', 'path': path, 'body': body}


def card_summary(card, index):
    return {
        'index': index,
        'card_id': stable_card_id(card, index),
        'priority': card_priority(card),
        'content': card.get('content') or '',
        'memory_type': card.get('memory_type') or 'unknown',
        'signal_strength': numeric(card.get('signal_strength')),
        'emotional_intensity': card.get('emotional_intensity') or 'unknown',
        'current_status': card.get('current_status') or 'unknown',
        'evidence_count': int(numeric(card.get('evidence_count'))),
        'domains': normalize_list(card.get('domains')),
        'people': normalize_list(card.get('people')),
        'source_ids': normalize_list(card.get('source_ids')),
        'source_id_count': len(normalize_list(card.get('source_ids'))),
        'review_reason': card.get('review_reason') or '',
        'ai_guidance': normalize_list(card.get('ai_guidance')),
        'timeline': normalize_list(card.get('timeline')),
        'incidents': normalize_list(card.get('incidents')),
        'competing_interpretations': normalize_list(card.get('competing_interpretations')),
    }


def safe_script_json(obj):
    return json.dumps(obj, ensure_ascii=False).replace('<', '\\u003c')


def dashboard_html(data):
    payload = safe_script_json(data)
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OB1 Human Memory V2 Review</title>
  <style>
    :root {{ font-family: Arial, sans-serif; color: #151515; background: #f6f6f3; }}
    body {{ margin: 0; }}
    header {{ position: sticky; top: 0; background: #ffffff; border-bottom: 1px solid #d8d8d2; padding: 14px 18px; z-index: 2; }}
    h1 {{ font-size: 20px; margin: 0 0 8px; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 10px; font-size: 13px; }}
    .summary span {{ background: #eeeeea; border: 1px solid #d8d8d2; padding: 4px 8px; border-radius: 4px; }}
    .filters {{ display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 8px; margin-top: 12px; }}
    input, select, button {{ font: inherit; border: 1px solid #bdbdb7; border-radius: 4px; padding: 7px 8px; background: #fff; }}
    button {{ cursor: pointer; }}
    main {{ padding: 18px; max-width: 1200px; margin: 0 auto; }}
    .card {{ background: #fff; border: 1px solid #d6d6cf; border-radius: 6px; padding: 14px; margin-bottom: 12px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; font-size: 12px; }}
    .meta span {{ background: #f0f0eb; border: 1px solid #ddddd6; padding: 3px 6px; border-radius: 4px; }}
    .content {{ white-space: pre-wrap; line-height: 1.45; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .selected {{ outline: 2px solid #111; }}
    @media (max-width: 760px) {{ .filters {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<header>
  <h1>OB1 Human Memory V2 Review</h1>
  <div class="summary" id="summary"></div>
  <div class="filters">
    <input id="search" placeholder="Search cards">
    <select id="domain"><option value="">All domains</option></select>
    <select id="type"><option value="">All types</option></select>
    <select id="status"><option value="">All statuses</option></select>
    <button id="export">Export decisions JSONL</button>
  </div>
</header>
<main id="cards"></main>
<script id="memory-data" type="application/json">{payload}</script>
<script>
const data = JSON.parse(document.getElementById('memory-data').textContent);
const decisions = JSON.parse(localStorage.getItem('ob1-human-v2-decisions') || '{{}}');
const cardsEl = document.getElementById('cards');
const searchEl = document.getElementById('search');
const domainEl = document.getElementById('domain');
const typeEl = document.getElementById('type');
const statusEl = document.getElementById('status');
function unique(values) {{ return [...new Set(values.filter(Boolean))].sort(); }}
function fillSelect(el, values) {{ values.forEach(v => {{ const o=document.createElement('option'); o.value=v; o.textContent=v; el.appendChild(o); }}); }}
fillSelect(domainEl, unique(data.cards.flatMap(c => c.domains || [])));
fillSelect(typeEl, unique(data.cards.map(c => c.memory_type)));
fillSelect(statusEl, unique(data.cards.map(c => c.current_status)));
function setSummary(visible) {{
  const s = data.summary;
  document.getElementById('summary').innerHTML = '';
  [['visible', visible], ['cards', s.card_count], ['delete candidates', s.delete_candidate_ids], ['held review', s.held_review_ids], ['db apply', s.db_apply]].forEach(([k,v]) => {{ const span=document.createElement('span'); span.textContent = `${{k}}: ${{v}}`; document.getElementById('summary').appendChild(span); }});
}}
function matches(card) {{
  const q = searchEl.value.toLowerCase().trim();
  if (q && !JSON.stringify(card).toLowerCase().includes(q)) return false;
  if (domainEl.value && !(card.domains || []).includes(domainEl.value)) return false;
  if (typeEl.value && card.memory_type !== typeEl.value) return false;
  if (statusEl.value && card.current_status !== statusEl.value) return false;
  return true;
}}
function mark(cardId, decision) {{ decisions[cardId] = {{ decision, decided_at: new Date().toISOString() }}; localStorage.setItem('ob1-human-v2-decisions', JSON.stringify(decisions)); render(); }}
function render() {{
  cardsEl.innerHTML = '';
  const visible = data.cards.filter(matches).sort((a,b) => b.priority - a.priority);
  setSummary(visible.length);
  visible.forEach(card => {{
    const div = document.createElement('section'); div.className = 'card';
    const meta = document.createElement('div'); meta.className = 'meta';
    [`#${{card.index}}`, card.memory_type, `signal ${{card.signal_strength}}`, card.current_status, `priority ${{Math.round(card.priority)}}`, `sources ${{card.source_id_count}}`, ...(card.domains || [])].forEach(t => {{ const span=document.createElement('span'); span.textContent=t; meta.appendChild(span); }});
    const content = document.createElement('div'); content.className = 'content'; content.textContent = card.content;
    const actions = document.createElement('div'); actions.className = 'actions';
    ['keep','edit','drop','unsure'].forEach(decision => {{ const btn=document.createElement('button'); btn.textContent=decision; if ((decisions[card.card_id] || {{}}).decision === decision) btn.className='selected'; btn.onclick=() => mark(card.card_id, decision); actions.appendChild(btn); }});
    div.appendChild(meta); div.appendChild(content); div.appendChild(actions); cardsEl.appendChild(div);
  }});
}}
[searchEl, domainEl, typeEl, statusEl].forEach(el => el.addEventListener('input', render));
document.getElementById('export').onclick = () => {{
  const lines = data.cards.map(card => JSON.stringify({{ card_id: card.card_id, index: card.index, decision: (decisions[card.card_id] || {{decision:'unreviewed'}}).decision, source_ids: card.source_ids }})).join('\n') + '\n';
  const blob = new Blob([lines], {{type: 'application/jsonl'}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'ob1-human-v2-review-decisions.jsonl'; a.click(); URL.revokeObjectURL(a.href);
}};
render();
</script>
</body>
</html>
'''


def export_dashboard(cards, dry_run_plan, output_root):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    summaries = [card_summary(card, index) for index, card in enumerate(cards, start=1)]
    summary = {
        'card_count': len(cards),
        'delete_candidate_ids': (dry_run_plan.get('counts') or {}).get('delete_candidate_ids', 0),
        'held_review_ids': (dry_run_plan.get('counts') or {}).get('held_review_ids', 0),
        'db_apply': False,
    }
    data = {'summary': summary, 'cards': summaries}
    (output_root / 'review-dashboard.html').write_text(dashboard_html(data), encoding='utf-8')
    write_jsonl(
        output_root / 'review-decisions-template.jsonl',
        [{'card_id': card['card_id'], 'index': card['index'], 'decision': 'unreviewed', 'source_ids': card['source_ids']} for card in summaries],
    )
    readme = (
        '# OB1 Human Memory V2 Mem0 Pilot Review\n\n'
        'Open `review-dashboard.html` locally to filter memory cards and mark keep/edit/drop/unsure. '
        'The dashboard stores temporary decisions in browser localStorage and has an export button for JSONL decisions.\n'
    )
    (output_root / 'README.md').write_text(readme, encoding='utf-8')
    return {'card_count': len(cards), 'db_apply': False, 'export_path': str(output_root)}


def export_mem0_requests(cards, output_path, user_id, agent_id=DEFAULT_AGENT_ID, app_id=DEFAULT_APP_ID, limit=0, min_signal=0, target='oss'):
    selected = select_pilot_cards(cards, limit=limit, min_signal=min_signal) if limit or min_signal else list(cards)
    requests = [card_to_mem0_request(card, index, user_id=user_id, agent_id=agent_id, app_id=app_id, target=target) for index, card in enumerate(selected, start=1)]
    write_jsonl(output_path, requests)
    return {'request_count': len(requests), 'card_count': len(cards), 'db_apply': False, 'output': str(output_path)}


def read_requests(path):
    requests = []
    for row in read_jsonl(path):
        if isinstance(row, dict):
            requests.append(row)
    return requests


def join_url(base_url, path):
    return base_url.rstrip('/') + '/' + path.lstrip('/')


def post_mem0_request(base_url, request_obj, api_key=None, auth_header='X-API-Key', auth_prefix='', timeout=30):
    url = join_url(base_url, request_obj.get('path') or '/memories')
    body = json.dumps(request_obj.get('body') or {}, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if api_key:
        value = f'{auth_prefix} {api_key}'.strip() if auth_prefix else api_key
        headers[auth_header] = value
    req = urllib.request.Request(url, data=body, headers=headers, method=request_obj.get('method') or 'POST')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status


def import_mem0_requests(requests_path, base_url, execute=False, api_key_env=None, auth_header='X-API-Key', auth_prefix='', limit=0, skip=0, timeout=30):
    requests = read_requests(requests_path)
    if skip and skip > 0:
        requests = requests[skip:]
    if limit and limit > 0:
        requests = requests[:limit]
    summary = {
        'base_url': base_url.rstrip('/'),
        'dry_run': not execute,
        'request_count': len(requests),
        'attempted': 0,
        'succeeded': 0,
        'failed': 0,
        'db_apply': False,
    }
    if skip and skip > 0:
        summary['skipped'] = skip
    if not execute:
        return summary
    api_key = os.environ.get(api_key_env) if api_key_env else None
    log_path = Path(requests_path).with_name('_mem0-import.log')
    with log_path.open('a', encoding='utf-8') as log:
        for request_obj in requests:
            summary['attempted'] += 1
            try:
                status = post_mem0_request(base_url, request_obj, api_key=api_key, auth_header=auth_header, auth_prefix=auth_prefix, timeout=timeout)
                summary['succeeded'] += 1
                log.write(json.dumps({'path': request_obj.get('path'), 'status': status}, separators=(',', ':')) + '\n')
            except Exception as exc:
                summary['failed'] += 1
                log.write(json.dumps({'path': request_obj.get('path'), 'error': exc.__class__.__name__}, separators=(',', ':')) + '\n')
    summary['log'] = str(log_path)
    return summary


def export_dashboard_command(args):
    cards = read_jsonl(args.cards)
    plan = read_json(args.dry_run_plan)
    summary = export_dashboard(cards, plan, args.output_root)
    print(json.dumps(summary, sort_keys=True, separators=(',', ':')))
    return 0


def export_mem0_command(args):
    cards = read_jsonl(args.cards)
    summary = export_mem0_requests(
        cards,
        args.output,
        user_id=args.user_id,
        agent_id=args.agent_id,
        app_id=args.app_id,
        limit=args.limit,
        min_signal=args.min_signal,
        target=args.target,
    )
    print(json.dumps(summary, sort_keys=True, separators=(',', ':')))
    return 0


def import_mem0_command(args):
    summary = import_mem0_requests(
        args.requests,
        args.base_url,
        execute=args.execute,
        api_key_env=args.api_key_env,
        auth_header=args.auth_header,
        auth_prefix=args.auth_prefix,
        limit=args.limit,
        skip=args.skip,
        timeout=args.timeout,
    )
    print(json.dumps(summary, sort_keys=True, separators=(',', ':')))
    return 0 if summary['failed'] == 0 else 1


def default_dashboard_root(timestamp=None):
    timestamp = timestamp or datetime.now().strftime('%Y%m%d-%H%M%S')
    return Path.home() / 'Desktop' / f'OB1-Mem0-Pilot-Review-{timestamp}'


def build_parser():
    parser = argparse.ArgumentParser(description='Local OB1 Human Memory V2 review and Mem0 export tools.')
    subparsers = parser.add_subparsers(dest='command')

    export_dashboard_parser = subparsers.add_parser('export-dashboard')
    export_dashboard_parser.add_argument('--cards', required=True)
    export_dashboard_parser.add_argument('--dry-run-plan', required=True)
    export_dashboard_parser.add_argument('--output-root', default=str(default_dashboard_root()))
    export_dashboard_parser.set_defaults(func=export_dashboard_command)

    export_mem0_parser = subparsers.add_parser('export-mem0')
    export_mem0_parser.add_argument('--cards', required=True)
    export_mem0_parser.add_argument('--output', required=True)
    export_mem0_parser.add_argument('--user-id', required=True)
    export_mem0_parser.add_argument('--agent-id', default=DEFAULT_AGENT_ID)
    export_mem0_parser.add_argument('--app-id', default=DEFAULT_APP_ID)
    export_mem0_parser.add_argument('--target', choices=['oss', 'platform'], default='oss')
    export_mem0_parser.add_argument('--limit', type=int, default=0)
    export_mem0_parser.add_argument('--min-signal', type=float, default=0)
    export_mem0_parser.set_defaults(func=export_mem0_command)

    import_mem0_parser = subparsers.add_parser('import-mem0')
    import_mem0_parser.add_argument('--requests', required=True)
    import_mem0_parser.add_argument('--base-url', required=True)
    import_mem0_parser.add_argument('--execute', action='store_true')
    import_mem0_parser.add_argument('--api-key-env')
    import_mem0_parser.add_argument('--auth-header', default='X-API-Key')
    import_mem0_parser.add_argument('--auth-prefix', default='')
    import_mem0_parser.add_argument('--limit', type=int, default=0)
    import_mem0_parser.add_argument('--skip', type=int, default=0)
    import_mem0_parser.add_argument('--timeout', type=int, default=30)
    import_mem0_parser.set_defaults(func=import_mem0_command)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, 'func'):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
