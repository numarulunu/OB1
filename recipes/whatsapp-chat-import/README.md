# WhatsApp Chat Import

Import WhatsApp text exports into Open Brain as distilled, duplicate-checked memories.

This recipe supports:

- Raw WhatsApp `_chat.txt` exports with bracketed timestamps like `[01.05.2026, 09:00:00] Name: message`
- Processed summary `.txt` files under a `Processed` folder
- Source directories, individual `.txt` files, and zip archives

It does not print raw chat content. Console output and logs are limited to counts, paths, chunk IDs, and status.

## Plan Only

```powershell
python recipes\whatsapp-chat-import\import-whatsapp.py "C:\Users\Gaming PC\Downloads\Memory\Training Data\WhatsApp" --plan-only
```

## Processed Summaries First

```powershell
python recipes\whatsapp-chat-import\import-whatsapp.py "C:\Users\Gaming PC\Downloads\Memory\Training Data\WhatsApp" --skip-raw --limit-chunks 15
```

## Raw Exports

```powershell
python recipes\whatsapp-chat-import\import-whatsapp.py "C:\Users\Gaming PC\Downloads\Memory\Training Data\WhatsApp" --skip-processed --limit-chunks 25
```

## Useful Options

- `--dry-run` extracts with the LLM but does not insert or mark chunks synced.
- `--limit-chunks N` processes only N unsynced chunks.
- `--max-days N` controls the raw-chat time window per chunk.
- `--max-chars N` caps the approximate LLM input size per chunk.
- `--sync-log PATH` sets the resume log.
- `--run-log PATH` sets the operational log.

Default model: `qwen/qwen3-235b-a22b-2507` through OpenRouter.
