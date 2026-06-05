# Gemini Conversation Import

Import Gemini text exports into Open Brain as curated memories, not raw transcript dumps.

## What It Does

- Reads Gemini `.txt` files from folders, zip archives, or individual files.
- Excludes merged corpus files by default to avoid reimporting account archives twice.
- Dedupes chats by normalized content hash across all sources.
- Splits oversized chats into bounded LLM chunks.
- Uses OpenRouter extraction, then Supabase direct insert with embeddings.
- Tracks imported chunks in `.local/open-brain-imports/gemini-sync-log.json`.
- Logs only counters and IDs to `.local/open-brain-imports/_import-gemini.log`.

## Plan First

```powershell
python recipes\gemini-conversation-import\import-gemini.py `
  "C:\path\to\Gemini-Chats-Archive-2.zip" `
  "C:\path\to\Gemini Chats" `
  "C:\path\to\Gemini-Chats-Ionutowscky-Archive.zip" `
  --plan-only
```

## Live Import

```powershell
python recipes\gemini-conversation-import\import-gemini.py <sources...> `
  --openrouter-model qwen/qwen3-235b-a22b-2507
```

Use `--limit-chunks N` for a smoke run. The importer skips chunks already present in the Gemini sync log.

## Notes

Do not pass merged files unless you intentionally want to process a concatenated corpus. Use `--include-merged` only for validation or special cases.
