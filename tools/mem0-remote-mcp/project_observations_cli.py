from __future__ import annotations

import argparse
import json

from project_observations import DEFAULT_PROJECT_OBSERVATIONS_LOG, ProjectObservationStore


def add_common_append_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--log', default=DEFAULT_PROJECT_OBSERVATIONS_LOG)
    parser.add_argument('--origin', default='unknown')
    parser.add_argument('--project-root', default='')
    parser.add_argument('--cwd', default='')
    parser.add_argument('--git-branch', default='')
    parser.add_argument('--worktree-root', default='')
    parser.add_argument('--event-type', default='note')
    parser.add_argument('--title', default='')
    parser.add_argument('--summary', default='')
    parser.add_argument('--file-read', action='append', default=[])
    parser.add_argument('--file-modified', action='append', default=[])
    parser.add_argument('--command', action='append', default=[], dest='commands')
    parser.add_argument('--test', action='append', default=[])
    parser.add_argument('--decision', action='append', default=[])
    parser.add_argument('--next-step', action='append', default=[])
    parser.add_argument('--memory-id', action='append', default=[])
    parser.add_argument('--source-hash', default='')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Append and query compact project observations.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    append = subparsers.add_parser('append')
    add_common_append_args(append)
    recent = subparsers.add_parser('recent')
    recent.add_argument('--log', default=DEFAULT_PROJECT_OBSERVATIONS_LOG)
    recent.add_argument('--limit', type=int, default=20)
    recent.add_argument('--origin', default='')
    recent.add_argument('--event-type', default='')
    by_file = subparsers.add_parser('by-file')
    by_file.add_argument('--log', default=DEFAULT_PROJECT_OBSERVATIONS_LOG)
    by_file.add_argument('file_path')
    by_file.add_argument('--limit', type=int, default=20)
    return parser.parse_args()


def payload_from_args(args: argparse.Namespace) -> dict:
    return {
        'origin': args.origin,
        'project_root': args.project_root,
        'cwd': args.cwd,
        'git_branch': args.git_branch,
        'worktree_root': args.worktree_root,
        'event_type': args.event_type,
        'title': args.title,
        'summary': args.summary,
        'files_read': args.file_read,
        'files_modified': args.file_modified,
        'commands': args.commands,
        'tests': args.test,
        'decisions': args.decision,
        'next_steps': args.next_step,
        'memory_ids': args.memory_id,
        'source_hash': args.source_hash,
    }


def main() -> int:
    args = parse_args()
    store = ProjectObservationStore(args.log)
    if args.command == 'append':
        print(json.dumps(store.append(payload_from_args(args)), sort_keys=True))
        return 0
    if args.command == 'recent':
        rows = store.recent(limit=args.limit, origin=args.origin, event_type=args.event_type)
        print(json.dumps({'rows': rows, 'stats': store.stats()}, sort_keys=True))
        return 0
    if args.command == 'by-file':
        print(json.dumps({'rows': store.by_file(args.file_path, limit=args.limit)}, sort_keys=True))
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
