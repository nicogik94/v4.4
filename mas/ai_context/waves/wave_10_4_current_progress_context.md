# Wave 10.4 — Refresh v4/v5 current progress context

## Goal

Update the project AI context so future Codex/ChatGPT sessions know exactly where v4/v5 stands after Waves 10.1, 10.2A, 10.2B, 10.2C, 10.2D, and 10.3.

## Required output

Create or update a concise current-progress context document under `ai_context/`.

Preferred file:

- `ai_context/v4_v5_current_progress.md`

## Must include

1. Current branch baseline:
   - `main`
   - latest known commit: `d4ce78d Auto-clean test artifacts after wave verification (#55)`

2. Completed hardening/productization waves:
   - Wave 10.1: pytest CI and provenance foundation
   - Wave 10.2A: stale workflow run recovery
   - Wave 10.2B: public exposure preflight interlock
   - Wave 10.2C: wave automation runbook/scripts
   - Wave 10.3: operator auth control-plane hardening
   - Wave 10.2D: auto-clean test artifacts after wave verification

3. Current safety posture:
   - local/operator-first
   - not public SaaS
   - public exposure is blocked by preflight unless explicitly and safely configured
   - operator auth exists for protected write/control-plane endpoints
   - `/health` remains open
   - no multi-tenant/public-user system yet

4. Current verified test status:
   - local full suite recently green: `696 passed, 1 warning, 67 subtests passed`
   - PR #55 GitHub pytest CI passed

5. Current wave automation workflow:
   - `scripts/wave_sync_main.sh`
   - `scripts/wave_start.sh`
   - `scripts/wave_verify.sh`
   - `scripts/wave_clean_artifacts.sh`
   - `scripts/wave_commit.sh`
   - `scripts/wave_pr.sh`

6. Git hygiene:
   - never commit `scenario_shadow.sqlite3`
   - never commit pycache, `.pyc`, exports, upload_store, zip files, or local docker-compose edits
   - `wave_verify.sh` now cleans known tracked test artifacts automatically

7. Recommended next implementation candidates:
   - operator authenticated startup/runbook smoke
   - release readiness checklist
   - export/provenance polish
   - Technology Readiness UX/output polish
   - monitoring template polish

## Forbidden changes

Do not change app behavior.

Do not touch:
- `api.py`
- `config.py`
- `runtime/preflight.py`
- exporters
- prompts
- tests
- GitHub workflows
- Docker Compose files
- generated artifacts
- `scenario_shadow.sqlite3`

## Verification

Run:

- `git diff --check`
- `scripts/wave_verify.sh`

Expected final status should include only the context/wave markdown files.
