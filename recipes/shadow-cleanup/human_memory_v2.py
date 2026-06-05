import argparse
import csv
import glob
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


POLICY_VERSION = 'human-memory-v2-formative-dossiers'

DOMAIN_ORDER = [
    'family_origin',
    'relationships',
    'psychology',
    'shadow_motives',
    'ai',
    'opera',
    'vocality',
    'money_execution',
    'business',
    'workflow',
]

DOMAIN_KEYWORDS = {
    'family_origin': [
        'childhood',
        'family',
        'family origin',
        'father',
        'mother',
        'parent',
        'sibling',
    ],
    'relationships': [
        'attachment',
        'dating',
        'partner',
        'partners',
        'relationship',
        'relationships',
        'romantic',
    ],
    'psychology': [
        'identity',
        'inner pattern',
        'psyche',
        'psychology',
        'self concept',
        'trigger',
    ],
    'shadow_motives': [
        'hidden motive',
        'shadow',
        'shadow motive',
        'shadow motives',
        'status hunger',
    ],
    'ai': [
        'ai',
        'agent',
        'agents',
        'artificial intelligence',
        'llm',
        'memory agents',
        'second brain',
    ],
    'opera': [
        'aria',
        'opera',
        'repertoire',
    ],
    'vocality': [
        'singing',
        'vocal',
        'vocality',
        'voice',
    ],
    'money_execution': [
        'cashflow',
        'finance execution',
        'money',
        'money execution',
        'revenue',
    ],
    'business': [
        'business',
        'client',
        'offer',
        'sales',
        'strategy',
    ],
    'workflow': [
        'cleanup',
        'process',
        'task',
        'workflow',
    ],
}

RESCUE_FIRST_LAYER_DECISIONS = {'keep', 'escalate', 'canonical'}
HIGH_PROTECTION_DOMAINS = {'psychology', 'relationships', 'shadow_motives', 'ai'}
PROTECTED_DOMAINS = {'family_origin'} | HIGH_PROTECTION_DOMAINS

TRANSCRIPT_PROCESS_CLUTTER_KEYWORDS = [
    'diarization',
    'speaker diarization',
    'speaker label',
    'speaker labels',
    'transcript process',
    'transcript segment',
]

PSYCHOLOGY_TYPES = [
    'pattern',
    'person',
    'event',
    'lesson',
    'trigger',
    'shadow_motive',
    'identity_shaping',
    'ai_breakthrough',
]

CURRENT_STATUSES = [
    'active',
    'dormant',
    'resolved',
    'superseded',
    'unknown',
]

EMOTIONAL_INTENSITIES = [
    'mild',
    'strong',
    'formative',
    'identity_shaping',
]


def psychology_card_schema():
    return {
        'type': 'object',
        'required': [
            'content',
            'memory_type',
            'signal_strength',
            'emotional_intensity',
            'current_status',
            'evidence_count',
            'domains',
            'people',
            'source_ids',
            'provenance',
        ],
        'properties': {
            'content': {'type': 'string'},
            'memory_type': {'type': 'string', 'enum': PSYCHOLOGY_TYPES},
            'signal_strength': {'type': 'number', 'minimum': 1, 'maximum': 10},
            'emotional_intensity': {'type': 'string', 'enum': EMOTIONAL_INTENSITIES},
            'current_status': {'type': 'string', 'enum': CURRENT_STATUSES},
            'evidence_count': {'type': 'integer', 'minimum': 0},
            'domains': {'type': 'array', 'items': {'type': 'string'}},
            'people': {'type': 'array', 'items': {'type': 'string'}},
            'source_ids': {'type': 'array', 'items': {'type': 'string'}},
            'provenance': {'type': 'object'},
        },
    }


def clamp_score(value):
    return max(1, min(10, int(round(float(value)))))


def psychology_signal_score(
    *,
    identity_impact,
    emotional_intensity,
    future_advice_value,
    recurrence,
    source_clarity,
):
    return clamp_score(
        clamp_score(identity_impact) * 0.30
        + clamp_score(emotional_intensity) * 0.25
        + clamp_score(future_advice_value) * 0.25
        + clamp_score(recurrence) * 0.10
        + clamp_score(source_clarity) * 0.10
    )


def business_signal_score(
    *,
    usefulness_currentness,
    project_relevance,
    actionability,
    recurrence,
    emotional_intensity,
):
    return clamp_score(
        clamp_score(usefulness_currentness) * 0.40
        + clamp_score(project_relevance) * 0.25
        + clamp_score(actionability) * 0.20
        + clamp_score(recurrence) * 0.10
        + clamp_score(emotional_intensity) * 0.05
    )


def row_content(row):
    return str(row.get('content') or row.get('text') or '')


def row_id(row):
    return row.get('id') or row.get('row_id')


def read_jsonl(path):
    records = []
    with Path(path).open('r', encoding='utf-8-sig') as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
            count += 1
    return count


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def safe_read_json_object(path):
    try:
        data = read_json(path)
    except json.JSONDecodeError:
        return None, {'path': str(path), 'pack_id': None, 'error': 'malformed_json'}

    if not isinstance(data, dict):
        return None, {'path': str(path), 'pack_id': None, 'error': 'json_not_object'}

    return data, None


def clean_slug(value):
    slug = re.sub(r'[^a-z0-9]+', '_', str(value or '').lower()).strip('_')
    return slug or 'unknown'


def stable_hash(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]


def load_cache_decisions(paths):
    decisions = {}
    for path in expand_output_paths(paths):
        for record in read_jsonl(path):
            current_row_id = record.get('row_id')
            decision = record.get('decision')
            if current_row_id is not None and decision is not None:
                decisions[str(current_row_id)] = str(decision)
    return decisions


def keyword_matches(content, keyword):
    return re.search(r'(?<!\w)' + re.escape(keyword.lower()) + r'(?!\w)', content) is not None


def normalize_for_domain_matching(value):
    return re.sub(r'[_-]+', ' ', value.lower())


def detect_domains(content):
    normalized = normalize_for_domain_matching(content)
    domains = []

    for domain in DOMAIN_ORDER:
        if any(keyword_matches(normalized, keyword) for keyword in DOMAIN_KEYWORDS[domain]):
            domains.append(domain)

    return domains


def metadata_values(metadata):
    values = []
    for key in ['topics', 'topic', 'type', 'source', 'project', 'cwd']:
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    return values


def routing_haystack(row):
    values = [row_content(row)]
    metadata = row.get('metadata')
    if isinstance(metadata, dict):
        values.extend(metadata_values(metadata))
    return ' '.join(values)


def is_transcript_process_clutter(content):
    normalized = normalize_for_domain_matching(content)
    return any(keyword_matches(normalized, keyword) for keyword in TRANSCRIPT_PROCESS_CLUTTER_KEYWORDS)


def classify_candidate(row, first_layer_decision):
    haystack = routing_haystack(row)
    domains = detect_domains(haystack)
    classification = {
        'id': row_id(row),
        'route': 'deterministic_drop',
        'domains': domains,
        'protection_level': 'none',
        'first_layer_decision': first_layer_decision,
    }

    if is_transcript_process_clutter(haystack) and not PROTECTED_DOMAINS.intersection(domains):
        classification['drop_reason'] = 'transcript_process_clutter'
        return classification

    if (
        domains
        or first_layer_decision in RESCUE_FIRST_LAYER_DECISIONS
    ):
        classification['route'] = 'rescue_candidate'

    if 'family_origin' in domains:
        classification['protection_level'] = 'maximum'
    elif HIGH_PROTECTION_DOMAINS.intersection(domains):
        classification['protection_level'] = 'high'
    elif classification['route'] == 'rescue_candidate':
        classification['protection_level'] = 'standard'
    else:
        classification['drop_reason'] = 'first_layer_drop'

    return classification


def build_candidate_records(rows, decisions):
    records = []
    for row in rows:
        current_row_id = row_id(row)
        classification = classify_candidate(row, decisions.get(str(current_row_id)))
        if classification['route'] == 'deterministic_drop':
            continue
        records.append(
            {
                'id': current_row_id,
                'content': row_content(row),
                'metadata': row.get('metadata') if isinstance(row.get('metadata'), dict) else {},
                'classification': classification,
            }
        )
    return records


def build_candidate_report(rows, decisions, candidates):
    route_counts = Counter()
    domain_counts = Counter()
    for row in rows:
        current_row_id = row_id(row)
        classification = classify_candidate(row, decisions.get(str(current_row_id)))
        route_counts[classification['route']] += 1
        domain_counts.update(classification['domains'])

    return {
        'snapshot_rows': len(rows),
        'candidate_rows': len(candidates),
        'route_counts': dict(sorted(route_counts.items())),
        'domain_counts': dict(sorted(domain_counts.items())),
        'db_apply': False,
    }


def first_topic(candidate):
    metadata = candidate.get('metadata')
    if not isinstance(metadata, dict):
        return 'unknown'

    topics = metadata.get('topics')
    if isinstance(topics, list) and topics:
        return topics[0]
    if topics is not None:
        return topics

    topic = metadata.get('topic')
    if isinstance(topic, list) and topic:
        return topic[0]
    if topic is not None:
        return topic

    return 'unknown'


def node_domain(candidate):
    classification = candidate.get('classification')
    domains = []
    if isinstance(classification, dict) and isinstance(classification.get('domains'), list):
        domains = [domain for domain in classification['domains'] if isinstance(domain, str)]

    for domain in DOMAIN_ORDER:
        if domain in PROTECTED_DOMAINS and domain in domains:
            return domain
    for domain in DOMAIN_ORDER:
        if domain in domains:
            return domain
    return 'unknown'


def node_key(candidate):
    return f'domain:{clean_slug(node_domain(candidate))}|topic:{clean_slug(first_topic(candidate))}'


def candidate_sort_key(candidate):
    return str(candidate.get('id') or candidate.get('row_id') or stable_hash(candidate))


def build_node_packs(candidates, max_rows_per_pack=35):
    if max_rows_per_pack <= 0:
        raise ValueError('max_rows_per_pack must be greater than 0')

    grouped = {}
    for candidate in candidates:
        key = node_key(candidate)
        grouped.setdefault(key, []).append(candidate)

    packs = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=candidate_sort_key)
        for start in range(0, len(items), max_rows_per_pack):
            chunk = items[start:start + max_rows_per_pack]
            pack_index = start // max_rows_per_pack + 1
            chunk_ids = [candidate_sort_key(candidate) for candidate in chunk]
            pack_identity = {
                'policy_version': POLICY_VERSION,
                'node_key': key,
                'pack_index': pack_index,
                'chunk_ids': chunk_ids,
            }
            packs.append(
                {
                    'pack_id': f'node-pack-{stable_hash(pack_identity)}',
                    'policy_version': POLICY_VERSION,
                    'stage': 'node_classification',
                    'node_key': key,
                    'row_count': len(chunk),
                    'items': chunk,
                }
            )
    return packs



def node_pack_sort_key(pack):
    return (str(pack.get('node_key') or ''), str(pack.get('pack_id') or ''))


def build_node_workloads(node_packs, max_packs_per_workload=40, max_rows_per_workload=180):
    if max_packs_per_workload <= 0:
        raise ValueError('max_packs_per_workload must be greater than 0')
    if max_rows_per_workload <= 0:
        raise ValueError('max_rows_per_workload must be greater than 0')

    workloads = []
    current_packs = []
    current_rows = 0

    for pack in sorted(node_packs, key=node_pack_sort_key):
        pack_rows = int(pack.get('row_count') or len(pack.get('items') or []))
        would_exceed_pack_limit = len(current_packs) >= max_packs_per_workload
        would_exceed_row_limit = current_packs and current_rows + pack_rows > max_rows_per_workload
        if would_exceed_pack_limit or would_exceed_row_limit:
            workload_identity = {
                'policy_version': POLICY_VERSION,
                'stage': 'node_classification_workload',
                'pack_ids': [item.get('pack_id') for item in current_packs],
            }
            workloads.append(
                {
                    'workload_id': f'node-workload-{stable_hash(workload_identity)}',
                    'policy_version': POLICY_VERSION,
                    'stage': 'node_classification_workload',
                    'pack_count': len(current_packs),
                    'row_count': current_rows,
                    'packs': current_packs,
                }
            )
            current_packs = []
            current_rows = 0

        current_packs.append(pack)
        current_rows += pack_rows

    if current_packs:
        workload_identity = {
            'policy_version': POLICY_VERSION,
            'stage': 'node_classification_workload',
            'pack_ids': [item.get('pack_id') for item in current_packs],
        }
        workloads.append(
            {
                'workload_id': f'node-workload-{stable_hash(workload_identity)}',
                'policy_version': POLICY_VERSION,
                'stage': 'node_classification_workload',
                'pack_count': len(current_packs),
                'row_count': current_rows,
                'packs': current_packs,
            }
        )

    return workloads
def node_policy_prompt(pack):
    schema = {
        'pack_id': pack.get('pack_id'),
        'policy_version': POLICY_VERSION,
        'nodes': [
            {
                'node_title': 'short stable dossier title',
                'node_type': 'pattern|person|event|lesson|trigger|shadow_motive|identity_shaping|ai_breakthrough',
                'domains': ['domain names'],
                'source_ids': ['input row ids assigned to this node'],
                'rationale': 'brief non-raw reason for grouping',
            }
        ],
        'drop_source_ids': ['input row ids that are clear clutter'],
        'review_source_ids': ['input row ids that need human review'],
        'review_questions': ['specific questions for ambiguous rows'],
    }
    return (
        f'Policy version: {POLICY_VERSION}\n\n'
        'Classify this node pack under Human Memory V2. Keep high recall for '
        'psychology/relationships/family-origin/shadow motives/identity-shaping/AI philosophy. '
        'Preserve formative, emotionally intense, identity-shaping, family-origin, relationship, '
        'shadow-motive, and AI philosophy/workstyle signals even when phrasing is messy or partial.\n\n'
        'Drop only clear transcript/process/technical clutter: diarization notes, speaker-label cleanup, '
        'generic debugging/process chatter, vocal lesson sludge without durable human signal, and duplicated '
        'project status noise already represented by stronger memory. Use review instead of drop when unsure.\n\n'
        'Return strict JSON only. Do not include Markdown, comments, or prose outside JSON. Required JSON schema:\n'
        f'{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n'
        'Coverage rule: every input id must appear exactly once across nodes.source_ids, drop_source_ids, '
        'or review_source_ids. Do not invent ids. Do not omit ids. Do not assign any id twice.\n\n'
        'Input pack JSON:\n'
        f'{json.dumps(pack, ensure_ascii=False, sort_keys=True, indent=2)}'
    )


def pack_input_ids(pack):
    ids = []
    for item in pack.get('items') or []:
        current_id = row_id(item)
        if current_id is not None:
            ids.append(str(current_id))
    return ids


def node_output_assignments(output):
    assigned = []
    type_errors = []
    counts = {
        'node_source_ids': 0,
        'drop_source_ids': 0,
        'review_source_ids': 0,
    }

    nodes = output.get('nodes')
    if not isinstance(nodes, list):
        type_errors.append({'field': 'nodes', 'expected': 'list'})
    else:
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                type_errors.append({'field': f'nodes[{index}]', 'expected': 'object'})
                continue
            raw_source_ids = node.get('source_ids')
            if not isinstance(raw_source_ids, list):
                type_errors.append({'field': f'nodes[{index}].source_ids', 'expected': 'list'})
                continue
            source_ids = [str(source_id) for source_id in raw_source_ids]
            counts['node_source_ids'] += len(source_ids)
            assigned.extend(source_ids)

    raw_drop_source_ids = output.get('drop_source_ids')
    if not isinstance(raw_drop_source_ids, list):
        type_errors.append({'field': 'drop_source_ids', 'expected': 'list'})
    else:
        drop_source_ids = [str(source_id) for source_id in raw_drop_source_ids]
        counts['drop_source_ids'] = len(drop_source_ids)
        assigned.extend(drop_source_ids)

    raw_review_source_ids = output.get('review_source_ids')
    if not isinstance(raw_review_source_ids, list):
        type_errors.append({'field': 'review_source_ids', 'expected': 'list'})
    else:
        review_source_ids = [str(source_id) for source_id in raw_review_source_ids]
        counts['review_source_ids'] = len(review_source_ids)
        assigned.extend(review_source_ids)

    return assigned, counts, type_errors


def validate_node_output(pack, output):
    input_ids = pack_input_ids(pack)
    input_id_set = set(input_ids)
    assigned_ids, assignment_counts, type_errors = node_output_assignments(output)
    assigned_counter = Counter(assigned_ids)
    assigned_id_set = set(assigned_ids)

    missing_ids = sorted(input_id_set - assigned_id_set)
    extra_ids = sorted(assigned_id_set - input_id_set)
    duplicate_ids = sorted(source_id for source_id, count in assigned_counter.items() if count > 1)
    status = 'pass' if not missing_ids and not extra_ids and not duplicate_ids and not type_errors else 'fail'

    return {
        'pack_id': pack.get('pack_id'),
        'status': status,
        'missing_ids': missing_ids,
        'extra_ids': extra_ids,
        'duplicate_ids': duplicate_ids,
        'type_errors': type_errors,
        'counts': {
            'input_ids': len(input_ids),
            **assignment_counts,
            'assigned_ids': len(assigned_ids),
            'unique_assigned_ids': len(assigned_id_set),
        },
    }


def expand_output_paths(patterns):
    paths = []
    for pattern in patterns:
        path_pattern = str(pattern)
        matches = sorted(glob.glob(path_pattern))
        if matches:
            paths.extend(matches)
        elif Path(path_pattern).exists():
            paths.append(path_pattern)
    return paths


def validate_node_outputs(packs, output_paths):
    pack_ids = {str(pack.get('pack_id')) for pack in packs}
    outputs_by_pack_id = {}
    issues = []
    extra_output_count = 0
    duplicate_output_count = 0
    unknown_output_count = 0
    malformed_output_count = 0
    for output_path in output_paths:
        output, issue = safe_read_json_object(output_path)
        if issue is not None:
            malformed_output_count += 1
            issues.append(issue)
            continue
        pack_id = output.get('pack_id')
        if pack_id is None or str(pack_id) not in pack_ids:
            extra_output_count += 1
            unknown_output_count += 1
            issues.append({'path': str(output_path), 'pack_id': pack_id, 'error': 'unknown_pack_id'})
            continue
        if str(pack_id) in outputs_by_pack_id:
            extra_output_count += 1
            duplicate_output_count += 1
            issues.append({'path': str(output_path), 'pack_id': pack_id, 'error': 'duplicate_pack_id'})
            continue
        outputs_by_pack_id.setdefault(str(pack_id), output)

    results = []
    missing_output_count = 0
    for pack in packs:
        pack_id = str(pack.get('pack_id'))
        output = outputs_by_pack_id.get(pack_id)
        if output is None:
            missing_output_count += 1
            results.append(
                {
                    'pack_id': pack.get('pack_id'),
                    'status': 'fail',
                    'missing_ids': pack_input_ids(pack),
                    'extra_ids': [],
                    'duplicate_ids': [],
                    'counts': {
                        'input_ids': len(pack_input_ids(pack)),
                        'node_source_ids': 0,
                        'drop_source_ids': 0,
                        'review_source_ids': 0,
                        'assigned_ids': 0,
                        'unique_assigned_ids': 0,
                    },
                    'error': 'missing_output',
                }
            )
            continue
        results.append(validate_node_output(pack, output))

    pass_count = sum(1 for result in results if result['status'] == 'pass')
    fail_count = len(results) - pass_count + extra_output_count + malformed_output_count
    summary = {
        'pack_count': len(packs),
        'output_count': len(output_paths),
        'pass_count': pass_count,
        'fail_count': fail_count,
        'missing_output_count': missing_output_count,
        'extra_output_count': extra_output_count,
        'duplicate_output_count': duplicate_output_count,
        'unknown_output_count': unknown_output_count,
        'malformed_output_count': malformed_output_count,
        'status': 'pass' if fail_count == 0 else 'fail',
        'db_apply': False,
    }
    return {'summary': summary, 'results': results, 'issues': issues}


CARD_SCHEMA_FIELDS = [
    'content',
    'memory_type',
    'signal_strength',
    'emotional_intensity',
    'current_status',
    'evidence_count',
    'domains',
    'people',
    'source_ids',
    'provenance',
    'timeline',
    'incidents',
    'competing_interpretations',
    'ai_guidance',
    'review_reason',
]


def normalize_id_list(value):
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def source_items_by_id(pack):
    items = {}
    for item in pack.get('items') or []:
        current_id = row_id(item)
        if current_id is not None:
            items[str(current_id)] = item
    return items


def build_card_node(node_pack, node, node_index):
    source_lookup = source_items_by_id(node_pack)
    source_ids = normalize_id_list(node.get('source_ids'))
    node_title = node.get('node_title') or node.get('title') or 'Untitled node'
    node_identity = {
        'node_pack_id': node_pack.get('pack_id'),
        'node_index': node_index,
        'node_title': node_title,
        'source_ids': source_ids,
    }
    return {
        'node_id': f'node-{stable_hash(node_identity)}',
        'node_pack_id': node_pack.get('pack_id'),
        'node_title': node_title,
        'node_type': node.get('node_type') or node.get('type') or 'pattern',
        'domains': normalize_id_list(node.get('domains')),
        'source_ids': source_ids,
        'rationale': str(node.get('rationale') or ''),
        'source_items': [source_lookup[source_id] for source_id in source_ids if source_id in source_lookup],
    }


def build_card_packs(node_packs, node_outputs, max_nodes_per_pack=12):
    if max_nodes_per_pack <= 0:
        raise ValueError('max_nodes_per_pack must be greater than 0')

    outputs_by_pack_id = {
        str(output.get('pack_id')): output
        for output in node_outputs
        if isinstance(output, dict) and output.get('pack_id') is not None
    }
    card_nodes = []
    for node_pack in node_packs:
        output = outputs_by_pack_id.get(str(node_pack.get('pack_id')))
        if not output or not isinstance(output.get('nodes'), list):
            continue
        for node_index, node in enumerate(output['nodes']):
            if isinstance(node, dict):
                card_nodes.append(build_card_node(node_pack, node, node_index))

    packs = []
    for start in range(0, len(card_nodes), max_nodes_per_pack):
        chunk = card_nodes[start:start + max_nodes_per_pack]
        pack_identity = {
            'policy_version': POLICY_VERSION,
            'stage': 'card_synthesis',
            'node_ids': [node['node_id'] for node in chunk],
        }
        packs.append(
            {
                'pack_id': f'card-pack-{stable_hash(pack_identity)}',
                'policy_version': POLICY_VERSION,
                'stage': 'card_synthesis',
                'node_count': len(chunk),
                'nodes': chunk,
            }
        )
    return packs


def card_synthesis_prompt(node_pack):
    schema = {
        'pack_id': node_pack.get('pack_id'),
        'policy_version': POLICY_VERSION,
        'cards': [
            {
                'content': 'compact human-memory card preserving the durable signal and nuance',
                'memory_type': 'pattern|person|event|lesson|trigger|shadow_motive|identity_shaping|ai_breakthrough',
                'signal_strength': 'number 1..10',
                'emotional_intensity': 'mild|strong|formative|identity_shaping',
                'current_status': 'active|dormant|resolved|superseded|unknown',
                'evidence_count': 'integer evidence/source count',
                'domains': ['domain names'],
                'people': ['canonical person names or roles'],
                'source_ids': ['source row ids represented by this card'],
                'provenance': {'node_ids': ['node ids'], 'source_summary': 'brief non-raw provenance'},
                'timeline': ['dated or ordered progression points when useful'],
                'incidents': ['concrete formative incidents when they matter'],
                'competing_interpretations': ['active/dormant/resolved interpretations or contradictions'],
                'ai_guidance': ['how future AI should adapt advice/retrieval using this card'],
                'review_reason': 'empty string unless human review is needed',
            }
        ],
        'held_for_review': ['source ids too ambiguous to synthesize safely'],
    }
    return (
        f'Policy version: {POLICY_VERSION}\n\n'
        'Synthesize these nodes into durable human-memory cards. Psychology, relationships, family-origin, '
        'shadow motives, emotionally raw formative events, and AI philosophy are central. Do not flatten psychology; '
        'preserve useful nuance, timeline progression, emotional intensity, uncertainty, and competing interpretations.\n\n'
        'For business/workflow/project material, keep the compact current architecture, purpose, workflow, and decision '
        'signal. Drop implementation sludge and transcript/process clutter unless it carries durable personal/project signal.\n\n'
        'Return strict JSON only. Do not include Markdown, comments, or prose outside JSON. Required JSON schema:\n'
        f'{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n'
        'Coverage rule: every source id represented by an input node should appear in at least one card.source_ids or '
        'held_for_review. Do not invent ids. Keep source_ids and provenance so the card remains auditable.\n\n'
        'Input card-synthesis pack JSON:\n'
        f'{json.dumps(node_pack, ensure_ascii=False, sort_keys=True, indent=2)}'
    )


def card_pack_source_ids(pack):
    ids = []
    for node in pack.get('nodes') or []:
        if isinstance(node, dict):
            ids.extend(normalize_id_list(node.get('source_ids')))
    return ids


def card_output_assignments(output):
    assigned = []
    type_errors = []
    missing_required_fields = []
    card_count = 0

    cards = output.get('cards')
    if not isinstance(cards, list):
        type_errors.append({'field': 'cards', 'expected': 'list'})
        cards = []

    for card_index, card in enumerate(cards):
        if not isinstance(card, dict):
            type_errors.append({'card_index': card_index, 'field': 'card', 'expected': 'object'})
            continue
        card_count += 1
        for field in CARD_SCHEMA_FIELDS:
            if field not in card:
                missing_required_fields.append({'card_index': card_index, 'field': field})

        signal_strength = card.get('signal_strength')
        if (
            isinstance(signal_strength, bool)
            or not isinstance(signal_strength, (int, float))
            or signal_strength < 1
            or signal_strength > 10
        ):
            type_errors.append({'card_index': card_index, 'field': 'signal_strength', 'expected': 'number 1..10'})

        source_ids = card.get('source_ids')
        if not isinstance(source_ids, list) or not source_ids:
            type_errors.append({'card_index': card_index, 'field': 'source_ids', 'expected': 'non-empty list'})
        else:
            assigned.extend(str(source_id) for source_id in source_ids)

        for list_field in ['domains', 'people', 'timeline', 'incidents', 'competing_interpretations', 'ai_guidance']:
            if list_field in card and not isinstance(card.get(list_field), list):
                type_errors.append({'card_index': card_index, 'field': list_field, 'expected': 'list'})
        if 'provenance' in card and not isinstance(card.get('provenance'), dict):
            type_errors.append({'card_index': card_index, 'field': 'provenance', 'expected': 'object'})
        if 'content' in card and not isinstance(card.get('content'), str):
            type_errors.append({'card_index': card_index, 'field': 'content', 'expected': 'string'})
        if 'memory_type' in card and card.get('memory_type') not in PSYCHOLOGY_TYPES:
            type_errors.append({'card_index': card_index, 'field': 'memory_type', 'expected': 'known memory type'})
        if 'emotional_intensity' in card and card.get('emotional_intensity') not in EMOTIONAL_INTENSITIES:
            type_errors.append({'card_index': card_index, 'field': 'emotional_intensity', 'expected': 'known emotional intensity'})
        if 'current_status' in card and card.get('current_status') not in CURRENT_STATUSES:
            type_errors.append({'card_index': card_index, 'field': 'current_status', 'expected': 'known current status'})
        evidence_count = card.get('evidence_count')
        if 'evidence_count' in card and (isinstance(evidence_count, bool) or not isinstance(evidence_count, int) or evidence_count < 0):
            type_errors.append({'card_index': card_index, 'field': 'evidence_count', 'expected': 'integer >= 0'})

    held_for_review = output.get('held_for_review', [])
    if held_for_review is not None and not isinstance(held_for_review, list):
        type_errors.append({'field': 'held_for_review', 'expected': 'list'})
    elif isinstance(held_for_review, list):
        assigned.extend(str(source_id) for source_id in held_for_review)

    return assigned, missing_required_fields, type_errors, card_count


def validate_card_output(output, pack=None):
    input_ids = card_pack_source_ids(pack or {})
    input_id_set = set(input_ids)
    assigned_ids, missing_required_fields, type_errors, card_count = card_output_assignments(output)
    assigned_counter = Counter(assigned_ids)
    assigned_id_set = set(assigned_ids)

    missing_source_ids = sorted(input_id_set - assigned_id_set)
    extra_source_ids = sorted(assigned_id_set - input_id_set) if input_id_set else []
    duplicate_source_ids = sorted(source_id for source_id, count in assigned_counter.items() if count > 1)
    status = 'pass' if not missing_required_fields and not type_errors and not missing_source_ids and not extra_source_ids else 'fail'

    return {
        'pack_id': output.get('pack_id') if isinstance(output, dict) else None,
        'status': status,
        'missing_required_fields': missing_required_fields,
        'type_errors': type_errors,
        'missing_source_ids': missing_source_ids,
        'extra_source_ids': extra_source_ids,
        'duplicate_source_ids': duplicate_source_ids,
        'counts': {
            'input_source_ids': len(input_ids),
            'assigned_source_ids': len(assigned_ids),
            'unique_assigned_source_ids': len(assigned_id_set),
            'card_count': card_count,
        },
    }


def validate_card_outputs(packs, output_paths):
    pack_ids = {str(pack.get('pack_id')) for pack in packs}
    packs_by_id = {str(pack.get('pack_id')): pack for pack in packs}
    outputs_by_pack_id = {}
    issues = []
    extra_output_count = 0
    duplicate_output_count = 0
    unknown_output_count = 0
    malformed_output_count = 0
    for output_path in output_paths:
        output, issue = safe_read_json_object(output_path)
        if issue is not None:
            malformed_output_count += 1
            issues.append(issue)
            continue
        pack_id = output.get('pack_id')
        if pack_id is None or str(pack_id) not in pack_ids:
            extra_output_count += 1
            unknown_output_count += 1
            issues.append({'path': str(output_path), 'pack_id': pack_id, 'error': 'unknown_pack_id'})
            continue
        if str(pack_id) in outputs_by_pack_id:
            extra_output_count += 1
            duplicate_output_count += 1
            issues.append({'path': str(output_path), 'pack_id': pack_id, 'error': 'duplicate_pack_id'})
            continue
        outputs_by_pack_id[str(pack_id)] = output

    results = []
    missing_output_count = 0
    card_count = 0
    for pack in packs:
        pack_id = str(pack.get('pack_id'))
        output = outputs_by_pack_id.get(pack_id)
        if output is None:
            missing_output_count += 1
            results.append(
                {
                    'pack_id': pack.get('pack_id'),
                    'status': 'fail',
                    'missing_required_fields': [],
                    'type_errors': [],
                    'missing_source_ids': card_pack_source_ids(pack),
                    'extra_source_ids': [],
                    'duplicate_source_ids': [],
                    'counts': {
                        'input_source_ids': len(card_pack_source_ids(pack)),
                        'assigned_source_ids': 0,
                        'unique_assigned_source_ids': 0,
                        'card_count': 0,
                    },
                    'error': 'missing_output',
                }
            )
            continue
        result = validate_card_output(output, packs_by_id[pack_id])
        results.append(result)
        card_count += result['counts']['card_count']

    pass_count = sum(1 for result in results if result['status'] == 'pass')
    fail_count = len(results) - pass_count + extra_output_count + malformed_output_count
    summary = {
        'pack_count': len(packs),
        'output_count': len(output_paths),
        'pass_count': pass_count,
        'fail_count': fail_count,
        'missing_output_count': missing_output_count,
        'extra_output_count': extra_output_count,
        'duplicate_output_count': duplicate_output_count,
        'unknown_output_count': unknown_output_count,
        'malformed_output_count': malformed_output_count,
        'card_count': card_count,
        'status': 'pass' if fail_count == 0 else 'fail',
        'db_apply': False,
    }
    return {'summary': summary, 'results': results, 'issues': issues}

def build_candidates_command(args):
    rows = read_jsonl(args.snapshot)
    decisions = load_cache_decisions(args.cache_files)
    candidates = build_candidate_records(rows, decisions)
    write_jsonl(args.output, candidates)
    report = build_candidate_report(rows, decisions, candidates)
    write_json(args.report, report)
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    return 0


def build_node_packs_command(args):
    candidates = read_jsonl(args.candidates)
    packs = build_node_packs(candidates, max_rows_per_pack=args.max_rows_per_pack)
    write_jsonl(args.output, packs)
    report = {
        'candidate_rows': len(candidates),
        'pack_count': len(packs),
        'db_apply': False,
    }
    write_json(args.report, report)
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    return 0




def unique_sorted(values):
    return sorted({str(value) for value in values if value is not None})


def extract_record_id(record):
    if isinstance(record, str):
        return record
    if not isinstance(record, dict):
        return None
    for field in ['id', 'row_id', 'source_id', 'thought_id']:
        if record.get(field) is not None:
            return str(record[field])
    return None


def ids_from_records(records):
    ids = []
    for record in records:
        current_id = extract_record_id(record)
        if current_id is not None:
            ids.append(current_id)
    return ids


def v2_card_source_ids(cards):
    ids = []
    for card in cards:
        if isinstance(card, dict):
            ids.extend(normalize_id_list(card.get('source_ids')))
    return ids


def build_v2_dry_run_plan(v1_delete_ids, held_ids, v2_cards):
    v1_delete_ids = unique_sorted(v1_delete_ids)
    held_ids = unique_sorted(held_ids)
    rescued_source_ids = unique_sorted(v2_card_source_ids(v2_cards))
    rescued_source_id_set = set(rescued_source_ids)
    delete_candidate_ids = [source_id for source_id in v1_delete_ids if source_id not in rescued_source_id_set]

    return {
        'policy_version': POLICY_VERSION,
        'mode': 'dry_run',
        'db_apply': False,
        'insert_candidates': v2_cards,
        'rescued_source_ids': rescued_source_ids,
        'delete_candidate_ids': delete_candidate_ids,
        'held_review_ids': held_ids,
        'counts': {
            'v1_delete_ids': len(v1_delete_ids),
            'v1_held_ids': len(held_ids),
            'v2_cards': len(v2_cards),
            'rescued_source_ids': len(rescued_source_ids),
            'delete_candidate_ids': len(delete_candidate_ids),
            'held_review_ids': len(held_ids),
        },
    }


def load_card_proposal_files(paths):
    cards = []
    held_ids = []
    issues = []
    for path in paths:
        data, issue = safe_read_json_object(path)
        if issue is not None:
            issues.append(issue)
            continue
        raw_cards = data.get('cards')
        if isinstance(raw_cards, list):
            cards.extend(card for card in raw_cards if isinstance(card, dict))
        raw_held = data.get('held_for_review')
        if isinstance(raw_held, list):
            held_ids.extend(str(source_id) for source_id in raw_held if source_id is not None)
    return cards, held_ids, issues


def write_v2_dry_run_artifacts(output_root, plan):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    plan_path = output_root / 'v2-dry-run-plan.json'
    insert_path = output_root / 'v2-insert-candidates.jsonl'
    delete_path = output_root / 'v2-delete-candidates.jsonl'
    held_path = output_root / 'v2-held-review-ids.jsonl'

    write_json(plan_path, plan)
    write_jsonl(insert_path, plan['insert_candidates'])
    write_jsonl(delete_path, [{'id': source_id, 'category': 'v2_delete_candidate'} for source_id in plan['delete_candidate_ids']])
    write_jsonl(held_path, [{'id': source_id, 'category': 'v2_hold_review'} for source_id in plan['held_review_ids']])
    return {
        'plan': str(plan_path),
        'insert_candidates': str(insert_path),
        'delete_candidates': str(delete_path),
        'held_review_ids': str(held_path),
    }
def read_json_objects(paths):
    objects = []
    issues = []
    for path in paths:
        data, issue = safe_read_json_object(path)
        if issue is None:
            objects.append(data)
        else:
            issues.append(issue)
    return objects, issues



def build_node_workloads_command(args):
    node_packs = read_jsonl(args.node_packs)
    workloads = build_node_workloads(
        node_packs,
        max_packs_per_workload=args.max_packs_per_workload,
        max_rows_per_workload=args.max_rows_per_workload,
    )
    write_jsonl(args.output, workloads)
    report = {
        'node_pack_count': len(node_packs),
        'node_row_count': sum(int(pack.get('row_count') or len(pack.get('items') or [])) for pack in node_packs),
        'workload_count': len(workloads),
        'max_packs_per_workload': args.max_packs_per_workload,
        'max_rows_per_workload': args.max_rows_per_workload,
        'db_apply': False,
    }
    write_json(args.report, report)
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    return 0
def build_card_packs_command(args):
    node_packs = read_jsonl(args.node_packs)
    output_paths = expand_output_paths(args.node_outputs)
    node_outputs, issues = read_json_objects(output_paths)
    card_packs = build_card_packs(node_packs, node_outputs, max_nodes_per_pack=args.max_nodes_per_pack)
    write_jsonl(args.output, card_packs)
    report = {
        'node_pack_count': len(node_packs),
        'node_output_count': len(output_paths),
        'card_pack_count': len(card_packs),
        'node_count': sum(pack['node_count'] for pack in card_packs),
        'db_apply': False,
    }
    if issues:
        report['malformed_output_count'] = len(issues)
    write_json(args.report, report)
    print(json.dumps(report, sort_keys=True, separators=(',', ':')))
    return 1 if issues else 0


def validate_cards_command(args):
    packs = read_jsonl(args.packs)
    output_paths = expand_output_paths(args.outputs)
    report = validate_card_outputs(packs, output_paths)
    write_json(args.report, report)
    print(json.dumps(report['summary'], sort_keys=True, separators=(',', ':')))
    return 0 if report['summary']['status'] == 'pass' else 1


def build_review_markdown(cards, summary):
    lines = [
        '# OB1 Claude Human Memory V2 Review',
        '',
        'Local review package for Human Memory V2 cards. No database changes have been applied.',
        '',
        '## Summary',
        '',
    ]
    for key in sorted(summary):
        lines.append(f'- {key}: {summary[key]}')
    lines.extend(['', '## Cards', ''])

    for index, card in enumerate(cards, start=1):
        memory_type = card.get('memory_type') or 'unknown'
        signal_strength = card.get('signal_strength', 'unknown')
        current_status = card.get('current_status', 'unknown')
        domains = ', '.join(str(domain) for domain in card.get('domains') or []) or 'none'
        people = ', '.join(str(person) for person in card.get('people') or []) or 'none'
        source_ids = ', '.join(str(source_id) for source_id in card.get('source_ids') or []) or 'none'
        content = str(card.get('content') or '')

        lines.extend(
            [
                f'## Card {index}: {memory_type}',
                '',
                '- [ ] Keep',
                '- [ ] Edit',
                '- [ ] Drop',
                '',
                f'- Signal strength: {signal_strength}',
                f'- Current status: {current_status}',
                f'- Domains: {domains}',
                f'- People: {people}',
                f'- Source IDs: {source_ids}',
                '',
                content,
                '',
            ]
        )
    return '\n'.join(lines).rstrip() + '\n'


def default_review_output_root(timestamp=None):
    timestamp = timestamp or datetime.now().strftime('%Y%m%d-%H%M%S')
    return Path.home() / 'Desktop' / f'OB1-Claude-Human-Memory-V2-Review-{timestamp}'


def write_cards_csv(path, cards):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        'index',
        'memory_type',
        'signal_strength',
        'current_status',
        'domains',
        'people',
        'source_id_count',
        'content',
    ]
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, card in enumerate(cards, start=1):
            writer.writerow(
                {
                    'index': index,
                    'memory_type': card.get('memory_type') or '',
                    'signal_strength': card.get('signal_strength') or '',
                    'current_status': card.get('current_status') or '',
                    'domains': ';'.join(str(item) for item in card.get('domains') or []),
                    'people': ';'.join(str(item) for item in card.get('people') or []),
                    'source_id_count': len(card.get('source_ids') or []),
                    'content': card.get('content') or '',
                }
            )


def export_review_package(dry_run_plan, cards, output_root=None):
    output_root = Path(output_root) if output_root else default_review_output_root()
    output_root.mkdir(parents=True, exist_ok=True)
    summary = {
        'card_count': len(cards),
        'delete_candidate_ids': (dry_run_plan.get('counts') or {}).get('delete_candidate_ids', 0),
        'held_review_ids': (dry_run_plan.get('counts') or {}).get('held_review_ids', 0),
        'db_apply': False,
    }

    readme = (
        '# OB1 Claude Human Memory V2 Review\n\n'
        'This package is local-only. Review `memory-cards-review.md` for Keep/Edit/Drop decisions. '\
        '`memory-cards.jsonl` contains the exact card objects, and `dry-run-plan.json` contains the local apply plan.\n'
    )
    (output_root / 'README.md').write_text(readme, encoding='utf-8')
    (output_root / 'memory-cards-review.md').write_text(build_review_markdown(cards, summary), encoding='utf-8')
    write_cards_csv(output_root / 'memory-cards.csv', cards)
    write_jsonl(output_root / 'memory-cards.jsonl', cards)
    write_json(output_root / 'dry-run-plan.json', dry_run_plan)

    jsonl_card_count = len(read_jsonl(output_root / 'memory-cards.jsonl'))
    return {
        'card_count': len(cards),
        'jsonl_card_count': jsonl_card_count,
        'db_apply': False,
        'export_path': str(output_root),
    }

def export_review_command(args):
    dry_run_plan = read_json(args.dry_run_plan)
    cards = read_jsonl(args.cards)
    summary = export_review_package(dry_run_plan, cards, output_root=args.output_root)
    print(json.dumps(summary, sort_keys=True, separators=(',', ':')))
    return 0 if summary['card_count'] == summary['jsonl_card_count'] else 1
def dry_run_plan_command(args):
    v1_delete_ids = ids_from_records(read_jsonl(args.v1_delete_candidates))
    v1_held_ids = ids_from_records(read_jsonl(args.v1_held_ids))
    proposal_paths = expand_output_paths(args.v2_card_proposals)
    cards, proposal_held_ids, issues = load_card_proposal_files(proposal_paths)
    plan = build_v2_dry_run_plan(v1_delete_ids, v1_held_ids + proposal_held_ids, cards)
    artifacts = write_v2_dry_run_artifacts(args.output_root, plan)
    plan['artifacts'] = artifacts
    write_json(Path(args.output_root) / 'v2-dry-run-plan.json', plan)
    summary = {
        **plan['counts'],
        'db_apply': False,
    }
    if issues:
        summary['malformed_output_count'] = len(issues)
    print(json.dumps(summary, sort_keys=True, separators=(',', ':')))
    return 1 if issues else 0
def validate_nodes_command(args):
    packs = read_jsonl(args.packs)
    output_paths = expand_output_paths(args.outputs)
    report = validate_node_outputs(packs, output_paths)
    write_json(args.report, report)
    print(json.dumps(report['summary'], sort_keys=True, separators=(',', ':')))
    return 0 if report['summary']['status'] == 'pass' else 1


def build_parser():
    parser = argparse.ArgumentParser(description='Human memory V2 rescue utilities.')
    subparsers = parser.add_subparsers(dest='command')

    build_candidates = subparsers.add_parser('build-candidates')
    build_candidates.add_argument('--snapshot', required=True)
    build_candidates.add_argument('--cache-files', nargs='+', required=True)
    build_candidates.add_argument('--output', required=True)
    build_candidates.add_argument('--report', required=True)
    build_candidates.set_defaults(func=build_candidates_command)

    build_node_packs = subparsers.add_parser('build-node-packs')
    build_node_packs.add_argument('--candidates', required=True)
    build_node_packs.add_argument('--max-rows-per-pack', type=int, default=35)
    build_node_packs.add_argument('--output', required=True)
    build_node_packs.add_argument('--report', required=True)
    build_node_packs.set_defaults(func=build_node_packs_command)

    build_node_workloads = subparsers.add_parser('build-node-workloads')
    build_node_workloads.add_argument('--node-packs', required=True)
    build_node_workloads.add_argument('--max-packs-per-workload', type=int, default=40)
    build_node_workloads.add_argument('--max-rows-per-workload', type=int, default=180)
    build_node_workloads.add_argument('--output', required=True)
    build_node_workloads.add_argument('--report', required=True)
    build_node_workloads.set_defaults(func=build_node_workloads_command)
    validate_nodes = subparsers.add_parser('validate-nodes')
    validate_nodes.add_argument('--packs', required=True)
    validate_nodes.add_argument('--outputs', nargs='+', required=True)
    validate_nodes.add_argument('--report', required=True)
    validate_nodes.set_defaults(func=validate_nodes_command)

    build_card_packs = subparsers.add_parser('build-card-packs')
    build_card_packs.add_argument('--node-packs', required=True)
    build_card_packs.add_argument('--node-outputs', nargs='+', required=True)
    build_card_packs.add_argument('--max-nodes-per-pack', type=int, default=12)
    build_card_packs.add_argument('--output', required=True)
    build_card_packs.add_argument('--report', required=True)
    build_card_packs.set_defaults(func=build_card_packs_command)

    validate_cards = subparsers.add_parser('validate-cards')
    validate_cards.add_argument('--packs', required=True)
    validate_cards.add_argument('--outputs', nargs='+', required=True)
    validate_cards.add_argument('--report', required=True)
    validate_cards.set_defaults(func=validate_cards_command)
    dry_run_plan = subparsers.add_parser('dry-run-plan')
    dry_run_plan.add_argument('--v1-delete-candidates', required=True)
    dry_run_plan.add_argument('--v1-held-ids', required=True)
    dry_run_plan.add_argument('--v2-card-proposals', nargs='+', required=True)
    dry_run_plan.add_argument('--output-root', required=True)
    dry_run_plan.set_defaults(func=dry_run_plan_command)
    export_review = subparsers.add_parser('export-review')
    export_review.add_argument('--dry-run-plan', required=True)
    export_review.add_argument('--cards', required=True)
    export_review.add_argument('--output-root')
    export_review.set_defaults(func=export_review_command)
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
