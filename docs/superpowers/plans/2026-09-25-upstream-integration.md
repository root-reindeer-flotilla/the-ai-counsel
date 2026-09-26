# Upstream Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the fork's nine local features (F1–F9) onto The AI Counsel v0.13.1 through two history-preserving merges, so future upstream syncs are a plain `git merge upstream/main`.

**Architecture:** Two mechanical merges come first. Step A merges llm-council-plus v0.7.0 and takes its side of every conflicted file. Step B merges The AI Counsel through a temporary graft `31c21d8 → 8351aa1` and takes upstream's side. Then each local feature is re-applied once, test-first, on top of v0.13.1, in its own commit. The fork's own tests serve as the failing tests. Upstream's structure wins everywhere, and local code adapts to upstream's interfaces. The Stage 2 generator contract, provider wiring, and the credential store are all upstream's.

**Tech Stack:** Git (`replace --graft`, merge), Python 3.10+/FastAPI/pytest (+pytest-asyncio, anyio), uv, React 19/Vite, vitest, `node:test`, npm.

**Spec:** `docs/superpowers/specs/2026-09-25-upstream-integration-design.md`. Read it first. Decisions D1–D10 and features F1–F9 referenced below are defined there. The spec's "Confirmed decisions" section (1–11) records the owner's answers to this plan's open questions and wins over anything below that disagrees.

**Execution:** subagent-driven (decision 5). Automated checks run in the execution session. Manual UI checks and the local cutover (Task 15) are the owner's (decisions 4 and 8).

## Global Constraints

- Work in a **full** clone of `root-reindeer-flotilla/the-ai-counsel` (`git fetch --unshallow` if shallow), on branch `integrate/ai-counsel`. Remotes: `origin` (this repo), `lcp` = `https://github.com/jacob-bd/llm-council-plus.git`, `upstream` = `https://github.com/jacob-bd/the-ai-counsel.git`.
- Fixed commits: fork head `a56aa0f` before the plan cherry-pick (the brief is `1de4bfb`; `a56aa0f` only un-ignores `uv.lock`); fork backup `0ffffa4` (`origin/backup/pre-integration`); llm-council-plus final `8351aa1`; The AI Counsel root `31c21d8`; The AI Counsel v0.13.1 `614dfb9`. If `upstream/main` has moved past `614dfb9`, merge the newer head and expect extra conflicts, resolved by the same rules.
- The graft is temporary: create it only for the Step B merge, delete it right after (`git replace -d 31c21d8`), and never push `refs/replace/*`.
- Never rebase, amend, or force-push `integrate/ai-counsel`. Both merge commits must keep their two parents.
- Merge commits contain conflict resolution only. Feature code goes in the follow-up task commits.
- Keep upstream's Stage 2 generator contract: the first yielded item is a flat `Dict[str, str]` of `"Response X" → model` (spec D3).
- Council member cap is `12`, from the single constant `backend.settings.MAX_COUNCIL_MEMBERS` (spec D8).
- Backend modules use relative imports (`from .x import y`), matching the codebase.
- Package manager is npm. Do not add bun files.
- Never skip, `xfail`, or delete a test to get green. A fork test may be edited only to follow an upstream API change (a new import path or keyword argument), and it must still check the same behavior.
- Secrets never go in `data/settings.json`. API keys go through `backend/credentials` (`api:<provider>`).
- **Task 16 is gated.** Never start Task 16 (cleanup) on your own, even when Task 15 is done. Start it only when the owner explicitly says "start task 16" (owner instruction, 2026-09-25).
- Scratch notes go in `$SCRATCH` (`export SCRATCH=$(mktemp -d)` once per shell). Never commit them.

## Adapting This Plan As You Go

This plan was written from trial merges and a reading of the code, not from a finished integration. Expect surprises: an upstream commit newer than `614dfb9`, a conflict the trial didn't show, a fork test whose intent only becomes clear once it runs, or an upstream helper that already covers a fork feature. **Re-planning when that happens is expected and encouraged.** Following a step that no longer fits reality is worse than changing it.

When something comes up:

1. **Stop and write it down.** Put what you found and why the current step doesn't fit in `$SCRATCH/replan-notes.md`.
2. **Change the plan itself.** Edit this file: add, split, reorder, or drop steps and tasks, and keep each task's **Files** and **Interfaces** blocks accurate. If a design decision changes, update the spec (`D1`–`D10`) in the same commit.
3. **Re-check downstream tasks.** A changed interface (for example the Stage 2 result keys or `RunManager`'s constructor) affects every later task that consumes it. Update those tasks before you continue.
4. **Commit the plan change on its own** (`docs(plan): …`) so the history shows why execution diverged.
5. **Ask when it's a judgment call.** If the change alters user-visible behavior, drops or reshapes a fork feature, or touches the Global Constraints, check with your human partner first. Mechanical adjustments (a renamed function, an extra conflicted file, a moved line number) don't need approval.

The Global Constraints and the "never" rules (no skipped tests, no history rewrites, no pushed replace refs) stay fixed. Everything else here is a best current guess.

## Review Focus

1. **Stage 2 edge inputs.** When one Stage 1 model fails, only successful models evaluate. With a single successful response, Stage 2 still yields a one-entry label map. An evaluator that writes unknown labels ("Response Z") or repeats labels must have those dropped from `parsed_ranking`, never mapped to a wrong model. Tests go in Task 8.
2. **Old conversations.** Saved Stage 2 items from before integration have no `stage2_label_map`. `Stage2.jsx` must fall back to the conversation's `label_to_model` and render exactly as before. Tests go in Task 12.
3. **Plaintext Requesty key from the fork.** Existing fork users have `requesty_api_key` in `data/settings.json`. On the first settings load (`GET /api/settings`, which the UI does on open; upstream's migration is lazy, not at process start) it must migrate into the credential store (`api:requesty`) and be removed from `settings.json`. `REQUESTY_API_KEY` in the environment must also work, and "Disconnect All Providers" must wipe `api:requesty`. Tests go in Task 9.
4. **Backend restart mid-run.** Runs are in memory. After a restart, `GET /api/conversations/{id}/runs/active` must not return 500 or hang: it returns the fork's `200 {"active_run": null}` for an existing conversation (404 only when the conversation itself is missing; see Task 10 execution notes). The stored conversation must keep the user message, and the UI must leave the loading state. Tests go in Task 10 (backend) and Task 12 (frontend).
5. **Debate runs keep one label space.** `debate.py` calls Stage 2 with `balanced_order=False`. Claim verdicts and paragraph annotations must keep referring to the same model in every round. Test goes in Task 8.

---

## Parallel Step A attempt (added 2026-09-25, during execution)

A second session, started from a stale shallow clone, did its own Step A merge before it saw this plan's progress. That merge is not used, because it re-applied fork features inside the merge commit, kept a typed Stage 2 first yield (against D3) and kept `bun.lock` (against D9). It is kept locally as `backup/local-step-a-8d67949` for reference only. These pieces of it were judged better than, or missing from, this plan, and are folded into the tasks below:

- **Task 8:** `prompt_override` forces canonical order even when `balanced_order` is left at its default, because a caller-built prompt already fixes the label order.
- **Task 10:** `RunManager` carries upstream's per-request options (`council_models`, `chairman_model`, `search_provider`, multi-turn history), runs model preflight like `/message/stream`, and awaits the now-async `generate_search_query`. The progress entry carries `run_id` and `event_count` so a client can re-attach with `from_event`. (The UI follows a run through `/progress` polling instead and uses `run_id` for Stop; spec decision 11.)
- **Task 10:** `_build_chat_history` strips thinking blocks (via `strip_thinking_tags`) before prior turns are reused as context.
- **Task 12:** the frontend event-handler, re-attach and Stop/navigate split, and the 12-slot council grid layout from that branch are reference implementations (`git show backup/local-step-a-8d67949:<path>`), adapted to v0.13.1 components.
- **Not restored:** the fork's sequential Ollama scheduling. Upstream runs every model in parallel and the owner chose to keep that (spec decision 10). Task 13 must not document sequential Ollama execution.

### Task 1: Prepare workspace, remotes, and backups

**Files:** none in the repo (git refs and an off-repo bundle only).

**Interfaces:**
- Produces: remotes `lcp` and `upstream` fetched; tag `pre-integration-2026-09-25` at the branch head just before the Step A merge (after the plan cherry-pick and the decisions commit), pushed to `origin`; optional bundle; local branch `integrate/ai-counsel` checked out.

- [ ] **Step 1: Get a full clone and the branch.**

  ```bash
  git clone https://github.com/root-reindeer-flotilla/the-ai-counsel.git ~/projects/the-ai-counsel-integration
  cd ~/projects/the-ai-counsel-integration
  git rev-parse --is-shallow-repository   # must print false; if true: git fetch --unshallow origin
  git switch integrate/ai-counsel
  git log --oneline -4                     # a56aa0f, then the cherry-picked plan and the decisions commit on top
  ```

- [ ] **Step 2: Add and fetch the two upstream remotes.**

  ```bash
  git remote add lcp https://github.com/jacob-bd/llm-council-plus.git
  git remote add upstream https://github.com/jacob-bd/the-ai-counsel.git
  git fetch lcp main
  git fetch upstream main
  ```

- [ ] **Step 3: Verify the facts the merges depend on. Stop if any check fails.**

  ```bash
  git rev-parse --short lcp/main                                   # expect 8351aa1
  git rev-list --max-parents=0 upstream/main | cut -c1-7           # expect 31c21d8
  git merge-base HEAD upstream/main || echo "no common ancestor"   # expect: no common ancestor
  git merge-base HEAD lcp/main | cut -c1-7                         # expect 58009fa
  git rev-parse --short upstream/main                              # 614dfb9 at planning time
  git replace -l                                                   # expect empty
  ```

- [ ] **Step 4: Create backups.**

  ```bash
  git tag pre-integration-2026-09-25 HEAD
  git push origin pre-integration-2026-09-25    # decision 6: a durable backup on origin. If tag pushes are refused (the cloud session's
                                                # proxy returns 403), create branch backup/pre-integration-2026-09-25 at HEAD through the GitHub API instead.
  git bundle create "$HOME/the-ai-counsel-pre-integration.bundle" --all   # optional; not durable in a cloud container
  git bundle verify "$HOME/the-ai-counsel-pre-integration.bundle"   # expect "is okay"
  ```

- [ ] **Step 5: Install toolchains for later checks.**

  ```bash
  uv --version && node --version && npm --version
  ```

  Expected: all three print versions. Node must be ≥ 20 (vitest 3 and `node --test`).

---

### Task 2: Step A: merge llm-council-plus v0.7.0 (mechanical)

**Files (conflict resolution only):**
- Resolve to the `lcp/main` side: `CLAUDE.md`, `README.md`, `backend/council.py`, `backend/main.py`, `frontend/src/App.jsx`, `frontend/src/api.js`, `frontend/src/components/ChatInterface.jsx`, `frontend/src/components/CouncilGrid.jsx`, `frontend/src/components/Settings.jsx`, `frontend/src/components/Stage2.jsx`, `frontend/src/components/settings/CouncilConfig.jsx`, `frontend/src/utils/modelHelpers.js`, `pyproject.toml`, `uv.lock`.
- Modify: `.gitignore`, the union of both sides minus the fork's `uv.lock` line.

**Interfaces:**
- Consumes: Task 1 remotes.
- Produces: merge commit `M_A` with parents `1de4bfb` and `8351aa1`. Fork-only files (`backend/runs.py`, `backend/requesty.py`, `backend/providers/requesty.py`, fork tests, `*.test.js`) are still present. Fork edits to non-conflicting files are kept as git auto-merged them.

- [ ] **Step 1: Start the merge.**

  ```bash
  git merge --no-ff --no-commit lcp/main
  git diff --name-only --diff-filter=U | tee "$SCRATCH/stepA-conflicts.txt"
  wc -l < "$SCRATCH/stepA-conflicts.txt"   # expect 15
  ```

  If the list differs from the 15 files above, stop and compare with the spec's trial-merge section before continuing.

- [ ] **Step 2: Take the llm-council-plus side of every conflicted file except `.gitignore`.**

  ```bash
  grep -v '^\.gitignore$' "$SCRATCH/stepA-conflicts.txt" | xargs git checkout --theirs --
  grep -v '^\.gitignore$' "$SCRATCH/stepA-conflicts.txt" | xargs git add --
  ```

- [ ] **Step 3: Resolve `.gitignore` as a union, dropping `uv.lock`.**

  Open `.gitignore`, keep every line from both sides, add `.coverage` if it's missing, delete the line `uv.lock`, and remove the conflict markers. Then:

  ```bash
  grep -c '^<<<<<<<\|^>>>>>>>' .gitignore   # expect 0
  grep -x 'uv.lock' .gitignore || echo ok   # expect ok
  git add .gitignore
  ```

- [ ] **Step 4: Smoke-check that the backend imports.**

  ```bash
  uv sync
  uv run python -c "import backend.main" && echo import-ok
  ```

  Expected: `import-ok`. If a fork-only module (for example `backend/runs.py`) raises `ImportError` for a name that llm-council-plus renamed, note it in `$SCRATCH/stepA-notes.txt` and continue. Tasks 5–10 fix fork modules against v0.13.1, not v0.7.0.

- [ ] **Step 5: Commit the merge.**

  ```bash
  git commit -m "merge: llm-council-plus v0.7.0 (lcp/main 8351aa1)

  Mechanical resolution: take llm-council-plus for all 14 conflicted files,
  union .gitignore without the uv.lock line. Fork features are re-applied on
  top of The AI Counsel v0.13.1 in follow-up commits."
  git log -1 --format='%p'   # expect two parents: 1de4bfb… 8351aa1…
  ```

---

### Task 3: Step B: merge The AI Counsel v0.13.1 through a temporary graft (mechanical)

**Files (conflict resolution only):**
- Resolve to the `upstream/main` side: `backend/ollama_client.py`, `backend/openrouter.py`, `backend/providers/anthropic.py`, `backend/providers/custom_openai.py`, `backend/providers/deepseek.py`, `backend/providers/google.py`, `backend/providers/groq.py`, `backend/providers/mistral.py`, `backend/providers/openai.py`, `start.sh`, and any further conflicted file.
- Delete: `docs/archive/GEMINI-2025-11-27.md` (upstream deleted it).
- Modify: `backend/tests/conftest.py`, upstream's file plus the fork's `anyio_backend` fixture.

**Interfaces:**
- Consumes: `M_A` from Task 2.
- Produces: merge commit `M_B` with parents `M_A` and `upstream/main`. The graft is removed. `git merge-base HEAD upstream/main` equals `git rev-parse upstream/main`.

- [ ] **Step 1: Create the graft and confirm the merge base.**

  ```bash
  git replace --graft 31c21d8 8351aa1
  git merge-base HEAD upstream/main | cut -c1-7   # expect 8351aa1
  ```

- [ ] **Step 2: Start the merge.**

  ```bash
  git merge --no-ff --no-commit upstream/main
  git diff --name-only --diff-filter=U | tee "$SCRATCH/stepB-conflicts.txt"
  git status --porcelain | grep -E '^(DU|UD|AA|DD)' || true
  ```

  Expected (from the trial merge): the 12 files listed under **Files**. More files may conflict if `upstream/main` moved past `614dfb9`.

- [ ] **Step 3: Resolve `conftest.py` as a union.**

  ```bash
  git checkout --theirs -- backend/tests/conftest.py
  cat >> backend/tests/conftest.py <<'EOF'


  @pytest.fixture
  def anyio_backend():
      """Fork tests use @pytest.mark.anyio; run them on asyncio only."""
      return "asyncio"
  EOF
  git add backend/tests/conftest.py
  ```

- [ ] **Step 4: Accept upstream's deletion of the GEMINI archive.**

  ```bash
  git rm -q docs/archive/GEMINI-2025-11-27.md
  ```

- [ ] **Step 5: Take upstream's side of every remaining conflicted file.**

  ```bash
  git diff --name-only --diff-filter=U | xargs -r git checkout --theirs --
  git diff --name-only --diff-filter=U | xargs -r git add --
  git diff --name-only --diff-filter=U | wc -l   # expect 0
  grep -rln '^<<<<<<< \|^>>>>>>> ' -- backend frontend/src the_ai_counsel_mcp start.sh || echo no-markers
  ```

  Expected: `0` and `no-markers`. `start.sh` is now upstream's npm launcher, and the bun launcher is dropped (spec D9).

- [ ] **Step 6: Delete fork files that upstream supersedes.**

  ```bash
  git rm -q --ignore-unmatch GEMINI.md frontend/bun.lock .vscode/settings.json
  ```

- [ ] **Step 7: Check that upstream's own test suites pass in the merged tree.**

  ```bash
  uv sync
  UPSTREAM_TESTS=$(git ls-tree -r --name-only upstream/main backend/tests the_ai_counsel_mcp/tests | grep '/test_.*\.py$')
  uv run pytest $UPSTREAM_TESTS -q
  ```

  Expected: all pass. If an upstream test fails, the cause is a fork edit that git auto-merged into a file upstream owns. Run `git diff upstream/main -- <file>`, remove the fork hunk so that file matches upstream, re-run, and `git add` the file. The feature returns in its task later.

- [ ] **Step 8: Record the fork-test failures as the backlog for Tasks 4–12.**

  ```bash
  uv run pytest backend/tests -q -p no:randomly 2>&1 | tail -40 | tee "$SCRATCH/stepB-fork-failures.txt"
  ```

  Expected: failures only in the fork test files (`test_temperature_overrides`, `test_council_critical_paths`, `test_stage2_*`, `test_rankings_aggregation`, `test_runs_resume`, `test_main_api_routes`, `test_openrouter_critical_paths`). Each is closed by a later task.

- [ ] **Step 9: Commit the merge, remove the graft, and verify the history link.**

  ```bash
  git commit -m "merge: The AI Counsel v0.13.1 (upstream/main $(git rev-parse --short upstream/main))

  Merged with a temporary graft 31c21d8 -> 8351aa1 so the rebrand and
  v0.8-v0.13 arrive as ordinary diffs. Mechanical resolution: take upstream
  for conflicted files, union conftest.py, accept upstream deletions.
  Fork features are re-applied in the following commits."
  git replace -d 31c21d8
  git replace -l                                                          # expect empty
  test "$(git merge-base HEAD upstream/main)" = "$(git rev-parse upstream/main)" && echo linked
  git log -1 --format='%p'                                                # two parents
  ```

  Expected: `linked`.

- [ ] **Step 10: Push the checkpoint.**

  ```bash
  git push origin integrate/ai-counsel
  ```

---

### Task 4: Tooling: Python dev deps, vitest, lockfiles

**Files:**
- Modify: `pyproject.toml`: add `pytest-cov>=7.0.0` to `[dependency-groups].dev` and `pythonpath = ["."]` to `[tool.pytest.ini_options]`.
- Modify: `uv.lock`, regenerated with `uv lock`.
- Modify: `frontend/package.json`: add `"test": "vitest run"` and devDependency `"vitest": "^3.2.4"`.
- Create: `frontend/vitest.config.js`
- Modify: `frontend/package-lock.json`, regenerated with `npm install`.

**Interfaces:**
- Produces: `npm test --prefix frontend` runs vitest over `src/**/*.test.js` except `src/utils/fontSize.test.js`, which stays on `node --test`.

- [ ] **Step 1: Show that frontend tests can't run yet.**

  ```bash
  npm test --prefix frontend
  ```

  Expected: `npm error Missing script: "test"`.

- [ ] **Step 2: Add the dependencies and the vitest config.**

  In `pyproject.toml`:

  ```toml
  [dependency-groups]
  dev = [
      "pytest>=9.0.3",
      "pytest-asyncio>=1.3.0",
      "pytest-cov>=7.0.0",
      "respx>=0.23.1",
      "ruff>=0.15.16",
  ]

  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  pythonpath = ["."]
  markers = [
      "real_settings_file: test intentionally writes the real data/settings.json (opts out of the conftest guard)",
  ]
  ```

  In `frontend/package.json`, add `"test": "vitest run"` under `scripts` and `"vitest": "^3.2.4"` under `devDependencies`.

  Create `frontend/vitest.config.js`:

  ```js
  import { defineConfig } from 'vitest/config';

  export default defineConfig({
    test: {
      include: ['src/**/*.test.js'],
      // fontSize.test.js uses node:test and runs with `node --test`.
      exclude: ['src/utils/fontSize.test.js', 'node_modules/**'],
    },
  });
  ```

- [ ] **Step 3: Regenerate the lockfiles.**

  ```bash
  uv lock && uv sync
  npm install --prefix frontend
  ```

- [ ] **Step 4: Check that the runners work.**

  ```bash
  npm test --prefix frontend 2>&1 | tail -15
  node --test frontend/src/utils/fontSize.test.js
  uv run pytest --co -q backend/tests | tail -3
  ```

  Expected: vitest runs (the fork's `api.test.js`, `Stage2.test.js`, and `modelHelpers.test.js` may fail until Task 12). `fontSize.test.js` passes. pytest collects with no import errors in collection.

- [ ] **Step 5: Commit.**

  ```bash
  git add pyproject.toml uv.lock frontend/package.json frontend/package-lock.json frontend/vitest.config.js
  git commit -m "build: add vitest runner and pytest-cov for fork test suites"
  ```

---

### Task 4b: Adapt fork tests to upstream API drift (added during execution)

Found after the Step B merge (`$SCRATCH/stepB-fork-failures.txt`). These fork tests check behavior that upstream changed on purpose. Each edit follows the upstream API and keeps what the test checks.

**Files:**
- Modify: `backend/tests/test_council_critical_paths.py::test_parse_ranking_truncates_to_expected_count`: upstream's parser dedupes before truncating (`test_ranking_parse.py::test_parse_ranking_deduplicates_labels`). Expect `["Response A", "Response B"]`; the test still checks truncation to `expected_count`.
- Modify: `backend/tests/test_storage_integrity.py::test_add_user_and_error_messages_append_expected_shapes`: upstream stores `stage2: None` on error messages.
- Modify: `backend/tests/test_main_api_routes.py`:
  - `test_post_conversations_happy_path`: the `create_conversation` stub accepts upstream's `mode=` keyword (`**kwargs`).
  - `test_get_conversations_happy_path`: assert the fork's fields as a subset of each item (upstream adds `mode`, `run_summary`, cost fields).
  - `test_get_settings_returns_shape_with_key_flags`: key flags come from the credential store (`settings_payload._key_set` → `has_secret`). Patch that instead of the plaintext settings field.
  - `test_stream_endpoint_invalid_execution_mode_returns_400`: upstream validates `execution_mode` with a pydantic `Literal`, so the status is 422. Rename to `…_is_rejected` and assert 422 and that the error names `execution_mode`.
- Not here: `test_put_settings_invalid_council_model_count_returns_400` (Task 11), `test_openrouter_generation_*` (Task 7).

- [ ] **Step 1:** Make the edits above. Run `uv run pytest backend/tests/test_council_critical_paths.py::test_parse_ranking_truncates_to_expected_count backend/tests/test_storage_integrity.py backend/tests/test_main_api_routes.py -q`. Expected: only the Task 7 and Task 11 cases still fail.
- [ ] **Step 2:** Commit: `test(fork): adapt fork tests to upstream API changes`.

---

### Task 5: F4: forced temperature 1.0 in `providers/temperature.py`

**Files:**
- Modify: `backend/providers/temperature.py`: add the forced-temperature rules.
- Modify: `backend/council.py`: call `resolve_temperature` in `query_model`, and delete any leftover `FORCED_TEMP_ONE_*`, `_normalize_model_for_rules`, and `_should_force_temperature_one`.
- Modify: `backend/tests/test_temperature_overrides.py`: point it at the new module and stub `attach_cost`.

**Interfaces:**
- Produces: `backend.providers.temperature.should_force_temperature_one(model_id: str) -> bool` and `backend.providers.temperature.resolve_temperature(model_id: str, requested: float) -> float`.

- [ ] **Step 1: Rewrite the fork test against the new module.**

  Replace `backend/tests/test_temperature_overrides.py` with:

  ```python
  from unittest.mock import AsyncMock, patch

  import pytest

  from backend import council
  from backend.providers.temperature import resolve_temperature, should_force_temperature_one


  @pytest.mark.parametrize(
      ("model_id", "expected"),
      [
          ("openrouter:google/gemini-3-pro-preview", True),
          ("openrouter:google/gemini-3-flash-preview", True),
          ("google:gemini-3-flash-preview", True),
          ("openrouter:google/gemini-2.5-flash", True),
          ("requesty:google/gemini-2.5-flash", True),
          ("openrouter:x-ai/grok-4.1-fast", True),
          ("openrouter:z-ai/glm-5", True),
          ("openrouter:minimax/minimax-m2.5", True),
          ("openrouter:openai/gpt-oss-120b", True),
          ("openrouter:openai/gpt-oss-20b:free", True),
          ("openrouter:openai/gpt-4o-mini", False),
          ("google:gemini-2.5-flash-lite", False),
          ("", False),
      ],
  )
  def test_should_force_temperature_one_by_model_id(model_id, expected):
      assert should_force_temperature_one(model_id) is expected


  def test_resolve_temperature_keeps_requested_value_for_regular_models():
      assert resolve_temperature("openai:gpt-4.1", 0.27) == 0.27
      assert resolve_temperature("openrouter:z-ai/glm-5", 0.27) == 1.0


  @pytest.mark.parametrize(
      ("model_id", "expected_temperature"),
      [
          ("openrouter:google/gemini-3-pro-preview", 1.0),
          ("openrouter:z-ai/glm-5", 1.0),
          ("openrouter:openai/gpt-oss-120b", 1.0),
          ("openrouter:openai/gpt-4o-mini", 0.27),
      ],
  )
  @pytest.mark.anyio
  async def test_query_model_applies_forced_temperature(model_id, expected_temperature):
      provider = type("DummyProvider", (), {})()
      provider.query = AsyncMock(return_value={"content": "ok", "error": False})

      with patch("backend.council.get_provider_for_model", return_value=provider), \
           patch("backend.council.attach_cost", new=AsyncMock(side_effect=lambda m, r: r)):
          await council.query_model(model_id, [{"role": "user", "content": "hi"}], timeout=5.0, temperature=0.27)

      assert provider.query.await_count == 1
      _, _, _, passed_temperature = provider.query.await_args.args
      assert passed_temperature == expected_temperature
  ```

- [ ] **Step 2: Run it and confirm it fails.**

  ```bash
  uv run pytest backend/tests/test_temperature_overrides.py -q
  ```

  Expected: `ImportError: cannot import name 'resolve_temperature'`.

- [ ] **Step 3: Implement the rules in `backend/providers/temperature.py`.**

  Append after `add_temperature_if_supported`:

  ```python
  # Models that only behave correctly at temperature 1.0 (matched on the model
  # part after any internal prefix and upstream provider slug).
  FORCED_TEMPERATURE_ONE_MODELS = frozenset({
      "gemini-3-pro-preview",
      "gemini-3-flash-preview",
      "gemini-2.5-flash",
      "grok-4.1-fast",
      "glm-5",
      "minimax-m2.5",
  })
  # Families where variants (":free", "-exp", …) share the rule.
  FORCED_TEMPERATURE_ONE_PREFIXES = ("gpt-oss-120b", "gpt-oss-20b")


  def should_force_temperature_one(model_id: str) -> bool:
      """True when the model must always run at temperature 1.0."""

      _, model = split_upstream_model(model_id)
      if not model:
          return False
      return model in FORCED_TEMPERATURE_ONE_MODELS or model.startswith(FORCED_TEMPERATURE_ONE_PREFIXES)


  def resolve_temperature(model_id: str, requested: float) -> float:
      """Return the temperature to request for model_id."""

      return 1.0 if should_force_temperature_one(model_id) else requested
  ```

  Add `"requesty"` to `INTERNAL_PROVIDER_PREFIXES` (keep the set alphabetical) so `requesty:` IDs split correctly. Task 9 relies on this.

- [ ] **Step 4: Call it from `council.query_model`.**

  In `backend/council.py`, add to the imports:

  ```python
  from .providers.temperature import resolve_temperature
  ```

  At the top of `query_model`, before `provider = get_provider_for_model(model)`:

  ```python
      temperature = resolve_temperature(model, temperature)
  ```

  Delete `FORCED_TEMP_ONE_MODELS`, `FORCED_TEMP_ONE_PREFIXES`, `_normalize_model_for_rules`, and `_should_force_temperature_one` if any survived the merges (`grep -n "FORCED_TEMP_ONE\|_should_force_temperature_one\|_normalize_model_for_rules" backend/` must print nothing).

- [ ] **Step 5: Run the new tests and upstream's temperature tests.**

  ```bash
  uv run pytest backend/tests/test_temperature_overrides.py backend/tests/test_provider_temperature.py -q
  ```

  Expected: all pass.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/providers/temperature.py backend/council.py backend/tests/test_temperature_overrides.py
  git commit -m "feat(fork): force temperature 1.0 for listed models via providers/temperature"
  ```

---

### Task 6: F5: thinking-content normalization on upstream's helper

**Files:**
- Modify: `backend/council.py`: add `strip_thinking_tags` as an alias of `strip_thinking_blocks`, and re-add `_extract_thinking_segments`, `_reasoning_details_to_text`, and `normalize_thinking_content` from the fork.
- Test: `backend/tests/test_council_critical_paths.py` (fork file, unchanged unless an upstream signature forces an edit).

**Interfaces:**
- Produces: `council.strip_thinking_tags(text: Any) -> str` (revised during execution: a fork function that runs `strip_thinking_blocks` and also strips `<thinking>` blocks; see spec D5), `council._prompt_safe_field(result, field) -> str`, and `council.normalize_thinking_content(content: Any, reasoning: Any = None, reasoning_details: Any = None) -> Dict[str, str]` with keys `display_text` and `prompt_safe_text`. Copy the exact signature from the fork source in Step 3.

- [ ] **Step 1: Run the fork test and confirm it fails.**

  ```bash
  uv run pytest backend/tests/test_council_critical_paths.py -q
  ```

  Expected: `AttributeError: module 'backend.council' has no attribute 'strip_thinking_tags'` (or `normalize_thinking_content`).

- [ ] **Step 2: Add the alias under `strip_thinking_blocks`.**

  ```python
  # Fork name kept for existing callers/tests; upstream's helper is canonical.
  strip_thinking_tags = strip_thinking_blocks
  ```

- [ ] **Step 3: Re-add the fork helpers unchanged, placed after the alias.**

  ```bash
  git show pre-integration-2026-09-25:backend/council.py | sed -n '/^def _extract_thinking_segments/,/^async def query_model/p' | sed '$d' > "$SCRATCH/thinking_helpers.py"
  ```

  Paste `_extract_thinking_segments`, `_reasoning_details_to_text`, and `normalize_thinking_content` from that file, plus `_to_text` if `council.py` lacks it. Do not paste `strip_thinking_tags`, because the alias replaces it. Every internal call to `strip_thinking_tags` inside the pasted code keeps working through the alias.

- [ ] **Step 4: Run the tests.**

  ```bash
  uv run pytest backend/tests/test_council_critical_paths.py backend/tests/test_ranking_parse.py backend/tests/test_title_generation.py -q
  ```

  Expected: all pass. If a fork case calls `generate_conversation_title` or `stage…` with the fork's old signature, change the call to upstream's signature (add `conversation_id=None` where it is keyword-only). Keep the assertions as they are.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/council.py backend/tests/test_council_critical_paths.py
  git commit -m "feat(fork): restore thinking-content normalization on strip_thinking_blocks"
  ```

---

### Task 7: F6 detection, F7, F8: OpenRouter extras and the generation endpoint

**Files:**
- Modify: `backend/openrouter.py`: re-add the fork helpers and the `transforms` parameter.
- Modify: `backend/providers/openrouter.py`: `OpenRouterProvider.query(..., transforms=None)`.
- Modify: `backend/council.py`: `query_model(..., transforms=None)` forwards to `OpenRouterProvider` only.
- Modify: `backend/main.py`: add `GET /api/openrouter/generation`.
- Test: `backend/tests/test_openrouter_critical_paths.py`, `backend/tests/test_stage2_middle_out.py` (detection cases), and `backend/tests/test_main_api_routes.py` (`test_openrouter_generation_*`).

**Interfaces:**
- Produces:
  - `openrouter.is_context_overflow_error(error_code, error_message) -> bool`
  - `openrouter.is_context_overflow_response(response_data: Optional[dict]) -> bool`
  - `openrouter.fetch_generation(generation_id: str) -> Dict[str, Any]`, which returns `{"data": …}` or `{"error": True, "error_message": str}`
  - `openrouter.query_model(model, messages, timeout=120.0, temperature=0.7, transforms: Optional[List[str]] = None)`
  - `council.query_model(model, messages, timeout=None, temperature=0.7, *, conversation_id=None, transforms: Optional[List[str]] = None)`
  - route `GET /api/openrouter/generation?id=<generation_id>` → `{"success": bool, "data"?: …, "error"?: str}`

- [ ] **Step 1: Run the fork tests and confirm they fail.**

  ```bash
  uv run pytest backend/tests/test_openrouter_critical_paths.py backend/tests/test_stage2_middle_out.py -k "overflow or strip or canonical or reasoning" -q
  uv run pytest backend/tests/test_main_api_routes.py -k generation -q
  ```

  Expected: `AttributeError` for `is_context_overflow_error`/`_strip_openrouter_prefix`, and 404 for the generation route.

- [ ] **Step 2: Re-add the helpers to `backend/openrouter.py`.**

  ```bash
  git show pre-integration-2026-09-25:backend/openrouter.py > "$SCRATCH/openrouter_fork.py"
  ```

  From `$SCRATCH/openrouter_fork.py`, copy these into upstream's `backend/openrouter.py` unchanged, above `query_model`: `OPENROUTER_MODELS_URL`, `_strip_openrouter_prefix`, `_is_openrouter_gemini3_reasoning_target`, `_is_openrouter_deepseek_reasoning_target`, `_extract_error_info`, `is_context_overflow_error`, `is_context_overflow_response`, and `_resolve_to_canonical_slug`. Copy `fetch_generation` below `query_model`.

  Then merge the fork's `query_model` behavior into upstream's `query_model`. Keep upstream's body and its usage/cost fields, and add:
  1. the parameter `transforms: Optional[List[str]] = None`, and `if transforms: payload["transforms"] = transforms` where the payload is built;
  2. `model = _strip_openrouter_prefix(model)` on entry;
  3. the reasoning payload for the two reasoning-target helpers, copied from the fork's `query_model`;
  4. on a failed response, the keys `error_code` and `is_context_overflow`, copied from the fork's error return (`'is_context_overflow': is_context_overflow_error(last_error_code, last_error_message)`).

  Use `diff <(git show upstream/main:backend/openrouter.py) "$SCRATCH/openrouter_fork.py"` to find each fork hunk.

- [ ] **Step 3: Thread `transforms` through the provider and `council.query_model`.**

  In `backend/providers/openrouter.py`:

  ```python
      async def query(self, model_id: str, messages: List[Dict[str, str]], timeout: float = 120.0, temperature: float = 0.7, transforms: Optional[List[str]] = None) -> Dict[str, Any]:
          if model_id.startswith("openrouter:"):
              model_id = model_id.replace("openrouter:", "", 1)
          return await openrouter.query_model(model_id, messages, timeout, temperature, transforms=transforms)
  ```

  (Add `Optional` to the `typing` import.)

  In `backend/council.py`, add `transforms: Optional[List[str]] = None` to `query_model`'s keyword-only arguments and change the dispatch to:

  ```python
      if isinstance(provider, OpenCodeProvider):
          response = await provider.query(model, messages, timeout, temperature, session_id=conversation_id)
      elif transforms and isinstance(provider, OpenRouterProvider):
          response = await provider.query(model, messages, timeout, temperature, transforms=transforms)
      else:
          response = await provider.query(model, messages, timeout, temperature)
  ```

  Import `OpenRouterProvider` from `.providers.openrouter` if `council.py` doesn't already.

- [ ] **Step 4: Add the generation route to `backend/main.py`.**

  Place it next to the other `/api/openrouter`/models routes:

  ```python
  @app.get("/api/openrouter/generation")
  async def get_openrouter_generation(id: str):
      """Fetch OpenRouter generation usage/cost metadata by generation ID."""
      from . import openrouter as openrouter_client

      result = await openrouter_client.fetch_generation(id)
      if result.get("error"):
          return {"success": False, "error": result.get("error_message", "Unknown error")}
      return {"success": True, "data": result.get("data")}
  ```

- [ ] **Step 5: Run the tests.**

  ```bash
  uv run pytest backend/tests/test_openrouter_critical_paths.py backend/tests/test_main_api_routes.py -k "generation or overflow or strip or canonical or reasoning or query_model" -q
  uv run pytest backend/tests/test_costs.py backend/tests/test_request_timeouts.py -q
  ```

  Expected: all pass. The `test_stage2_middle_out.py` retry cases still fail until Task 8. If a fork test patches `openrouter.get_openrouter_api_key`, upstream now reads the key through `backend.credentials.get_api_key`. Change the patch target to `backend.openrouter.get_api_key` (or wherever upstream's `openrouter.py` imports it from) and keep the assertion as it is.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/openrouter.py backend/providers/openrouter.py backend/council.py backend/main.py backend/tests/test_openrouter_critical_paths.py backend/tests/test_main_api_routes.py
  git commit -m "feat(fork): restore OpenRouter overflow detection, reasoning targets, transforms and generation endpoint"
  ```

---

### Task 8: F3 + F6 retry: balanced cyclic Stage 2 ordering on upstream's contract

**Files:**
- Modify: `backend/council.py`: helpers `_canonical_candidate_id`, `_build_stage2_candidates`, `_deterministic_cyclic_orders`, `_dedupe_valid_order`; the `balanced_order` keyword and per-evaluator maps in `stage2_collect_rankings`; the middle-out retry; `calculate_aggregate_rankings`; `build_stage_texts` plus `_relabel_ranking_to_global` (Step 5c).
- Modify: `backend/debate.py:642`: pass `balanced_order=False`.
- Modify: `backend/tests/test_stage2_permutation.py`, `backend/tests/test_stage2_middle_out.py`: adapt to the flat first yield. (Revised during execution: `backend/tests/test_rankings_aggregation.py` has no `query_model` fake and needed no change; it passes as written once Step 5b lands.)
- Create: `backend/tests/test_stage2_contract.py`

**Interfaces:**
- Consumes: `council.query_model(..., transforms=…)` and `openrouter.is_context_overflow_response` from Task 7.
- Produces:
  - `stage2_collect_rankings(user_query, stage1_results, search_context="", request=None, prompt_override=None, *, conversation_id=None, balanced_order: bool = True)`
  - First yield: `Dict[str, str]` (`"Response A" → model`, canonical order).
  - Each result dict: upstream keys plus `stage2_label_map: Dict[str, str]`, `stage2_candidate_label_map: Dict[str, str]`, `parsed_ranking_local: List[str]`, `parsed_ranking_candidate_ids: List[str]`, `parsed_ranking_models: List[str]`, `stage2_transform_applied: bool`, `stage2_retry_reason: Optional[str]`, `stage2_middle_out_mode: str`. `parsed_ranking` holds **global** labels.
  - `calculate_aggregate_rankings(stage2_results, label_to_model, return_diagnostics=False)`: upstream's call shape; the fork's weighted, per-evaluator-aware body (Step 5b).
  - `build_stage_texts(stage1_results, stage2_results)`: unchanged signature; Stage 2 ranking text comes out in the global label space (Step 5c).

- [ ] **Step 1: Write the contract test.**

  Create `backend/tests/test_stage2_contract.py`:

  ```python
  from types import SimpleNamespace

  import pytest

  from backend import council


  def _settings():
      return SimpleNamespace(
          stage2_prompt="{responses_text}\n\nFINAL RANKING:",
          stage2_temperature=0.3,
      )


  def _stage1(n):
      return [
          {"model": f"requesty:model-{chr(97 + i)}", "response": f"answer {i}", "error": None}
          for i in range(n)
      ]


  async def _collect(stage1, **kwargs):
      items = []
      async for item in council.stage2_collect_rankings("Q?", stage1, **kwargs):
          items.append(item)
      return items[0], items[1:]


  @pytest.fixture
  def fake_llm(monkeypatch):
      replies = {}

      async def _fake_query_model(model, messages, timeout=None, temperature=0.7, *, conversation_id=None, transforms=None):
          return {"content": replies.get(model, replies.get("*")), "error": False}

      monkeypatch.setattr(council, "get_settings", _settings)
      monkeypatch.setattr(council, "query_model", _fake_query_model)
      return replies


  @pytest.mark.anyio
  async def test_first_yield_is_flat_label_map(fake_llm):
      fake_llm["*"] = "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"
      first, results = await _collect(_stage1(3))
      assert isinstance(first, dict) and "model" not in first
      assert first == {
          "Response A": "requesty:model-a",
          "Response B": "requesty:model-b",
          "Response C": "requesty:model-c",
      }
      assert all(isinstance(v, str) for v in first.values())
      assert len(results) == 3


  @pytest.mark.anyio
  async def test_parsed_ranking_is_global_and_matches_models(fake_llm):
      fake_llm["*"] = "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"
      first, results = await _collect(_stage1(3))
      for r in results:
          assert [first[label] for label in r["parsed_ranking"]] == r["parsed_ranking_models"]
          assert [r["stage2_label_map"][label] for label in r["parsed_ranking_local"]] == r["parsed_ranking_models"]


  @pytest.mark.anyio
  async def test_unknown_and_duplicate_labels_are_dropped(fake_llm):
      fake_llm["*"] = "FINAL RANKING:\n1. Response Z\n2. Response B\n3. Response B\n4. Response A"
      first, results = await _collect(_stage1(3))
      for r in results:
          assert r["parsed_ranking_local"] == ["Response B", "Response A"]
          assert len(r["parsed_ranking"]) == 2
          assert set(r["parsed_ranking"]) <= set(first)


  @pytest.mark.anyio
  async def test_single_successful_response(fake_llm):
      fake_llm["*"] = "FINAL RANKING:\n1. Response A"
      stage1 = _stage1(2)
      stage1[1]["error"] = True
      first, results = await _collect(stage1)
      assert first == {"Response A": "requesty:model-a"}
      assert [r["model"] for r in results] == ["requesty:model-a"]
      assert results[0]["parsed_ranking"] == ["Response A"]


  @pytest.mark.anyio
  async def test_balanced_order_false_uses_one_label_space(fake_llm):
      fake_llm["*"] = "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"
      first, results = await _collect(_stage1(3), balanced_order=False)
      for r in results:
          assert r["stage2_label_map"] == first
          assert r["parsed_ranking"] == r["parsed_ranking_local"]
  ```

  Revised during execution: upstream's Stage 2 reads `settings.response_language`, so every fake settings object in this task (the contract test's `_settings()` and the fakes in `test_stage2_permutation.py` and `test_stage2_middle_out.py`) also sets `response_language=None`. That follows an upstream API change and checks the same behavior. The contract test also gets the tests from Steps 5c and 6, plus `test_debate_requests_one_label_space_every_round` (Review Focus 5: `run_iterative_debate` passes `balanced_order=False` to Stage 2 in every round).

- [ ] **Step 2: Adapt the fork tests to the flat first yield.**

  In `backend/tests/test_stage2_permutation.py::test_stage2_uses_per_evaluator_label_maps_and_normalizes_to_canonical`:
  - change the fake's signature to `async def _fake_query_model(model, messages, timeout=None, temperature=0.7, *, conversation_id=None, transforms=None):`;
  - replace `stage2_candidate_maps = init_payload["stage2_candidate_maps_by_evaluator"]` with `stage2_candidate_maps = {r["model"]: r["stage2_candidate_label_map"] for r in results}`;
  - replace `init_payload["label_to_model"]` with `init_payload`.

  Apply the same fake-signature change in `test_stage2_middle_out.py` (`test_rankings_aggregation.py` has no fake; see **Files**), and patch `council.query_model` (not `council.openrouter.query_model`) for the retry cases. The retry must now go through `query_model(..., transforms=["middle-out"])`. Assert on the `transforms` keyword that the fake records.

- [ ] **Step 3: Run the Stage 2 tests and confirm they fail.**

  ```bash
  uv run pytest backend/tests/test_stage2_contract.py backend/tests/test_stage2_permutation.py backend/tests/test_stage2_middle_out.py backend/tests/test_rankings_aggregation.py -q
  ```

  Expected: failures (`_deterministic_cyclic_orders` missing, `stage2_label_map` KeyError, unexpected keyword `balanced_order`).

- [ ] **Step 4: Re-add the pure helpers.**

  Copy `_canonical_candidate_id`, `_build_stage2_candidates`, `_deterministic_cyclic_orders`, and `_dedupe_valid_order` unchanged from `git show pre-integration-2026-09-25:backend/council.py` (fork lines 436–489; if the local tag is missing, `0ffffa4` and `origin/backup/pre-integration-2026-09-25` have the identical file) into `backend/council.py`, just above `stage2_collect_rankings`. Add `import hashlib` if missing. In `_build_stage2_candidates`, `strip_thinking_tags` resolves through the Task 6 alias.

- [ ] **Step 5: Rework `stage2_collect_rankings` on upstream's body.**

  Add `balanced_order: bool = True` after `conversation_id` in the keyword-only arguments. Then change upstream's body as follows (keep everything else, including prompt override, language, and progress):

  ```python
      successful_results = [r for r in stage1_results if not r.get('error')]
      successful_models = [r['model'] for r in successful_results]
      candidates = _build_stage2_candidates(successful_results)
      label_keys = [f"Response {chr(65 + i)}" for i in range(len(candidates))]

      # Global (canonical) label space: upstream contract, yielded first.
      label_to_model = {label: c["model"] for label, c in zip(label_keys, candidates)}
      model_to_global_label = {model: label for label, model in label_to_model.items()}
      yield label_to_model

      if balanced_order:
          seed_key = f"{conversation_id or ''}|{user_query}"
          orders = _deterministic_cyclic_orders(candidates, successful_models, seed_key)
      else:
          orders = {m: candidates for m in successful_models}

      label_maps: Dict[str, Dict[str, str]] = {}
      candidate_maps: Dict[str, Dict[str, str]] = {}
      messages_by_evaluator: Dict[str, List[Dict[str, str]]] = {}
      for evaluator in successful_models:
          ordered = orders.get(evaluator, candidates)
          local_labels = label_keys[: len(ordered)]
          label_maps[evaluator] = {l: c["model"] for l, c in zip(local_labels, ordered)}
          candidate_maps[evaluator] = {l: c["candidate_id"] for l, c in zip(local_labels, ordered)}
          responses_text = "\n\n".join(f"{l}:\n{c['text']}" for l, c in zip(local_labels, ordered))
          # Build this evaluator's prompt exactly as upstream builds the shared one,
          # substituting responses_text; keep upstream's fallback on format errors.
          messages_by_evaluator[evaluator] = [{"role": "user", "content": build_prompt(responses_text)}]
  ```

  Here `build_prompt` stands for upstream's existing prompt-formatting block. Move that block into a local `def build_prompt(responses_text: str) -> str:` inside the function, unchanged, so each evaluator gets its own `responses_text`.

  Replace upstream's per-model query with the retry-aware version:

  ```python
      async def _query_one(m: str):
          meta = {"stage2_transform_applied": False, "stage2_retry_reason": None,
                  "stage2_middle_out_mode": "retry_on_overflow"}
          msgs = messages_by_evaluator[m]
          response = await query_model(m, msgs, temperature=stage2_temp, conversation_id=conversation_id)
          if isinstance(get_provider_for_model(m), OpenRouterProvider) and openrouter.is_context_overflow_response(response):
              meta.update(stage2_transform_applied=True, stage2_retry_reason="context_overflow")
              response = await query_model(m, msgs, temperature=stage2_temp,
                                           conversation_id=conversation_id, transforms=["middle-out"])
          return response, meta
  ```

  Keep upstream's scheduling and its disconnect checks around `_query_one`. (Revised during execution: upstream v0.13.1's Stage 2 has no Ollama-sequential split; it runs one task per evaluator in parallel, and that is kept as is. The retry metadata is created in the exception-safe wrapper and passed into `_query_one`, so an exception during the middle-out retry still reports `stage2_transform_applied=True`.) When building each result, after upstream parses `parsed = parse_ranking_from_text(text, expected_count=…)`:

  ```python
          local_map = label_maps.get(model, {})
          parsed_local = _dedupe_valid_order(parsed, set(local_map))
          parsed_models = [local_map[l] for l in parsed_local]
          item.update(
              parsed_ranking=[model_to_global_label[m] for m in parsed_models],
              parsed_ranking_local=parsed_local,
              parsed_ranking_models=parsed_models,
              parsed_ranking_candidate_ids=[candidate_maps[model][l] for l in parsed_local],
              stage2_label_map=local_map,
              stage2_candidate_label_map=candidate_maps.get(model, {}),
              **meta,
          )
  ```

  Error results get the same keys with empty lists and the evaluator's maps. `openrouter` is `from . import openrouter`.

- [ ] **Step 5b: Restore the fork's weighted `calculate_aggregate_rankings` (spec D3, revised during execution).**

  Upstream's version re-parses raw ranking text against the global map, which is wrong under balanced ordering. Replace it with the fork's version from `git show pre-integration-2026-09-25:backend/council.py` (plus `STAGE2_HARD_CAP_MIN_COMPLETION = 0.25`), with these changes: read the local map from `stage2_label_map` (the fork read `stage2_label_model_map`), and in the legacy text fallback call `parse_ranking_from_text(text, expected_count=…, valid_labels=list(label_to_model))` as upstream does. Keep the signature `(stage2_results, label_to_model, return_diagnostics=False)`. `backend/tests/test_rankings_aggregation.py` (fork) and every upstream test that calls it must pass.

- [ ] **Step 5c: One label space for Stage 3.**

  Read `stage3_synthesize_final`. If it puts evaluator ranking text into the chairman prompt, convert each evaluator's local labels to global labels first (through `stage2_label_map` → model → global label), and do it in one pass so `A→B` and `B→A` swaps don't chain. Add a test in `test_stage2_contract.py` showing the chairman prompt only uses global labels. If Stage 3 already works from `parsed_ranking` or model names, record that in the commit message and add no code.

  Resolved during execution: upstream's Stage 3 builds `stage2_text` from each evaluator's raw ranking text in `build_stage_texts` (also used by `debate.py`), so conversion was needed. `build_stage_texts` now runs each ranking through `_relabel_ranking_to_global`, one regex pass over `\bResponse [A-Z]\b`. The global labels come from the order of the successful Stage 1 results, which is exactly how `stage2_collect_rankings` assigns them. Results without `stage2_label_map` (legacy, or upstream fakes) are left unchanged. For debate (`balanced_order=False`) the rewrite is the identity.

- [ ] **Step 6: Keep debate on one label space.**

  In `backend/debate.py`, in the `stage2_collect_rankings(` call near line 642, add `balanced_order=False,` after `conversation_id=conversation_id,`.

  Also make `prompt_override` imply canonical order inside `stage2_collect_rankings` (`if prompt_override: orders = {m: candidates for m in successful_models}`), so any caller that pre-builds the prompt gets one label space even without `balanced_order=False`. Add to `test_stage2_contract.py`: with `prompt_override="PREBUILT"` and the default `balanced_order`, every evaluator receives exactly `"PREBUILT"` and every `stage2_label_map` equals the first yield.

- [ ] **Step 7: Run the Stage 2, debate, and MCP suites.**

  ```bash
  uv run pytest backend/tests/test_stage2_contract.py backend/tests/test_stage2_permutation.py backend/tests/test_stage2_middle_out.py backend/tests/test_rankings_aggregation.py backend/tests/test_ranking_parse.py backend/tests/test_debate.py backend/tests/test_debate_integration.py the_ai_counsel_mcp/tests -q
  ```

  Expected: all pass.

- [ ] **Step 8: Commit.**

  ```bash
  git add backend/council.py backend/debate.py backend/tests/test_stage2_contract.py backend/tests/test_stage2_permutation.py backend/tests/test_stage2_middle_out.py
  git commit -m "feat(fork): balanced cyclic Stage 2 ordering on upstream's label_to_model contract

  Per-evaluator label maps travel on each result; parsed_ranking stays in the
  global label space so aggregate rankings, heatmap and MCP are unchanged.
  Debate keeps one label space (balanced_order=False). OpenRouter context
  overflow retries once with middle-out through query_model."
  ```

---

**Task 8 review follow-up (2026-09-25).** Spec-compliance and code-quality reviews found nothing blocking. Fixed in a follow-up commit: per-evaluator state keyed by candidate id (a model listed twice no longer collapses labels); Stage 3 gets a per-evaluator label legend in place of the `Response X` rewrite (spec D3); MCP ranking entries carry `stage2_label_map`; new tests for these and for the `parsed_ranking_local` + `stage2_label_map` aggregation path. Carried forward: Task 9 adds a test that an overflow reply from a `requesty:` model is **not** retried with middle-out (`stage2_transform_applied is False`, one call); Task 12's `deanonymizeStage2Text` must use `stage2_label_map`, and no frontend build should be cut from this branch between Tasks 8 and 12 (the Stage 2 tab shows local-label text against the global map until then). Not changed: the failed first attempt's cost on a middle-out retry is not recorded (overflow rejections are normally free).

---

### Task 9: F1: Requesty provider on upstream's wiring

**Files:**
- Unchanged (already on HEAD in the needed shape, see execution notes): `backend/requesty.py` (reads the key through `config.get_requesty_api_key`, returns `usage`), `backend/providers/requesty.py`, `backend/settings.py` (`"requesty": False` toggle and `requesty_api_key` field).
- Modify: `backend/config.py`: `get_requesty_api_key()` returns `get_api_key("requesty")` only (`REQUESTY_API_URL` already there).
- Modify: `backend/credentials/ids.py`: `KNOWN_SECRET_IDS`, `SETTINGS_FIELD_TO_SECRET_ID`, `ENV_OVERRIDES`.
- Modify: `backend/credentials/relay_import.py`: add the `"requesty"` entry, and treat it as an aggregator toggle (like `openrouter`) in `_enable_providers_for_imported`.
- Modify: `backend/settings_payload.py`: `requesty_api_key_set`.
- Modify: `backend/main.py`: `UpdateSettingsRequest.requesty_api_key`, the update handler, `"requesty"` in the `/api/models/direct` skip list, `/api/models/requesty`, `/api/settings/test-requesty`.
- Modify: `backend/tests/test_stage2_contract.py`: Requesty overflow is not retried with middle-out (Task 8 carry-forward).
- Modify: `backend/council.py`: `"requesty": RequestyProvider()` in `PROVIDERS`.
- Modify: `backend/costs.py`: `"requesty"` in `_SUPPORTED_PROVIDER_PREFIXES`, and `_catalog_platform` returns `None` for it.
- Modify: `the_ai_counsel_mcp/server.py`: add `requesty` to the provider list in the instructions string.
- Modify: `backend/tests/conftest.py`: `self.requesty_api_key = None` in the fake settings class (next to `nvidia_api_key`).
- Create: `backend/tests/test_requesty_provider.py`

**Interfaces:**
- Produces: secret id `api:requesty`; env override `REQUESTY_API_KEY`; settings field `requesty_api_key` (write-only through `PUT /api/settings`); `GET /api/settings` → `requesty_api_key_set: bool`; model-ID prefix `requesty:`; `GET /api/models/requesty` → `{"models": [...]}`; `POST /api/settings/test-requesty` with body `{"api_key": str}` → `{"success": bool, "message": str}`.

- [ ] **Step 1: Write the failing tests.**

  Create `backend/tests/test_requesty_provider.py`:

  ```python
  import json

  import pytest

  from backend import council, costs
  from backend.credentials import file_backend, ids, store, upgrade
  from backend.providers.requesty import RequestyProvider
  from backend.providers.temperature import split_upstream_model


  @pytest.fixture()
  def cred_file(tmp_path, monkeypatch, fake_keyring):
      path = tmp_path / "credentials.json"
      monkeypatch.setattr(file_backend, "CREDENTIALS_FILE", path)
      monkeypatch.setattr(store, "get_effective_mode", lambda: "file")
      monkeypatch.setattr(store, "_preferred_mode", lambda: "file")
      monkeypatch.setattr(store, "ENV_OVERRIDES", {})
      yield path


  def test_requesty_secret_is_registered():
      assert "api:requesty" in ids.KNOWN_SECRET_IDS
      assert ids.SETTINGS_FIELD_TO_SECRET_ID["requesty_api_key"] == "api:requesty"
      assert ids.ENV_OVERRIDES["api:requesty"] == "REQUESTY_API_KEY"


  def test_requesty_routes_to_provider():
      assert isinstance(council.get_provider_for_model("requesty:openai/gpt-4o-mini"), RequestyProvider)


  def test_requesty_prefix_is_internal():
      assert split_upstream_model("requesty:openai/gpt-5") == ("openai", "gpt-5")


  def test_requesty_cost_is_estimate_only():
      assert "requesty" in costs._SUPPORTED_PROVIDER_PREFIXES
      assert costs._catalog_platform("requesty", "openai/gpt-4o-mini") is None


  def test_store_key_is_read_by_config(cred_file):
      from backend.config import get_requesty_api_key
      store.set_secret("api:requesty", "rq-test")
      assert get_requesty_api_key() == "rq-test"


  def test_env_key_is_read_by_config(monkeypatch, cred_file):
      from backend.config import get_requesty_api_key
      monkeypatch.setattr(store, "ENV_OVERRIDES", {"api:requesty": "REQUESTY_API_KEY"})
      monkeypatch.setenv("REQUESTY_API_KEY", "rq-env")
      assert get_requesty_api_key() == "rq-env"
  ```

  Add the plaintext-migration test to the same file (Review Focus 3). `ensure_credentials_upgraded` imports `get_settings`/`update_settings` from `backend.settings` inside the function, so patch them there:

  ```python
  def test_plaintext_requesty_key_migrates_to_store(cred_file, monkeypatch):
      from backend import settings as settings_mod
      from backend.settings import Settings

      current = Settings(requesty_api_key="rq-plain", credentials_migrated=False)
      applied = {}
      monkeypatch.setattr(settings_mod, "get_settings", lambda: current)
      monkeypatch.setattr(settings_mod, "update_settings", lambda **kw: applied.update(kw))

      assert upgrade.ensure_credentials_upgraded() is True
      assert store.get_secret("api:requesty") == "rq-plain"
      assert applied["requesty_api_key"] is None
      assert applied["credentials_migrated"] is True
  ```

  Add the settings-API cases to `backend/tests/test_main_api_routes.py`. That file defines a module-level `client = TestClient(main.app)`; use it directly, not as a fixture:

  ```python
  def test_put_settings_accepts_requesty_key(monkeypatch):
      saved = {}
      monkeypatch.setattr("backend.credentials.store.set_secret", lambda sid, v: saved.__setitem__(sid, v))
      resp = client.put("/api/settings", json={"requesty_api_key": "rq-x"})
      assert resp.status_code == 200
      assert saved.get("api:requesty") == "rq-x"

  def test_get_settings_reports_requesty_key_set():
      body = client.get("/api/settings").json()
      assert "requesty_api_key_set" in body
      assert "requesty_api_key" not in body
  ```

  Before writing these, check how `PUT /api/settings` routes `openrouter_api_key` into the store (`grep -n "SETTINGS_FIELD_TO_SECRET_ID\|set_secret" backend/main.py backend/settings.py`). Patch the same function that call goes through.

- [ ] **Step 2: Run the tests and confirm they fail.**

  ```bash
  uv run pytest backend/tests/test_requesty_provider.py backend/tests/test_main_api_routes.py -k requesty -q
  ```

  Expected: `AssertionError`/`KeyError` on the ID registry, and routing that falls through to the default provider.

- [ ] **Step 3: Register the secret.**

  In `backend/credentials/ids.py`, add `"api:requesty",` after `"api:openrouter",` in `KNOWN_SECRET_IDS`, `"requesty_api_key": "api:requesty",` in `SETTINGS_FIELD_TO_SECRET_ID`, and `"api:requesty": "REQUESTY_API_KEY",` in `ENV_OVERRIDES`. In `backend/credentials/relay_import.py`, add `"requesty": ("api:requesty", "Requesty API key"),` next to the `"nvidia"` entry.

- [ ] **Step 4: Wire config, settings, payload, and costs.**

  `backend/config.py`:

  ```python
  # Requesty API endpoint
  REQUESTY_API_URL = "https://router.requesty.ai/v1/chat/completions"


  def get_requesty_api_key() -> str:
      """Get Requesty API key from credential store or environment."""
      from .credentials import get_api_key

      return get_api_key("requesty") or os.getenv("REQUESTY_API_KEY", "")
  ```

  `backend/settings.py`: add `"requesty": False,` to `DEFAULT_ENABLED_PROVIDERS` after `"openrouter"`, and `requesty_api_key: Optional[str] = None` next to `openrouter_api_key`.
  `backend/settings_payload.py`: add `"requesty_api_key_set": _key_set("api:requesty"),` after `openrouter_api_key_set`.
  `backend/costs.py`: add `"requesty",` to `_SUPPORTED_PROVIDER_PREFIXES`, and in `_catalog_platform` add `if provider == "requesty": return None` before the final return.
  `backend/tests/conftest.py`: add `self.requesty_api_key = None` next to `self.nvidia_api_key = None`.

- [ ] **Step 5: Wire the provider registry, the request model, and the routes.**

  `backend/council.py`: `from .providers.requesty import RequestyProvider` and `"requesty": RequestyProvider(),` in `PROVIDERS`.
  `backend/requesty.py`: replace its key lookup with `from .config import get_requesty_api_key`. Where it builds the success dict, add `"usage": data.get("usage")` exactly as upstream's `backend/openrouter.py` does.
  `backend/main.py`: add `requesty_api_key: Optional[str] = None` under `openrouter_api_key` in `UpdateSettingsRequest` (around line 1721), and under the `openrouter_api_key` handler (around line 1958):

  ```python
      if request.requesty_api_key is not None:
          updates["requesty_api_key"] = request.requesty_api_key
  ```

  Copy the fork's `/api/models/requesty` and `/api/settings/test-requesty` handlers from `git show pre-integration-2026-09-25:backend/main.py` (`grep -n "requesty" …`). In the test handler, fall back to `get_requesty_api_key()` when the body's key is empty, the same way the OpenRouter test handler does at `main.py:2568`.
  `the_ai_counsel_mcp/server.py:32`: add `requesty, ` to the provider list after `openrouter`.

- [ ] **Step 6: Run the tests.**

  ```bash
  uv run pytest backend/tests/test_requesty_provider.py backend/tests/test_main_api_routes.py backend/tests/test_credentials_store.py backend/tests/test_disconnect_all_providers.py backend/tests/test_settings_secret_redaction.py backend/tests/test_relay_ai_import.py backend/tests/test_costs.py the_ai_counsel_mcp/tests -q
  ```

  Expected: all pass. `test_disconnect_all_providers` covers wiping `api:requesty` because it iterates `KNOWN_SECRET_IDS`.

- [ ] **Step 7: Commit.**

  ```bash
  git add backend/requesty.py backend/providers/requesty.py backend/config.py backend/credentials/ids.py backend/credentials/relay_import.py backend/settings.py backend/settings_payload.py backend/main.py backend/council.py backend/costs.py backend/tests/conftest.py backend/tests/test_requesty_provider.py backend/tests/test_main_api_routes.py the_ai_counsel_mcp/server.py
  git commit -m "feat(fork): wire Requesty provider into credential store, settings, costs and routing"
  ```

**Task 9 execution notes (2026-09-25).** Done in `d8eadea`. Changes from the steps above:

- `backend/requesty.py`, `backend/providers/requesty.py`, and the `settings.py` toggle and field came through the merges unchanged from the fork and already had the needed shape: `requesty.py` gets its key from `config.get_requesty_api_key()` and already returns `usage`. Only `get_requesty_api_key()` changed.
- `get_requesty_api_key()` returns `get_api_key("requesty")` with no `or os.getenv("REQUESTY_API_KEY")`. The env var is still read, through `ENV_OVERRIDES` in the store. With the extra `os.getenv`, "Disconnect All Providers" could not stop the env var from bringing the key back, because `disabled_secret_ids` only applies inside the store. This follows NVIDIA's wiring (D6). A test covers it.
- `/api/models/direct` walks `PROVIDERS`. Without `"requesty"` in its skip list, every Requesty model would also show up as a direct model. The fork skipped it too.
- `relay_import._enable_providers_for_imported` sends any id in `RELAY_API_MAP` it doesn't special-case to the direct-provider branch, which turns on `direct` and adds a direct toggle. `requesty` now takes the `openrouter` branch (`if rid in ("openrouter", "requesty")`), so it turns on `enabled_providers["requesty"]`.
- `/api/models/requesty` returns `PROVIDERS["requesty"].get_models()` and doesn't repeat the prefix and sort code. `test-requesty` checks through `PROVIDERS["requesty"].validate_key`, as in the fork.
- Extra tests in `test_requesty_provider.py`: a fork-style `settings.json` with a plaintext key migrates on the first `GET /api/settings` (the key goes to the store and `settings.json` is rewritten without it); Disconnect All wipes `api:requesty`, turns off the toggle, and the env var stays blocked; relay import; the two routes; the direct-models skip.
- Not changed: `costs` confidence is `"medium"` only for `custom`. Requesty estimates get `cost_status: "estimated"` and `is_estimate: True`, but `pricing_confidence: "high"` when the catalog matches.
- Upstream's migration is lazy. `ensure_credentials_upgraded()` runs from `build_settings_response` (`GET`/`PUT /api/settings`) and the admin export, not at process start. A council run before the first settings load finds no Requesty key (or any other migrated key). Also, `update_settings` always redacts every `SETTINGS_FIELD_TO_SECRET_ID` field. So any settings write that happens before that first load drops the plaintext keys without migrating them. Examples are a `PUT /api/settings` that arrives before any `GET` (the handler calls `update_settings` before `build_settings_response`), an OAuth connect, a relay import, or a store `delete_secret` (`set_secret` only writes settings when the id is in `disabled_secret_ids`). The UI always loads settings first, so in practice this is only a risk for scripted or MCP-only use. Task 15's cutover checklist should open the UI (or `GET /api/settings`) once before the first run or any other settings change, and should back up `data/settings.json` before that.

---

**Task 9 review follow-up (2026-09-25).** Both reviews: compliant, nothing blocking. Fixed in a follow-up commit: Requesty requests now follow upstream's temperature rules on the bare model name (`<host>/<model>[@region][:tier]` → `<model>`; `requesty` added to the aggregator set in `providers/temperature.py`), so gpt-5/o-series/Claude 4+ no longer get a temperature they reject and forced-1.0 models match with host/region/tier suffixes; exceptions with an empty message and string error bodies produce a non-empty error; a 403 from key validation reads "Invalid API key"; Requesty costs are medium-confidence estimates with a note, and a zero-price match is never reported as free (spec D6). Review Focus 3 and Task 15 Step 4 now describe the lazy migration. Not changed (upstream behavior or out of scope): no startup-time `ensure_credentials_upgraded()`; `get_openrouter_api_key`'s `os.getenv` fallback; MCP provider tools and `get_all_models` don't list Requesty (same gap as NVIDIA); Requesty 5xx is not retried.

---

### Task 10: F2: resumable runs on upstream's pipeline

**Files:**
- Modify: `backend/runs.py`: move to upstream's stage signatures, the flat Stage 2 contract, and the `_active_runs` progress map.
- Modify: `backend/main.py`: in the marked fork block, `RUN_MANAGER = RunManager(progress=_active_runs, fetch_search_context=_fetch_search_context)`, the run-guard middleware and the five run routes; outside it, `run_id`/`event_count` in `/progress` and `_build_chat_history` delegating to `runs.build_chat_history`.
- Test: `backend/tests/test_runs_resume.py` (fork) and new cases in `backend/tests/test_main_api_routes.py`.

**Interfaces:**
- Consumes: the Stage 2 contract from Task 8.
- Produces:
  - `POST /api/conversations/{id}/runs` (body = upstream `SendMessageRequest`, including `search_provider`, `council_models`, `chairman_model`, `documents`) → run snapshot; 422 bad mode (pydantic `Literal`, as upstream's `/message/stream`), 400 invalid documents, 404 missing conversation, 409 active run (a `/runs` run, or an upstream stream or advisor debate registered in `_active_runs`).
  - `GET /api/conversations/{id}/runs/active` → `{"active_run": snapshot}`, or `{"active_run": null}` when none (the fork's contract, kept); 404 `"Conversation not found"`.
  - `GET /api/runs/{run_id}` → snapshot, or 404.
  - `GET /api/runs/{run_id}/stream?from_event=N` → SSE (`text/event-stream`), or 404.
  - `POST /api/runs/{run_id}/cancel` → `{"run_id": …, "status": …}` (the fork's shape, not a full snapshot), or 404. A second cancel while the run is stopping is a no-op.
  - Upstream's `POST …/message`, `…/message/stream`, `…/message/debate` and `…/debate/stream` → `409 {"detail": "Conversation already has an active run"}` while a `/runs` run is live on that conversation (fork middleware; upstream's handlers are unchanged).
  - Snapshot keys are the fork's `RunManager.snapshot()` keys, unchanged.
  - Aggregate ranking rows carry `generation_time_ms`, `generation_time_seconds`, `generation_total_tokens` and `generation_total_cost` (the fork leaderboard's columns).

- [ ] **Step 1: Add the restart and route tests.**

  *Superseded by the execution notes below:* the first test's 404 and the bad-mode 400 in this code block are not what shipped (the route answers `200 {"active_run": null}` and 422).

  Append to `backend/tests/test_main_api_routes.py`, using that file's module-level `client` and its `monkeypatch` conventions for `main.storage`:

  ```python
  def test_active_run_after_restart_is_404_not_500(monkeypatch):
      from backend import main
      monkeypatch.setattr(main.storage, "get_conversation", lambda cid: {"id": cid, "messages": [{"role": "user", "content": "hi"}]})
      main.RUN_MANAGER = main.RunManager()  # a fresh manager == backend restarted
      resp = client.get("/api/conversations/c1/runs/active")
      assert resp.status_code == 404

  def test_unknown_run_routes_are_404():
      for method, path in [("get", "/api/runs/nope"), ("get", "/api/runs/nope/stream"), ("post", "/api/runs/nope/cancel")]:
          assert getattr(client, method)(path).status_code == 404

  def test_start_run_rejects_bad_mode(monkeypatch):
      from backend import main
      monkeypatch.setattr(main.storage, "get_conversation", lambda cid: {"id": cid, "messages": []})
      resp = client.post("/api/conversations/c1/runs", json={"content": "q", "execution_mode": "bogus"})
      assert resp.status_code == 400
  ```

  If the fork's `get_active_conversation_run` returns 404 for a missing conversation and 200/`null` for no active run, keep the fork's behavior and change the first assertion to match the fork's exact response. The requirement is "not 500 and not a hang".

- [ ] **Step 2: Run the run tests and confirm they fail.**

  ```bash
  uv run pytest backend/tests/test_runs_resume.py backend/tests/test_main_api_routes.py -k "run" -q
  ```

  Expected: 404 on `POST …/runs` (route missing), and `test_runs_resume` failures on stage signatures or Stage 2 payload shape.

- [ ] **Step 3: Adapt `backend/runs.py` to upstream.**

  In `_execute_run`:
  - Call upstream's stage functions with upstream's arguments. Read them first: `grep -n "^async def stage1_collect_responses\|^async def stage2_collect_rankings\|^async def stage3_synthesize_final\|^async def generate_conversation_title" -A10 backend/council.py`. Pass `conversation_id=run.conversation_id` wherever upstream accepts it.
  - Stage 2: treat the first item (a `dict` without `"model"`) as `run.label_to_model`. Build `run.stage2_label_maps_by_evaluator[r["model"]] = r["stage2_label_map"]` and `run.stage2_candidate_maps_by_evaluator[r["model"]] = r["stage2_candidate_label_map"]` from each result. Drop the `stage2_init_data` branch.
  - Aggregate: `run.aggregate_rankings, run.ranking_diagnostics = calculate_aggregate_rankings(run.stage2_results, run.label_to_model, return_diagnostics=True)` (Task 8 Step 5b keeps `return_diagnostics`).
  - Progress: when a run starts, write `_active_runs[conversation_id]` through the helper upstream uses at `main.py:80`/`main.py:93`. Update `"stage"` and `progress` at the same points upstream's `/message/stream` does, and pop it in `finally`. So that `runs.py` doesn't import `main.py`, put the three small helpers (`_start_progress`, `_set_stage`, `_finish_progress`) in `backend/runs.py` and have `main.py` pass its `_active_runs` dict into `RunManager(progress=_active_runs)`.
  - Storage: save assistant messages through the upstream `storage` function that `/message/stream` uses (`grep -n "storage\.add_assistant_message" backend/main.py`), with the same keyword arguments, so `run_summary`/cost fields are derived the same way.
  - Request options: `start_run` also takes `search_provider`, `council_models`, `chairman_model` (all optional, from upstream's `SendMessageRequest`) and `history` (default: built from the conversation before the new user message). Pass `models_override`/`history` to Stage 1 and `chairman_override` to Stage 3. `web_search` is true when either `web_search` or `search_provider` is set; resolve the provider and its env keys the same way upstream's `_apply_search_env` does.
  - Preflight: before Stage 1, run `preflight_models` on the council (plus the chairman in `full` mode). On failure, record it with `storage.add_error_message`, emit `{"type": "error", "message": …}`, and end the run as `failed`.
  - Search query: `generate_search_query` is async upstream; await it, and only when `search_keyword_extraction == "llm"` and the provider is not DuckDuckGo (upstream's rule).
  - Progress entry: add `run_id` and `event_count` to the run's `_active_runs` entry and to `/progress`'s response, so the UI can re-attach with `streamRun(run_id, …, from_event)`.
  - Chat history: make `main._build_chat_history` (and the run's history) prefer `response_prompt_safe` and pass content through `strip_thinking_tags`, so thinking blocks are not sent back as context.
  - Extra tests in `test_runs_resume.py`: preflight failure fails the run and stores the error; `council_models` and prior turns reach Stage 1 as `models_override`/`history` with thinking stripped; `/progress` reports the background run with `run_id`.

- [ ] **Step 4: Add the routes to `backend/main.py`.**

  ```python
  from .runs import RunManager, VALID_EXECUTION_MODES
  ```

  After `_active_runs` is defined:

  ```python
  RUN_MANAGER = RunManager(progress=_active_runs)
  ```

  Copy the five route handlers from `git show pre-integration-2026-09-25:backend/main.py | sed -n 99,170p` unchanged, except that the body model is upstream's `SendMessageRequest`. Place them after the `/progress` route (around `main.py:753`).

- [ ] **Step 5: Run the tests.**

  ```bash
  uv run pytest backend/tests/test_runs_resume.py backend/tests/test_main_api_routes.py backend/tests/test_advisor_progress.py backend/tests/test_run_summary.py backend/tests/test_conversation_cost.py -q
  ```

  Expected: all pass.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/runs.py backend/main.py backend/tests/test_runs_resume.py backend/tests/test_main_api_routes.py
  git commit -m "feat(fork): restore resumable council runs on upstream stage pipeline and progress map"
  ```

**Task 10 execution notes (2026-09-25).** Done in `bbcdbfe`. Changes from the steps above:

- `GET …/runs/active` keeps the fork's contract, as Step 1 allows: `200 {"active_run": null}` when the conversation exists but has no live run (the state after a restart), 404 only for a missing conversation. The route test is `test_active_run_after_restart_reports_no_active_run`. Review Focus 4 and the Interfaces block are updated.
- `POST …/runs` with a bad mode returns 422, not 400. The body is upstream's `SendMessageRequest`, whose `execution_mode` is a `Literal`, and upstream's own `/message/stream` test expects 422.
- `start_run` also takes upstream's `documents`: the stages get `build_effective_query(content, documents)`, and the user message stores `content` plus attachment metadata, as `/message/stream` does. Invalid documents raise `DocumentError` (a `ValueError`) → 400. Without this, sending through `/runs` would silently drop attachments.
- Failed turns are recorded as upstream records them. Preflight failure and "all Stage 1 models failed" call `storage.add_error_message` (the fork saved an assistant message holding the failed Stage 1 results). An unexpected exception saves the partial results (`incomplete: True`) when Stage 1 produced any, otherwise an `Error: …` error message. Cancel still saves an `aborted` assistant message, now also with `incomplete: True`.
- Saved metadata gains upstream's `cost_report` and `web_search: True`; the `complete` event carries `{"metadata": {"cost_report": …}}` like upstream's.
- `start_run` refuses (409) while `_active_runs` already holds an entry for the conversation (an upstream `/message/stream` or advisor debate), and the run only pops its own entry. Otherwise the two would overwrite and pop each other's progress.
- Fixed a fork bug: `cancel_run` on a run whose task had not started yet cancelled the task before `_execute_run` entered its `try`, so the run stayed `queued` and kept the conversation locked. A queued run now only gets `cancel_event`, which `_execute_run` checks first.
- ~~`runs.py` mirrors upstream's `_apply_search_env`~~ Superseded by the review fixes below: `main` injects its own `_fetch_search_context`.
- `build_chat_history` lives in `runs.py`; `main._build_chat_history` delegates to it, so `/message/stream`, debate and `/message` also stop sending thinking blocks back as context.
- Fork tests edited for upstream API drift only: stage and title fakes accept upstream's keyword arguments (`**_kwargs`), and an autouse fixture stubs `preflight_models` (and title generation) so no test calls a real model.
- `run.stage2_label_maps_by_evaluator` / `stage2_candidate_maps_by_evaluator` stay keyed by model for the snapshot shape. A model listed twice collapses there; nothing in `runs.py` reads them. Aggregation reads each result's own `stage2_label_map`.

**Task 10 review fixes (2026-09-25).** Done in `55171ec` (`fix(fork): address Task 10 review findings in resumable runs`), each test-first:

- Stop while a finished first-message run waits for its title no longer loses the turn: the title is awaited through `asyncio.shield`, and the cancel handler waits for it with `asyncio.wait` (up to 2 s) after saving the partial turn.
- The terminal status is set immediately before the final event on every path (`_emit_final`), so a stream that attaches during finalization still gets `complete`/`cancelled`/`error`. `cancel_run` is a no-op once `cancel_event` is set. A task interrupted inside a handler (e.g. at shutdown) still ends as `cancelled` and releases the conversation.
- Guard in the other direction: upstream's per-conversation turn routes answer 409 while a `/runs` run is live (pure ASGI middleware in the fork block, inside `CORSMiddleware` so the browser can read the 409).
- Leaderboard metrics read upstream's result shape (the old `stage1_duration_ms`/`stage1_total_tokens`/`stage1_usage`/`stage1_response_id` fields never exist upstream): per-model time is arrival time within each stage, tokens come from `usage` (total, else input + output, either naming), cost from the `cost` record's `total_cost`. Rows carry `generation_time_ms`, `generation_time_seconds`, `generation_total_tokens`, `generation_total_cost`. The ~250 lines of print helpers are replaced by one `logger.info` per stage. `test_stage2_aggregate_rows_include_generation_time_and_tokens` was rewritten against upstream result shapes (API drift) and still checks time, tokens and cost on the rows.
- A failure after partial progress saves the turn with a top-level `error: "Error: …"` (the UI reads `msg.error`), alongside `metadata.error_message`.
- A cancel before any Stage 1 result saves no assistant turn (upstream's rule); only the user message stays. `test_cancel_before_run_starts_releases_conversation` now asserts that and that the conversation is free for the next run.
- The title task is cancelled in `finally` if it is still pending (early failure, all-failed Stage 1, or a title that outlives the cancel wait).
- Search goes through main's `_fetch_search_context`, injected as `RunManager(fetch_search_context=…)`; `runs.py` only derives the provider for `search_start`. `RunManager()` without it fails a web-search run with a clear error. `RUN_MANAGER` moved into the fork block, after `_fetch_search_context` is defined.
- `start_run` keeps at most the 20 most recent finished runs (never a live one), and a finished run drops its `history` and document-expanded `query` (neither is in the snapshot).
- Added tests: SSE replay from `from_event`, progress-entry ownership in `_finish_progress`, and the Review Focus 4 restart case on real tmp storage.

---

### Task 11: F9: council member cap of 12 everywhere

**Files:**
- Modify: `backend/settings.py:229`: `MAX_COUNCIL_MEMBERS = 12`.
- Modify: `backend/main.py:1988`: use `MAX_COUNCIL_MEMBERS`.
- Modify: `the_ai_counsel_mcp/tools/council.py:124`: use `MAX_COUNCIL_MEMBERS`, and update the tool description text `1-8` → `1-12`.
- Modify: `the_ai_counsel_mcp/tests/test_tools_council.py:132,141`: `"1-8"` → `"1-12"`.
- Modify: `frontend/src/components/CouncilSetup.jsx:8`: `MAX_MEMBERS = 12`.
- ~~Modify: `frontend/src/components/settings/CouncilConfig.jsx`~~ (not applicable in v0.13.1; see execution notes).
- Create: `frontend/src/constants/council.js`
- Test: `backend/tests/test_main_api_routes.py`, `backend/tests/test_council_presets.py` (12 kept / 13th trimmed), `the_ai_counsel_mcp/tests/test_tools_council.py` (13 rejected, 12 accepted).
- Modify: `README.md:242`, `docs/mcp/TOOLS.md:72,225`: `1 to 8`/`1–8` → `1 to 12`/`1–12`.

**Interfaces:**
- Produces: `backend.settings.MAX_COUNCIL_MEMBERS == 12`; `frontend/src/constants/council.js` exports `MAX_COUNCIL_MEMBERS = 12`.

- [ ] **Step 1: Write the failing tests.**

  Add to `backend/tests/test_main_api_routes.py`:

  ```python
  def test_put_settings_allows_twelve_council_models(monkeypatch):
      resp = client.put("/api/settings", json={"council_models": [f"openrouter:m/{i}" for i in range(12)]})
      assert resp.status_code == 200

  def test_put_settings_rejects_thirteen_council_models():
      resp = client.put("/api/settings", json={"council_models": [f"openrouter:m/{i}" for i in range(13)]})
      assert resp.status_code == 400
      assert "12" in resp.json()["detail"]
  ```

  The fork's `test_put_settings_invalid_council_model_count_returns_400` asserts a **minimum** of two models ("At least two council models"). Upstream has no minimum check and documents councils of 1 model. Per spec D8 the minimum follows upstream, so rename that test to `test_put_settings_accepts_single_council_model` and assert `status_code == 200` for `["only-one"]`. The owner confirmed this change (decision 2), so make it without stopping.

- [ ] **Step 2: Run them and confirm they fail.**

  ```bash
  uv run pytest backend/tests/test_main_api_routes.py -k council_models -q
  ```

  Expected: the twelve-model case returns 400 (`Maximum of 8`).

- [ ] **Step 3: Use one constant everywhere.**

  `backend/settings.py`: `MAX_COUNCIL_MEMBERS = 12`.
  `backend/main.py`:

  ```python
      if request.council_models is not None:
          if len(request.council_models) > MAX_COUNCIL_MEMBERS:
              raise HTTPException(
                  status_code=400,
                  detail=f"Maximum of {MAX_COUNCIL_MEMBERS} council models allowed"
              )
  ```

  (Import `MAX_COUNCIL_MEMBERS` from `.settings`.)
  `the_ai_counsel_mcp/tools/council.py`: `if not (1 <= len(models) <= MAX_COUNCIL_MEMBERS):`, with `from backend.settings import MAX_COUNCIL_MEMBERS` if the MCP package already imports from `backend`. Otherwise define `MAX_COUNCIL_MEMBERS = 12` at the top of that module with the comment `# keep in sync with backend.settings.MAX_COUNCIL_MEMBERS`. Replace `1-8` with `1-12` in its strings.
  Create `frontend/src/constants/council.js`:

  ```js
  // Keep in sync with backend.settings.MAX_COUNCIL_MEMBERS.
  export const MAX_COUNCIL_MEMBERS = 12;
  ```

  `CouncilSetup.jsx`: `import { MAX_COUNCIL_MEMBERS as MAX_MEMBERS } from '../constants/council';` in place of the local `const MAX_MEMBERS = 8;`. In `CouncilConfig.jsx`, disable the add button when `councilModels.length >= MAX_COUNCIL_MEMBERS` (import from `../../constants/council`).
  Docs: update `README.md:242` and `docs/mcp/TOOLS.md:72,225`.

- [ ] **Step 4: Run the tests.**

  ```bash
  uv run pytest backend/tests/test_main_api_routes.py backend/tests/test_council_presets.py the_ai_counsel_mcp/tests/test_tools_council.py -q
  grep -rn "Maximum of 8\|1-8\|1–8\|1 to 8 models\|MAX_MEMBERS = 8" backend the_ai_counsel_mcp frontend/src README.md docs/mcp || echo clean
  ```

  Expected: all pass, and `clean`.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/settings.py backend/main.py the_ai_counsel_mcp/tools/council.py the_ai_counsel_mcp/tests/test_tools_council.py frontend/src/constants/council.js frontend/src/components/CouncilSetup.jsx frontend/src/components/settings/CouncilConfig.jsx README.md docs/mcp/TOOLS.md backend/tests/test_main_api_routes.py
  git commit -m "feat(fork): raise council member cap to 12 via one shared constant"
  ```

---

**Task 11 execution notes (2026-09-25).** `CouncilConfig.jsx` in v0.13.1 has no add-member control (members are chosen in `CouncilSetup.jsx`), so its step did not apply. `test_configure_council_too_many_models` now sends 13 models instead of 9, so it still exercises the limit. The MCP package does not import from `backend`, so `the_ai_counsel_mcp/tools/council.py` has its own `MAX_COUNCIL_MEMBERS = 12` with a sync comment. The lineup grid still tops out at 8 seats, and that is more than cosmetic until Task 12: `getAddSlot` (`councilGridUtils.js`) returns `null` at 8 members, so the setup UI cannot add a 9th member; and a council of 9–12 that arrives another way (MCP `council_settings`, a preset, a config import) gets several members on the same `getMemberSlot` cell, so the covered cards cannot be clicked to edit or remove. Nothing is lost or rejected: the backend keeps and runs all 12. Task 12 Step 3b fixes the grid. After Task 11 the backend and MCP suites are fully green; the frontend failures (3 tests in `Stage2.test.js`, and `api.test.js` failing to load) belong to Task 12. Review follow-up: the MCP constant moved below the imports (ruff E402), and tests were added for preset trimming at 12/13 and the MCP tool accepting exactly 12.

---

### Task 12: Frontend: Requesty UI, resumable runs, Stage 2 display, fork tests

**Files:** (the real list after the rework; the original plan listed fewer files and put the fork code inside upstream files)
- Modify (upstream files: one-line fork hooks, plus small fork edits in `Stage2.jsx`, `RankingHeatmap.jsx` and `councilGridUtils.js`): `frontend/src/App.jsx`, `frontend/src/api.js`, `frontend/src/components/Settings.jsx`, `frontend/src/components/Stage2.jsx`, `frontend/src/components/ChatInterface.jsx`, `frontend/src/components/ChatInterface.css`, `frontend/src/components/CouncilGrid.jsx`, `frontend/src/components/EditableCouncilGrid.jsx`, `frontend/src/components/CouncilSetup.jsx`, `frontend/src/components/AdvisorSetup.jsx`, `frontend/src/components/RankingHeatmap.jsx`, `frontend/src/components/settings/CouncilConfig.jsx`, `frontend/src/components/settings/ProviderSettings.jsx`, `frontend/src/constants/oauthProviders.js`, `frontend/src/utils/councilGridUtils.js`, `frontend/vitest.config.js`.
- Create (fork modules): `frontend/src/forkApi.js` (Requesty and run API calls, SSE line parser, `responseError`), `frontend/src/hooks/useForkRuns.js` (council runs in `App.jsx`), `frontend/src/hooks/useRequestySettings.js` (Requesty in `Settings.jsx`), `frontend/src/utils/councilRuns.js` (pure run-outcome helpers), `frontend/src/utils/requesty.js`, `frontend/src/utils/stage2Labels.js`, `frontend/src/components/LeaderboardMetrics.jsx`, `frontend/src/components/ReconnectRunButton.jsx`, `frontend/src/components/CouncilGrid.fork.css`, `frontend/src/assets/icons/requesty.svg`, `frontend/vitest.setup.js`.
- Test: `frontend/src/forkApi.test.js`, `frontend/src/utils/councilRuns.test.js`, `frontend/src/utils/stage2Labels.test.js`, `frontend/src/utils/councilGridUtils.test.js` (new); `frontend/src/components/Stage2.test.js`, `frontend/src/api.test.js`, `frontend/src/utils/modelHelpers.test.js` (fork tests).
- Unchanged: `frontend/src/utils/modelHelpers.js` (its tests pass on upstream's helpers).

**Interfaces:**
- Consumes: the backend routes from Tasks 7, 9, and 10, and the Stage 2 result keys from Task 8.
- Produces: `deanonymizeStage2Text(text, result, labelToModel)` exported from `Stage2.jsx` (or `utils/stage2Labels.js` if `Stage2.jsx` must stay component-only), which uses `result.stage2_label_map ?? labelToModel`.

- [ ] **Step 1: Add the Stage 2 fallback tests.**

  Append to `frontend/src/components/Stage2.test.js` (keep the fork's existing cases):

  ```js
  import { describe, it, expect } from 'vitest';
  import { deanonymizeStage2Text } from './Stage2.jsx';

  describe('deanonymizeStage2Text', () => {
    const global = { 'Response A': 'm/one', 'Response B': 'm/two' };

    it('uses the evaluator-local map when present', () => {
      const result = { stage2_label_map: { 'Response A': 'm/two', 'Response B': 'm/one' } };
      expect(deanonymizeStage2Text('Response A beats Response B', result, global))
        .toContain('two');
    });

    it('falls back to the conversation map for pre-integration results', () => {
      expect(deanonymizeStage2Text('Response A beats Response B', {}, global))
        .toMatch(/one[\s\S]*two/);
    });
  });
  ```

  Match the asserted display format (for example `**one**`) to what upstream's `Stage2.jsx` already renders for a model label. Read its current replace logic first and keep that output format.

- [ ] **Step 2: Run the frontend tests and confirm they fail.**

  ```bash
  npm test --prefix frontend
  ```

  Expected: failures in `Stage2.test.js` (missing export) and `api.test.js` (missing run/Requesty functions), plus any `modelHelpers.test.js` gaps.

- [ ] **Step 3: Implement.**

  - `Stage2.jsx`: move upstream's label-replacement code into an exported `deanonymizeStage2Text(text, result, labelToModel)` that uses `const map = result?.stage2_label_map ?? labelToModel;`, and call it per evaluator.
  - `api.js`: copy `getRequestyModels`, `testRequestyKey`, `startRun`, `getActiveRun`, `getRun`, `streamRun`, and `cancelRun` from `git show pre-integration-2026-09-25:frontend/src/api.js` (lines 271–456) into upstream's `api` object. Reuse upstream's SSE parsing helper if it exists, and keep the fork's `parseSseDataChunk` only if upstream lacks one.
  - *Re-attach part superseded by spec decision 11 (see "Task 12 rework" below): the UI re-attaches through `/progress` polling, not `getActiveRun`/`streamRun(from_event)`.* `App.jsx`: for council modes (`chat_only`, `chat_ranking`, `full`) call `api.startRun` then `api.streamRun`, feeding events into the handler upstream's `sendMessageStream` uses. `startRun` sends the same body upstream's `sendMessageStream` sends (`search_provider`, `council_models`, `chairman_model`, `documents`). Debate keeps `sendMessageStream`/debate endpoints. On selecting a conversation, call `api.getActiveRun(id)`. When `active_run` is a snapshot, `streamRun(active_run.run_id, handler, signal, 0)` (or rebuild from the snapshot and pass `from_event = active_run.event_count`). When `active_run` is `null` (no live run, including after a backend restart), clear the loading state; a 404 means the conversation is gone. `GET /progress` for a `/runs` run also carries `run_id` and `event_count`. For Stage 2 de-anonymization, use each result's `stage2_label_map`, not `metadata.stage2_label_maps_by_evaluator` (keyed by model, so a duplicated model collapses).
  - Requesty UI: mirror the NVIDIA entries in `councilGridUtils.js` (`requesty: { color: '#6d5dfc', label: 'Requesty', logo: requestyLogo }` and `['requesty:', 'requesty']`), `CouncilSetup.jsx` (`requesty: 'requesty_api_key_set'` and the `||` availability check), `CouncilConfig.jsx`, `AdvisorSetup.jsx`, and `Settings.jsx`. In `ProviderSettings.jsx`, add a Requesty section cloned from the OpenRouter one (key field, Test button → `api.testRequestyKey`, enable toggle bound to `enabled_providers.requesty`).
  - `modelHelpers.js`: add back only the functions `modelHelpers.test.js` imports that upstream lacks (`git show pre-integration-2026-09-25:frontend/src/utils/modelHelpers.js`).
  - 409 handling (from Task 10's review fixes): `startRun` can answer `409 {"detail": "Conversation already has an active run"}` (a `/runs` run, an upstream stream or an advisor debate is live on the conversation); show it and re-attach through `getActiveRun`/`/progress` instead of leaving a stuck spinner. While a `/runs` run is live, do not start a debate (`/message/debate`) or advisor debate (`/debate/stream`) stream on that conversation; the backend now refuses those (and `/message`, `/message/stream`) with the same 409, so handle it there too.
  - Stage 2 display: de-anonymize with each Stage 2 result's own `stage2_label_map` (falling back to the conversation's `label_to_model` for old results).
  - Leaderboard (optional): aggregate ranking rows carry `generation_time_ms`, `generation_time_seconds`, `generation_total_tokens` and `generation_total_cost`, so the fork's leaderboard time/token/cost columns (`agg.generation_time_seconds`, `agg.generation_total_tokens`) can be ported.
  - Reference from the parallel attempt (`backup/local-step-a-8d67949`): `App.jsx` `createCouncilEventHandler`/`attachToRun` (Stop calls `cancelRun`; switching conversation or unmount only aborts the stream; lost stream keeps partial results with a "reload to resume" error), `api.js` `sendMessageRun`/`streamRun(runId, onEvent, signal, fromEvent)`, and the 12-slot layout in `EditableCouncilGrid.jsx`/`councilGridUtils.js`. Adapt to v0.13.1's components; do not copy wholesale.

- [ ] **Step 3b: Grid layouts for up to 12 members (from Task 11).**

  In `councilGridUtils.js`, derive the lineup rows from the shared constant (`LINEUP_ROWS = Math.ceil(MAX_COUNCIL_MEMBERS / LINEUP_COLS)`), make `getMemberSlot` give every member of an n-member council (n = 1..12) a distinct slot, make `getAddSlot` return a free slot while n < `MAX_COUNCIL_MEMBERS` and `null` at the cap, and make `getCouncilLayoutClass` return `layout-${n}-members` up to 12 (the `layout-9..12-members` CSS already exists in `CouncilGrid.css`). Default `EditableCouncilGrid`'s `maxMembers` to `MAX_COUNCIL_MEMBERS`. For n ≤ 8, keep upstream's exact slot positions. Reference: `git show origin/backup/local-step-a-8d67949:frontend/src/utils/councilGridUtils.js`. Add `councilGridUtils.test.js`: no duplicate slots for n = 1..12, an add slot for n < 12, none at 12, and upstream's positions unchanged for n ≤ 8.

- [ ] **Step 4: Run all frontend checks.**

  ```bash
  npm test --prefix frontend
  node --test frontend/src/utils/fontSize.test.js
  npm run lint --prefix frontend
  npm run build --prefix frontend
  ```

  Expected: all pass, and the build writes `frontend/dist`.

- [ ] **Step 5: Commit.**

  ```bash
  git add frontend/src
  git commit -m "feat(fork): restore Requesty UI, resumable runs and per-evaluator Stage 2 display"
  ```

**Task 12 execution notes (2026-09-25).** Done in four commits: `4baef46` (Stage 2 display), `e43698f` (API client), `ddf70a0` (Requesty UI and 12-seat grid), `215c5c0` (resumable runs in the UI). Changes from the steps above:

- `deanonymizeStage2Text` and the evaluator map selection live in `utils/stage2Labels.js`, as the Interfaces block allows: upstream's lint config (`react-refresh/only-export-components`) keeps `Stage2.jsx` component-only. The Step 1 tests import from there and assert the exact output (`**two** beats **one**`). The fork's `Stage2.test.js` label-map test reads `utils/stage2Labels.js` instead of `Stage2.jsx` (same assertions). Labels are replaced in one pass (`\bResponse [A-Z]\b`), so swaps never chain.
- Map order: `stage2_label_map`, then the fork's pre-integration keys (`stage2_label_model_map` on the result, `stage2_label_maps_by_evaluator` in the message metadata, which fork-saved conversations have), then `label_to_model`. The fork's old results stored **local** labels in `parsed_ranking`, so the Extracted Ranking and the heatmap read `parsed_ranking_models` when present and otherwise keep upstream's global reading (the heatmap keeps upstream's indexing, unknown labels included). New results are unaffected: `parsed_ranking_models` is `parsed_ranking` mapped through the global map.
- `modelHelpers.js` needed nothing: `modelHelpers.test.js` already passed on upstream's helpers.
- The fork's calls live in `frontend/src/forkApi.js`; `api.js` spreads `createForkApi(API_BASE)` into its `api` object and re-exports `parseSseDataChunk` (upstream's `_consumeSSEStream` is block-based and private, and `api.test.js` tests the fork's line parser). `api.js` reads `window` at load, so `vitest.setup.js` gives the Node test run a minimal `window`. Upstream's stream routes now throw errors carrying `status`/`detail` so a 409 can be recognized.
- *Superseded by the rework below (decision 11):* Re-attach uses `/progress` (`run_id`, `event_count`) from upstream's `checkForActiveRun`, then `getRun` only to merge the snapshot's metadata (label maps, rankings, search context). `getActiveRun` is in the API but the UI does not need it. Upstream's event handler moved unchanged into `createCouncilEventHandler(conversationId)` in `App.jsx`, with a local `setCurrentConversation` that only patches that conversation's in-flight assistant turn. An upstream change to that handler will conflict on the move; resolve it by applying the change inside the factory.
- As in the fork before integration, each tab remembers its open conversation in `sessionStorage` and reopens it after a reload, so "reload mid-run" re-attaches without a click.
- 409 (from `startRun`, `/message/debate`) keeps the question, shows a clear message and a **Reconnect** button (reload the conversation and re-attach); a lost stream keeps partial results with the same button. The button is a small hook in `ChatInterface.jsx` (`onResumeRun`, `msg.resumable`). The advisor debate shows a clear message on 409. Deleting a conversation with an attached run cancels the run.
- Requesty models come from `/api/models/requesty` (Task 9 keeps them out of `/api/models/direct`), not from `DIRECT_PROVIDER_KEY_FLAGS`, through `utils/requesty.js` in `CouncilSetup`, `AdvisorSetup` and the settings model list. The enable toggle sits in Council Config next to OpenRouter's, where v0.13.1 keeps every source toggle; the API-keys section has key, Test (auto-saves and enables), Disconnect and a model count. `requesty` is kept by `normalizeEnabledProviders`, the reset payloads and the Remote APIs master switch, and counts in `hasConfiguredProviders`/`countStoredCredentials`. New icon `assets/icons/requesty.svg`.
- The leaderboard also shows each row's `generation_total_cost` (formatted with upstream's `formatUsd`), next to the fork's time and token columns.
- Checks: vitest 6 passed + 3 failed with `api.test.js` not loading → 51 passed (7 files); `node --test` fontSize 3/3; lint 40 problems (29 errors, 11 warnings) before and after, same files; build OK; backend and MCP 692 passed. Browser smoke (Playwright, backend run from a temporary copy of the repo so `data/` was untouched, API responses stubbed, no model calls): typing in the Requesty key field and Test raise no errors; the lineup adds members up to 12 with 12 distinct slots and no add button at 12; Council Config shows the Requesty toggle; with stubbed `/runs` routes, a run renders each evaluator's text with its own map, a 409 keeps the question, a dropped stream keeps partial results, Stop posts `/cancel`, and a reload re-attaches with `from_event`. No page errors.


**Task 12 rework (2026-09-25, after the Task 12 spec and code-quality reviews).** One commit on `integrate/ai-counsel`. The owner chose to re-attach by polling (spec decision 11) and to keep fork code out of upstream files. Changes:

- `App.jsx` keeps upstream's council event handler inline, as at `1d1eace`. Council sends use `streamMethod = isDebate ? api.streamDebateMessage.bind(api) : forkRuns.streamRun`; `forkRuns.streamRun` (`hooks/useForkRuns.js`) checks `signal.aborted`, then `startRun`, records the `run_id` for Stop, then `streamRun` from event 0. Upstream's send `catch` starts with one early return, `forkRuns.handleSendError`, which handles detach, 409 and a dropped stream and leaves Stop and "nothing sent" to upstream. The other hooks are one line each: `restoreSelection` after the conversation list loads, `isCurrent` guards in `loadConversation` and `checkForActiveRun` (a stale `/progress` answer for A never touches B), `runId` on the polled placeholder turn, `stop` in `handleAbort`, `cancelBeforeDelete` in `handleDeleteConversation`, and the advisor 409 message.
- Reload and switching back follow the run through upstream's `/progress` polling. Stop on a polled run calls `api.cancelRun(progress.run_id)`. Re-selecting the open conversation while it polls or streams keeps following it (upstream would abort and not re-attach).
- Deleting a conversation cancels its live run even when this tab is detached from it (`getActiveRun`, then `cancelRun`).
- A 409 puts the unsent question back into the message box (`restoredInput` → `useRestoredInput` in `ChatInterface.jsx`) and reads "a council is still running or finishing in this conversation". A dropped stream with a 404 says the run is no longer on the server, not that it is still running.
- `utils/councilRuns.js` `classifySendError` decides the outcome: `detach` (left the conversation, or any failure for a conversation no longer on screen, so upstream's catch never patches or trims the one now shown), `stopped`, `conflict`, `interrupted` (resumable), `remove-optimistic`. `runProgressState` reads `/progress {active: false}` as "not loading" (Review Focus 4). Both are tested, with `polledRunId` and `interruptedRunMessage`.
- Requesty's toggle has one rule everywhere: off unless `enabled_providers.requesty === true` (`normalizeEnabledProviders`, `utils/requesty.js`, the backend default).
- Read-only grid for 9–12 members: `CouncilGrid.fork.css` spans the chairman over the three member rows (`grid-row: 1 / span 3`). Upstream's `grid-row: 1 / -1` resolves to row 1 because the grid has no explicit rows, so member 9 landed in the chairman's column. Checked by rendering `CouncilGrid` in headless Chromium at 1280 px (members in 4 columns × 3 rows, no card in the chairman's column, no overlap) and 480 px (same as upstream's mobile layout for 8 members).
- Leaderboard metrics moved to `components/LeaderboardMetrics.jsx`; Requesty settings to `hooks/useRequestySettings.js`. `Stage2.test.js` asserts `Stage2.jsx` renders with `getEvaluatorLabelMap` and `LeaderboardMetrics`. The fork SSE parser logs a throwing handler as "SSE event handler failed", not as a parse error, and keeps reading.
- Two deliberate rendering differences from upstream for old conversations: Stage 2 labels are matched as whole words (`\bResponse [A-Z]\b`, one pass), where upstream's `new RegExp(label, 'g')` replaced every occurrence, including one inside a longer word (`Response Alpha`); and an empty ranking text renders "No response" (the fork's `ThinkBlockRenderer`, which also collapses `<think>` blocks), where upstream's `MarkdownContent` rendered nothing.
- Checks: vitest 65 passed (7 files); `node --test` fontSize 3/3; lint 40 problems (29 errors, 11 warnings), the same as `1d1eace` (one `exhaustive-deps` warning now also names `loadConversation`, same line and rule); build OK. The upstream-file diff against `1d1eace` shrank from 390+/76− to 72+/40− lines (`App.jsx`, `Settings.jsx`, `Stage2.jsx`, `api.js`).


**Task 12 rework review follow-up (2026-09-26).** Spec-compliance review: every requirement met; code-quality review: nothing Critical or Important. Fixed in a follow-up commit:
- Stop on a polled run: a `/progress` answer already in flight could bring the stage spinners back on the stopped turn. The stopped turn is no longer `externalRun` (`markPolledTurnStopped`).
- The `'draft'` fallback applied to any send: after a send from A and then New Conversation, A's 409 put A's question into the new draft, a 500 trimmed the draft's turn, and Stop on the draft could cancel A's run. Only the send that started on the draft now counts it (`isSendOnScreen`, `startedOnDraft`).
- Deleting the open conversation, then switching before the delete finished, cleared the newly selected one: the post-delete check reads `forkRuns.isCurrent(id)`.
- The conflict and interrupted patches share one `markResumable`. `forkApi.streamRun` has tests for an event split across chunks, a last event with no trailing newline, and an abort mid-stream. Stale re-attach wording (here and in `backend/main.py`'s `/progress` comment) now points to decision 11; the Files block no longer claims one-line hooks only.

Not changed: Stop on a polled run with no `run_id` (an upstream stream or debate after a reload) only stops polling, as upstream does, because the UI cannot cancel it; after a 409 the unsent turn stays on screen with the Reconnect button while the question goes back into the box (the owner's requirement; Reconnect reloads the saved conversation); `api.getRun` and `/progress`'s `event_count` stay as API for clients; `resume()` checks `/progress` itself so a finished run clears the loading state, which upstream's `checkForActiveRun` does not; `useForkRuns` is covered through its pure helpers in `utils/councilRuns.js`, since the frontend has no DOM test environment.


---

### Task 13: Docs sync and cleanup

**Files:**
- Modify: `README.md`, `AGENTS.md`: add a "Fork additions" section listing F1–F9, one line each, with links to the spec.
- Modify: `CHANGELOG.md`: `## [Unreleased]` with Added (Requesty, resumable runs, balanced Stage 2, forced temperature, OpenRouter middle-out retry and generation endpoint) and Changed (council cap 12).
- Modify: `skills/the-ai-counsel-api/SKILL.md`: the `requesty_api_key` field, `requesty_api_key_set`, the five run routes, `GET /api/openrouter/generation`, and cap 12.
- Modify: `docs/mcp/TOOLS.md` (provider list and cap, if Task 11 missed anything), `docs/CREDENTIALS.md` (`api:requesty`, `REQUESTY_API_KEY`), `.env.example` (`REQUESTY_API_KEY=`).
- Keep: `docs/stage2-ordering-analysis.md`, `docs/HTTP2-HTTP3-Implementation-Report.md`, `scripts/test_reasoning_all_models.py`.
- Delete: `INTEGRATION_PLAN.md`.

- [ ] **Step 1: Check the version is unchanged and no stale names remain.**

  ```bash
  uv run pytest backend/tests/test_version_consistency.py -q
  grep -rn "llm_council_mcp\|llm-council-plus" --include=*.py --include=*.js --include=*.jsx --include=*.md . | grep -v CHANGELOG.md | grep -v docs/MIGRATION.md | grep -v docs/superpowers || echo clean
  ```

  Expected: pass, and `clean` (or only intentional historical references).

- [ ] **Step 2: Write the docs listed under Files, then delete the brief.**

  ```bash
  git rm -q INTEGRATION_PLAN.md
  ```

- [ ] **Step 3: Commit.**

  ```bash
  git add README.md AGENTS.md CHANGELOG.md skills/the-ai-counsel-api/SKILL.md docs .env.example
  git commit -m "docs: document fork additions and sync API/credential docs"
  ```

---

### Task 14: Full verification and pull request

**Files:** none (verification only).

- [ ] **Step 1: Run every automated check.**

  ```bash
  uv sync && uv run pytest backend/tests the_ai_counsel_mcp/tests -q
  uv run ruff check backend the_ai_counsel_mcp 2>&1 | tail -1   # upstream 614dfb9 itself reports 57 errors and its CI doesn't run ruff; expect no more than upstream's count, with none in fork-added code
  npm ci --prefix frontend && npm test --prefix frontend && node --test frontend/src/utils/fontSize.test.js
  npm run lint --prefix frontend && npm run build --prefix frontend
  ```

  Expected: all green. Compare with `$SCRATCH/stepB-fork-failures.txt`. Every entry there now passes.

- [ ] **Step 2: Verify history.**

  ```bash
  git replace -l                                                                  # empty
  test "$(git merge-base HEAD upstream/main)" = "$(git rev-parse upstream/main)" && echo linked
  git log --graph --oneline -25
  git log --oneline 58009fa..pre-integration-2026-09-25 | wc -l                   # 12 (6 fork commits + merge 0ffffa4 + brief 1de4bfb + a56aa0f + plan + decisions + count fix); the tag is 09395e0
  git merge-base --is-ancestor pre-integration-2026-09-25 HEAD && echo fork-kept
  git merge-base --is-ancestor 8351aa1 HEAD && echo lcp-kept
  ```

  Expected: `linked`, `fork-kept`, `lcp-kept`, and a graph showing both merge commits.

- [ ] **Step 3: Manual checks (spec success criterion 5). The owner runs these locally (decision 4); the execution session puts this list in the PR body and hands it over.**

  Run `./start.sh`, then `curl -s localhost:8001/api/health`. Expected: `"status"` ok and `"mcp": {"tools": 10}`. Check in the UI:
  1. Existing conversations from `data/` open and render, including Stage 2 of an old conversation.
  2. Settings → Requesty: save a key, reload, and it shows as set. A council with one `requesty:` model and one `openrouter:` model completes Stages 1–3, and Stage 2 shows each evaluator's own ordering.
  3. Reload the page during Stage 1: the run re-attaches and finishes.
  4. Restart the backend during Stage 1: the UI leaves the loading state, and the conversation shows the user message.
  5. A 12-member council saves. A 13th cannot be added.
  6. A debate run completes all rounds.

- [ ] **Step 4: Push and open the pull request into `main`.**

  ```bash
  git push origin integrate/ai-counsel
  ```

  Open a PR `integrate/ai-counsel` → `main` titled "Integrate fork features onto The AI Counsel v0.13.1". Use a merge commit, not squash or rebase, so both merge commits and the fork history reach `main`. Paste the Step 1 and Step 2 output into the PR body. Leave the PR open for the owner (decision 7).

---

### Task 15: Local machine cutover (`~/projects/llm-council-plus`)

**Files:** none in the repo. The owner runs this on the original machine after the PR merges (decision 8). The execution session only supplies these commands.

- [ ] **Step 1: Back up the local state.**

  ```bash
  cd ~/projects/llm-council-plus
  git stash push -u -m pre-integration            # uv.lock tweak + .vscode workspace
  cp -r data ~/llm-council-plus-data-backup
  ```

- [ ] **Step 2: Point the checkout at the integrated `main`.**

  ```bash
  git remote rename origin lcp
  git remote add origin https://github.com/root-reindeer-flotilla/the-ai-counsel.git
  git remote add upstream https://github.com/jacob-bd/the-ai-counsel.git
  git fetch --all
  git switch -C main origin/main
  git branch -u origin/main
  uv sync && npm install --prefix frontend
  ```

- [ ] **Step 3: Drop the stash.** Run `git stash show -p stash@{0}`. The `uv.lock` tweak is superseded by the relock in Task 4, and the `.vscode` workspace is personal. Then run `git stash drop` (or `git stash pop` and keep only the `.vscode` file, untracked).

- [ ] **Step 4: Check that data migrated.** Back up `data/settings.json` first. Run `./start.sh` and open the UI (or `curl localhost:8001/api/settings`) **before** any council run, MCP call or settings change: upstream's credential upgrade is lazy and runs on that first settings load (Review Focus 3); a settings write before it would drop the plaintext key without migrating it. Confirm old conversations load and the Requesty key shows as set. Run `grep -c requesty_api_key data/settings.json`. Expected: `0`, or the field is `null`.

  Expect one visible change in new turns: a turn where every model failed in Stage 1 (or model preflight failed) now reloads as a single error line, as upstream records it, not as per-model error cards. Turns saved before the integration keep their old look.

- [ ] **Step 5: Re-register MCP per `docs/MIGRATION.md`.**

  ```bash
  claude mcp remove llm-council
  claude mcp add the-ai-counsel -- uv --directory ~/projects/llm-council-plus run python -m the_ai_counsel_mcp
  ```

- [ ] **Step 6: Clean up branches that are fully merged.**

  ```bash
  for b in requesty reindeerflotilla stage2-balanced-cyclic-permutation backup/pre-integration; do
    git merge-base --is-ancestor "$b" main && git branch -d "$b"
  done
  ```

  Keep tag `pre-integration-2026-09-25` and the bundle until you are satisfied, then delete them. Optionally rename the directory to `~/projects/the-ai-counsel`, then re-run Step 5 with the new path.

- [ ] **Step 7: Future syncs.**

  ```bash
  git fetch upstream && git merge upstream/main
  ```

  Expected: an ordinary merge, with no graft needed.

---

### Task 16: Cleanup of backup branches and integration files (owner-triggered only)

**Gate: do not start this task until the owner explicitly says "start task 16".** Finishing Task 15, a merged PR, or anything else is not a trigger. Deleting branches cannot be undone from this repo, so confirm the exact list with the owner before each deletion step, even after the trigger.

**Precondition:** the PR `integrate/ai-counsel` → `main` is merged with a merge commit, and the owner has finished Task 15 on the local machine.

**Files:**
- Delete: `docs/superpowers/plans/2026-09-25-upstream-integration.md`, `docs/superpowers/specs/2026-09-25-upstream-integration-design.md` (this plan and its spec; they stay in git history). Keep upstream's own `docs/superpowers/*accessible-font-size*` files.
- Delete, if Task 13 has not already: `INTEGRATION_PLAN.md`.
- Modify: `README.md`/`AGENTS.md` "Fork additions" sections, only to drop links to the deleted spec (point to the merge commit or the CHANGELOG instead).

- [ ] **Step 1: Inventory and confirm.** List what exists now and show it to the owner for a yes/no per item:

  ```bash
  git fetch origin --prune
  git ls-remote --heads origin
  git tag -l 'pre-integration*'
  git ls-files docs/superpowers INTEGRATION_PLAN.md
  ```

  Candidates (as of 2026-09-25): remote branches `backup/pre-integration` and `fork-main` (both `0ffffa4`), `backup/pre-integration-2026-09-25` (`09395e0`), `backup/local-step-a-8d67949` (the parallel Step A attempt), `claude/laughing-cori-wxbb2c` (plan conversion), and `integrate/ai-counsel` once merged; the local tag `pre-integration-2026-09-25` if it was pushed; on the owner's machine, `backup/*` branches, the `pre-integration` stash, `~/llm-council-plus-pre-integration.bundle` and `~/llm-council-plus-data-backup` (the owner deletes those himself). Check each branch is fully merged into `main` or intentionally abandoned: `git branch -r --merged origin/main`, `git log --oneline origin/main..origin/<branch>`.

- [ ] **Step 2: Remove the integration files.** Only after the owner confirms:

  ```bash
  git switch -c chore/post-integration-cleanup origin/main
  git rm -q docs/superpowers/plans/2026-09-25-upstream-integration.md docs/superpowers/specs/2026-09-25-upstream-integration-design.md
  git rm -q --ignore-unmatch INTEGRATION_PLAN.md
  grep -rn "2026-09-25-upstream-integration" README.md AGENTS.md CHANGELOG.md docs || echo clean
  ```

  Fix any remaining links, run `uv run pytest backend/tests the_ai_counsel_mcp/tests -q` and `npm test --prefix frontend`, commit `chore: remove upstream-integration plan and spec`, push, and open a PR to `main` for the owner.

- [ ] **Step 3: Delete the confirmed remote branches.** One command per branch the owner approved, for example `git push origin --delete backup/local-step-a-8d67949`. Do not delete `main`. Delete `integrate/ai-counsel` only if the PR is merged. Record what was deleted, with each branch's last commit hash, in the PR description from Step 2 so it can be restored with `git push origin <hash>:refs/heads/<name>`.

- [ ] **Step 4: Confirm.** `git ls-remote --heads origin` shows only the branches the owner chose to keep.

