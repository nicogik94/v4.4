# Codex Wave Executor Template

Use this prompt to execute one implementation wave from a wave spec.

## Prompt

You are working in repo:

`/home/nicolas/dev/v4.4/mas`

Wave spec:

`<path-to-wave-spec>`

Expected branch:

`<expected-wave-branch>`

Task:

Implement only the wave described in the spec. Do not implement adjacent product, runtime, API, auth, database, exporter, prompt, model-routing, Docker Compose, GitHub workflow, or test behavior unless the spec explicitly allows it.

Hard rules:

- Do not commit unless explicitly asked to use `scripts/wave_commit.sh`.
- Do not push unless explicitly asked to use `scripts/wave_pr.sh`.
- Never merge PRs.
- Stop if the current branch is not the expected branch.
- Stop if `git status --short` is not clean at preflight.
- Stop if the implementation requires forbidden files.
- Stop before editing any file not listed as allowed by the wave spec.

Preflight:

Run and verify:

```bash
pwd
git branch --show-current
git status --short
git log --oneline -5
```

Then read the full wave spec before making changes.

Implementation:

- Implement only the requested wave.
- Keep edits minimal and directly tied to the spec goal.
- Do not perform broad refactors or unrelated cleanup.
- Do not touch forbidden files.
- If a required change appears to need a forbidden file, stop and report the conflict.

Verification:

- Run `git diff --check`.
- Run every targeted test listed in the wave spec.
- Run the full suite:

```bash
PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests -q
```

Artifact cleanup:

- Restore known test artifacts before any commit.
- Use `scripts/wave_clean_artifacts.sh` for tracked `scenario_shadow.sqlite3` and tracked `__pycache__/*.pyc` artifacts.
- Do not delete untracked files automatically.
- Do not use `git clean`.

Commit and PR:

- Commit only with explicit file paths via `scripts/wave_commit.sh`.
- Do not stage all files.
- Do not commit test artifacts.
- Open a PR only with `scripts/wave_pr.sh` when explicitly instructed.
- Never merge a PR.

Final report:

- Current `git status --short`.
- `git diff --name-only`.
- `git diff --stat`.
- Changed files.
- Tests run and results.
- Commit hash if a commit was created.
- PR URL if a PR was opened.
- Any skipped steps or blockers.
