# Mem0 VPS Staging Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a self-hosted Mem0 staging runtime on the existing VPS stack, import the 300-card OB1 Human Memory V2 pilot, and test whether Mem0 retrieval is good enough to become the final runtime.

**Architecture:** Keep OB1 as the canonical staging/audit layer. Run Mem0 OSS as an isolated Docker Compose stack on the app server, expose the dashboard and REST API through existing Pangolin/Newt routing, then import only curated V2 cards with `infer=false` so Mem0 stores the distilled memories instead of re-extracting raw chats.

**Tech Stack:** Mem0 OSS server/dashboard, Docker Compose, pgvector Postgres, Pangolin/Newt, Python import bridge at `recipes/shadow-cleanup/review_mem0_pipeline.py`.

---

## Facts Already Verified

- App server SSH works: `root@178.104.203.128`.
- Pangolin server SSH works: `root@89.167.94.30`.
- `newt-pangolin` is active on the app server.
- Pangolin containers are running on the Pangolin server.
- `mem0.ionutrosu.xyz` and `mem0-api.ionutrosu.xyz` already resolve to `89.167.94.30`.
- Both Mem0 hostnames currently return Pangolin `404`, meaning DNS exists but resources are not routed yet.
- App-server host port `3000` is already used by Cal.com, so Mem0 must not bind host port `3000`.
- Use `127.0.0.1:13000` for the dashboard and `127.0.0.1:18888` for the API.

## File Map

| Action | Path | Purpose |
| --- | --- | --- |
| Remote create | `/opt/mem0-staging/` | Isolated Mem0 staging stack on app server |
| Remote create | `/opt/mem0-staging/src/` | Official Mem0 repo clone |
| Remote create | `/opt/mem0-staging/src/server/.env` | Mem0 runtime config, generated from template plus secrets |
| Remote modify | Pangolin config via API | Add two public resources: `mem0` and `mem0-api` |
| Local use | `.local/open-brain-cleanup/human-v2/mem0/mem0-pilot-top300-requests.jsonl` | First import batch |
| Local use | `.local/open-brain-cleanup/human-v2/mem0/mem0-full-import-requests.jsonl` | Full import only after pilot succeeds |

## Required Secret Inputs

These values must be read from local config/env or generated, never printed:

- `OPENAI_API_KEY` or another Mem0-supported key for embeddings/LLM defaults. The ChatGPT subscription does not automatically provide this; Mem0 runtime operations need an API key.
- Mem0 `ADMIN_API_KEY`, generated server-side.
- Mem0 `JWT_SECRET`, generated server-side.
- Pangolin admin email/password from `self-hosting-setup/config.txt`, used only to create resources.

## Task 1: Non-Destructive Preflight

- [ ] Confirm no existing Mem0 containers or target ports are occupied.

Run:

```powershell
ssh root@178.104.203.128 "docker ps --format '{{.Names}}\t{{.Ports}}' | grep -Ei 'mem0|:13000|:18888' || true"
```

Expected: no active Mem0 rows and no `13000` or `18888` host-port conflicts.

- [ ] Confirm Pangolin resources are not already configured.

Run:

```powershell
curl.exe -sk -o NUL -w "%{http_code}" https://mem0.ionutrosu.xyz
curl.exe -sk -o NUL -w "%{http_code}" https://mem0-api.ionutrosu.xyz/docs
```

Expected: current result is `404` until resources are added.

## Task 2: Deploy Mem0 Staging On The App Server

- [ ] Create the isolated staging directory and clone/update official Mem0.

Run only after approval:

```powershell
ssh root@178.104.203.128 "mkdir -p /opt/mem0-staging && cd /opt/mem0-staging && if [ -d src/.git ]; then cd src && git pull --ff-only; else git clone --depth 1 https://github.com/mem0ai/mem0.git src; fi"
```

Expected: `/opt/mem0-staging/src/server/docker-compose.yaml` exists.

- [ ] Generate server-side Mem0 secrets without printing them to chat.

Run only after approval:

```powershell
ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && cp -n .env.example .env && chmod 600 .env && grep -q '^ADMIN_API_KEY=.' .env || sed -i \"s|^ADMIN_API_KEY=.*|ADMIN_API_KEY=$(openssl rand -hex 32)|\" .env && grep -q '^JWT_SECRET=.' .env || sed -i \"s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -hex 32)|\" .env"
```

Expected: `.env` exists with generated `ADMIN_API_KEY` and `JWT_SECRET`.

- [ ] Fill non-secret public runtime settings and disable telemetry.

Run only after approval:

```powershell
ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && sed -i 's|^POSTGRES_HOST=.*|POSTGRES_HOST=postgres|;s|^POSTGRES_PORT=.*|POSTGRES_PORT=5432|;s|^POSTGRES_DB=.*|POSTGRES_DB=mem0|;s|^POSTGRES_USER=.*|POSTGRES_USER=postgres|;s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=postgres|;s|^POSTGRES_COLLECTION_NAME=.*|POSTGRES_COLLECTION_NAME=mem0_memories|;s|^AUTH_DISABLED=.*|AUTH_DISABLED=false|;s|^DASHBOARD_URL=.*|DASHBOARD_URL=https://mem0.ionutrosu.xyz|;s|^MEM0_TELEMETRY=.*|MEM0_TELEMETRY=false|' .env"
```

Expected: `.env` points Mem0 to its bundled Postgres and public dashboard URL.

- [ ] Add the API key for embeddings/LLM defaults.

Run only after the API key source is confirmed:

```powershell
# Use an existing local env var or config value; do not echo it.
# Example shape only:
# ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && printf '\nOPENAI_API_KEY=%s\n' '<secret-value>' >> .env"
```

Expected: `.env` has a real `OPENAI_API_KEY` or equivalent supported provider key.

- [ ] Patch host ports so Mem0 does not collide with Cal.com.

Run only after approval:

```powershell
ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && cp docker-compose.yaml docker-compose.yaml.bak.$(date +%Y%m%d%H%M%S) && sed -i 's|\"8888:8000\"|\"127.0.0.1:18888:8000\"|;s|\"3000:3000\"|\"127.0.0.1:13000:3000\"|;s|NEXT_PUBLIC_API_URL=http://localhost:8888|NEXT_PUBLIC_API_URL=https://mem0-api.ionutrosu.xyz|' docker-compose.yaml"
```

Expected: API is local-only on `18888`; dashboard is local-only on `13000`.

- [ ] Start Mem0 staging.

Run only after approval:

```powershell
ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && docker compose pull postgres || true && docker compose up -d --build"
```

Expected: `mem0`, `postgres`, and `mem0-dashboard` containers start.

## Task 3: Route Pangolin Resources

- [ ] Add `mem0.ionutrosu.xyz` to target app-server localhost port `13000`.
- [ ] Add `mem0-api.ionutrosu.xyz` to target app-server localhost port `18888`.

Implementation detail: use the existing Pangolin API pattern in `self-hosting-setup/CLAUDE.md`, with cookie auth from `config.txt`. Required resource fields are `http: true`, `protocol: "tcp"`, `siteId`, `domainId`, `subdomain`, and target `{ "ip": "127.0.0.1", "port": <port>, "method": "http", "enabled": true }`.

Expected: public dashboard and API hostnames stop returning Pangolin `404`.

## Task 4: Verify Mem0 Before Import

- [ ] Check local API and dashboard from the app server.

Run:

```powershell
ssh root@178.104.203.128 "curl -s -o /dev/null -w 'dashboard=%{http_code}\n' http://127.0.0.1:13000 && curl -s -o /dev/null -w 'api_docs=%{http_code}\n' http://127.0.0.1:18888/docs"
```

Expected: dashboard returns a real HTTP code, API docs return a real HTTP code, not connection failure.

- [ ] Check public HTTPS routes.

Run:

```powershell
curl.exe -sk -o NUL -w "dashboard=%{http_code}`n" https://mem0.ionutrosu.xyz
curl.exe -sk -o NUL -w "api_docs=%{http_code}`n" https://mem0-api.ionutrosu.xyz/docs
```

Expected: no `404`; TLS route works through Pangolin.

## Task 5: Import The 300-Card Pilot

- [ ] Export the Mem0 admin API key from the server into the local shell without printing it.

Run:

```powershell
$env:MEM0_API_KEY = (ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && sed -n 's/^ADMIN_API_KEY=//p' .env").Trim()
```

Expected: `$env:MEM0_API_KEY.Length` is non-zero. Do not print the value.

- [ ] Dry-run the import command.

Run:

```powershell
python recipes\shadow-cleanup\review_mem0_pipeline.py import-mem0 --requests .local\open-brain-cleanup\human-v2\mem0\mem0-pilot-top300-requests.jsonl --base-url https://mem0-api.ionutrosu.xyz --api-key-env MEM0_API_KEY
```

Expected: `dry_run=true`, `request_count=300`, `attempted=0`.

- [ ] Execute the pilot import.

Run:

```powershell
python recipes\shadow-cleanup\review_mem0_pipeline.py import-mem0 --requests .local\open-brain-cleanup\human-v2\mem0\mem0-pilot-top300-requests.jsonl --base-url https://mem0-api.ionutrosu.xyz --api-key-env MEM0_API_KEY --execute
```

Expected: `succeeded=300`, `failed=0`. Log file contains paths/statuses only, not card text.

## Task 6: Retrieval Pilot Gate

- [ ] Ask 10 practical retrieval questions against the pilot memory, covering psychology, relationships, AI workflows, opera, business/systems, and current project architecture.
- [ ] Compare retrieved memories with the Desktop V2 review dashboard.
- [ ] If retrieval is sharp, import the full `2,189` cards.
- [ ] If retrieval is weak, adjust Mem0 metadata/search strategy before full import.

## Rollback

Rollback should not touch OB1 or Supabase.

Stop Mem0 staging:

```powershell
ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && docker compose stop"
```

Full removal if the pilot is rejected:

```powershell
ssh root@178.104.203.128 "cd /opt/mem0-staging/src/server && docker compose down && cd /opt && mv mem0-staging mem0-staging.disabled.$(date +%Y%m%d%H%M%S)"
```

Pangolin rollback: delete or disable only the two resources `mem0` and `mem0-api` in Pangolin. Do not alter existing n8n/Cal.com/Vocality resources.

## Decision Gate

Proceed only after explicit approval for these remote mutations:

1. Create `/opt/mem0-staging` on the app server.
2. Clone/build Mem0 containers.
3. Add two Pangolin resources.
4. Import the 300-card curated pilot into Mem0.
