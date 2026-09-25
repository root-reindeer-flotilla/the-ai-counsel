# Integrate local llm-council-plus fork with upstream (The AI Counsel v0.13.1)

## Context

`~/projects/llm-council-plus` tracks `jacob-bd/llm-council-plus` as `origin`. Local `main` is **7 commits ahead** of the last sync (`58009fa`, 2026-02-18). Those commits are **not pushed anywhere**, because `origin` is the upstream author's repo. They add:
- Requesty.ai provider (`backend/requesty.py`, `backend/providers/requesty.py`, settings/config/UI)
- Resumable run management (`backend/runs.py` + API routes + frontend)
- Balanced cyclic-permutation Stage 2 ordering (`_build_stage2_candidates`, `_deterministic_cyclic_orders`, `_dedupe_valid_order` in `backend/council.py`)
- Token usage / `response_id` in providers, thinking-content normalization, temperature forcing, OpenRouter fixes, ~10 backend test files, frontend tests, bun.lock

Upstream moved on:
- `llm-council-plus` added 92 commits (v0.4 → v0.7.0) and is now **deprecated**.
- Development continues in **`jacob-bd/the-ai-counsel`** (v0.13.1, 2026-09-18). It has **separate git history**: its root commit `31c21d8` is llm-council-plus v0.7.0 with a rebrand applied (`llm_council_mcp` → `the_ai_counsel_mcp`, etc.; see its `docs/MIGRATION.md`). Data formats and `/api/*` stay compatible.

Goal: move to the-ai-counsel v0.13.1 while keeping every local feature, and keep the history so future upstream merges are plain `git merge`s.

Trial merges in a scratch clone gave these conflict counts:
- Step A (merge llm-council-plus v0.7.0): 15 conflicting files.
- A single direct merge with the-ai-counsel would conflict in 25 files.

That is why the plan uses two checkpointed steps.

## Approach

### 0. Safety / prep (in `~/projects/llm-council-plus`)
- `git stash push -u -m pre-integration` (uncommitted `uv.lock` tweak + untracked `.vscode/llm-council-plus.code-workspace`).
- `git branch backup/pre-integration main` and `git bundle create ~/llm-council-plus-pre-integration.bundle --all`. This is an off-repo backup, since the local commits exist nowhere else.
- `cp -r data ~/llm-council-plus-data-backup`.
- Remotes: `git remote rename origin lcp` and `git remote add upstream https://github.com/jacob-bd/the-ai-counsel.git`, then `git fetch lcp upstream`.
- Work on a branch: `git switch -c integrate/ai-counsel main`.

### Step A: merge llm-council-plus v0.7.0 (`lcp/main`, `8351aa1`)
`git merge lcp/main`. Resolve the 15 conflicts: `.gitignore`, `CLAUDE.md`, `README.md`, `backend/council.py` (7 hunks), `backend/main.py` (6), `frontend/src/App.jsx` (7), `api.js`, `ChatInterface.jsx`, `CouncilGrid.jsx`, `Settings.jsx`, `Stage2.jsx` (4), `settings/CouncilConfig.jsx`, `utils/modelHelpers.js`, `pyproject.toml`, `uv.lock`.

The principle is to **take upstream's structure and re-apply local features on top**, not the reverse (see the resolution guide below). Regenerate the lockfile with `uv lock` rather than hand-merging it. Run the checks in the Verification section, then commit.

### Step B: merge The AI Counsel v0.13.1 with a temporary graft
The two repos share no commit, so give git a correct merge base:
```
git replace --graft 31c21d8 8351aa1     # pretend counsel's root commit descends from lcp final
git merge upstream/main                 # base = 8351aa1 → the rebrand + v0.8–v0.13 arrive as normal diffs
git replace -d 31c21d8                  # remove graft after the merge commit exists
```
After the merge, future updates are plain `git fetch upstream && git merge upstream/main`, because the merge commit already links both histories. The expected overlap is mostly `backend/main.py` (+834 upstream), `settings.py`, providers, `Settings.jsx`, `ProviderSettings.jsx`, `CouncilConfig.jsx`, `App.jsx`, `start.sh`, `README.md` and `uv.lock`.

### Conflict resolution guide (both steps)

| Area | Resolution |
|---|---|
| `backend/providers/*.py` (anthropic, openai, groq, mistral, deepseek, google, custom_openai), `ollama_client.py`, `openrouter.py` | Take upstream. It now returns `"usage"` itself. Re-add `response_id` / `total_tokens` only where `runs.py` or `council._extract_total_tokens` reads them (grep first). |
| Temperature forcing (`_normalize_model_for_rules`, `_should_force_temperature_one` in council.py) | Drop these in favor of upstream `backend/providers/temperature.py`. Port any model rule it lacks into that file. |
| `backend/council.py` Stage 2 | Keep upstream `stage2_collect_rankings` / `calculate_aggregate_rankings` and re-insert the cyclic-permutation helpers and their call site. Keep the local thinking-normalization helpers (`strip_thinking_tags`, `normalize_thinking_content`, …) unless upstream has equivalents. |
| Requesty provider | Keep `backend/requesty.py`, `providers/requesty.py`, the `PROVIDERS` entry and `REQUESTY_API_URL`/`get_requesty_api_key` in config. **Also add** (these are not conflicts, but needed for Requesty to work with upstream's newer code): `"requesty_api_key": "api:requesty"` in `backend/credentials/ids.py`, `"requesty"` in `INTERNAL_PROVIDER_PREFIXES` in `providers/temperature.py`, the key field in main.py's settings request model and update handler (mirror `openrouter_api_key` around main.py:1721/1958), and the provider in `settings.py` defaults. |
| Resumable runs (`backend/runs.py`, routes in `main.py`) | Keep `runs.py` as-is and re-add its routes into upstream's `main.py`. Check that it still fits upstream's new streaming, debate (Stage 4) and live-progress flow. At minimum a council-only run must resume. |
| Frontend (`App.jsx`, `api.js`, `Settings.jsx`, `settings/ProviderSettings.jsx`, `settings/CouncilConfig.jsx`, `Stage2.jsx`, `ChatInterface.jsx`, `CouncilGrid.jsx`, `modelHelpers.js`) | Take upstream files and re-add the local pieces: the Requesty section in ProviderSettings, resume-run calls in `api.js`/`App.jsx`, the Stage 2 ordering display, and the 12-member cap if upstream lacks it. |
| `pyproject.toml`, `frontend/package.json` | Union of dependencies, then `uv lock` and a fresh frontend lockfile. Upstream uses npm; drop `frontend/bun.lock` unless you run bun. |
| `README.md`, `CLAUDE.md`, `.gitignore`, `start.sh` | Take upstream docs. Append a short "Fork additions" section to README/CLAUDE.md. Take the union for `.gitignore`. For `start.sh`, take upstream and re-apply your tweaks. |

### Post-merge
- `git stash pop` and decide on the old `uv.lock` tweak (it's probably obsolete after the relock).
- Delete the fully merged branches `requesty`, `reindeerflotilla` and `stage2-balanced-cyclic-permutation` (0 unique commits each), and `backup/*` once satisfied.
- Update the MCP registration per `docs/MIGRATION.md` (`claude mcp remove llm-council` → add `the-ai-counsel` pointing at `python -m the_ai_counsel_mcp`).
- Optional: create your own GitHub fork and push `main` there, so the work has a remote backup.
- Optional: rename the directory to `~/projects/the-ai-counsel`.

## Verification (after Step A, and again after Step B)
1. `uv sync && uv run pytest backend/tests` (Step B also `the_ai_counsel_mcp/tests`). All your tests (`test_stage2_permutation`, `test_runs_resume`, `test_rankings_aggregation`, …) and upstream's must pass.
2. `npm install --prefix frontend && npm test --prefix frontend` (vitest: `api.test.js`, `Stage2.test.js`, `modelHelpers.test.js`), then `npm run build --prefix frontend`.
3. `./start.sh` and `curl localhost:8001/api/health`. In the UI, check that:
   - existing conversations from `data/` load;
   - a council run using a Requesty model and an OpenRouter model completes all stages, with Stage 2 showing the balanced ordering;
   - killing the backend mid-run and resuming the run works;
   - Settings saves and reloads the Requesty key.
4. `git log --graph --oneline -15` shows your 7 commits plus both upstream lines. `git merge-base HEAD upstream/main` (with the graft removed) returns upstream HEAD.
