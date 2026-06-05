# Claude Code History Import

Import local Claude Code JSONL history into Open Brain as durable, searchable thoughts.

This recipe is modeled after the ChatGPT conversation importer, but it handles Claude Code's local `~/.claude/projects/**/*.jsonl` format. It first strips deterministic noise such as tool calls, tool results, thinking blocks, attachments, and file snapshots. Then it can either send the cleaned dialogue to an LLM for memory extraction or import the cleaned dialogue directly with `--raw`.

## What It Does

1. Finds Claude Code `.jsonl` session files.
2. Keeps only user and assistant text.
3. Removes tool payloads, hidden thinking, hook output, attachments, and file snapshots before any LLM call.
4. Filters trivial sessions before spending tokens.
5. Extracts 0-5 standalone memories per useful session.
6. Inserts thoughts into Open Brain with metadata linking back to the Claude project, session ID, path, timestamps, and extractor model.

Default extractor:

```text
qwen/qwen3-235b-a22b-2507
```

Fallback extractor:

```text
openai/gpt-4o-mini
```

## Prerequisites

- Working Open Brain setup.
- Python 3.10+.
- Local Claude Code history at `~/.claude/projects` or another folder of `.jsonl` files.
- OpenRouter API key for LLM extraction and embeddings.
- Supabase URL and service role key for live import.

Install dependencies from this recipe folder:

```bash
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
$env:OPENROUTER_API_KEY="sk-or-v1-your-key"
$env:SUPABASE_URL="https://YOUR_PROJECT_REF.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
```

## Safe First Run

Run a raw dry-run first. This does not need API keys and does not write to Open Brain or the sync log.

```bash
python import-claude-history.py ~/.claude/projects --dry-run --raw --limit 5 --report claude-dry-run.md
```

Then run an extraction dry-run. This uses OpenRouter but still does not write to Open Brain.

```bash
python import-claude-history.py ~/.claude/projects --dry-run --limit 5 --report claude-extraction-dry-run.md
```

For larger runs, use an extraction cache so paid LLM work is saved and reusable:

```bash
python import-claude-history.py ~/.claude/projects --dry-run --extraction-cache claude-extraction-cache.jsonl --report claude-extraction-report.md
```

## Live Import

After reviewing the report:

```bash
python import-claude-history.py ~/.claude/projects --report claude-import-report.md
```

The sync log `claude-history-sync-log.json` prevents re-importing the same session content on later runs. Dry-runs do not mark sessions as imported.

If you already created an extraction cache during dry-run, pass the same cache path during live import. Cached sessions are reused instead of extracted again.

```bash
python import-claude-history.py ~/.claude/projects --extraction-cache claude-extraction-cache.jsonl --report claude-import-report.md
```

## Import Existing Kontext Memory

Kontext entries are already distilled memories, so this path does not use an LLM. It exports or imports the existing Kontext `entries` table directly.

Safe export first:

```bash
python import-kontext-memory.py --dry-run --export kontext-memory-export.jsonl
```

Live import after Open Brain credentials are set:

```bash
python import-kontext-memory.py
```

## Useful Options

| Flag | Description | Default |
|------|-------------|---------|
| `--dry-run` | Parse and extract without writing to Open Brain | Off |
| `--raw` | Skip LLM extraction and import cleaned session dialogue as one reference thought | Off |
| `--limit N` | Max sessions to process | `0` unlimited |
| `--after YYYY-MM-DD` | Only sessions on or after this date | None |
| `--before YYYY-MM-DD` | Only sessions on or before this date | None |
| `--focus TOPICS` | Focus extraction on a preset or custom topic description | All |
| `--model openrouter` | Use OpenRouter for extraction | `openrouter` |
| `--openrouter-model ID` | Primary extraction model | `qwen/qwen3-235b-a22b-2507` |
| `--fallback-openrouter-model ID` | Fallback if primary model fails | `openai/gpt-4o-mini` |
| `--model ollama` | Use local Ollama extraction | Off |
| `--ollama-model NAME` | Ollama model name | `qwen3` |
| `--min-messages N` | Skip sessions with fewer extracted messages | `2` |
| `--min-words N` | Skip short sessions below 10 messages | `50` |
| `--max-words N` | Skip very large sessions to avoid token spend | `50000` |
| `--max-dialogue-chars N` | Cap session prompt sent to the extractor | `120000` |
| `--report FILE` | Write a markdown import report | None |
| `--extraction-cache FILE` | Save/reuse extracted memories as JSONL | None |

Focus presets: `tech`, `strategy`, `personal`, `creative`, `all`.

## What Gets Stored

Each imported thought has content like:

```text
[Claude: ProjectName] The user decided to use Qwen3-235B as the default bulk memory extractor because it performed well in the bake-off and is much cheaper than GPT-4o-mini.
```

Metadata includes:

```json
{
  "source": "claude_history",
  "claude_project": "ProjectName",
  "claude_session_id": "session-id",
  "source_path": ".../session-id.jsonl",
  "cwd": "...",
  "first_timestamp": "...",
  "last_timestamp": "...",
  "message_count": 42,
  "extractor_model": "qwen/qwen3-235b-a22b-2507"
}
```

## Tests

From the OB1 repo root:

```bash
python -m pytest recipes/claude-code-history-import/test_claude_jsonl_parser.py -q
```
