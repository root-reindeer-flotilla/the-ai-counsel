# Upstream Integration: Fork Features on The AI Counsel v0.13.1

**Source brief:** `INTEGRATION_PLAN.md` on branch `integrate/ai-counsel` (commit `1de4bfb`). This spec keeps that brief's goal and two-step merge, verifies its facts against the repository, and records the decisions and corrections found while checking it.

## Confirmed decisions (2026-09-25)

The repository owner confirmed these before execution started. They override anything below that disagrees.

1. **Branch.** Work happens on `integrate/ai-counsel` and is pushed there. Commit `399e044` (this spec and its plan, from `claude/laughing-cori-wxbb2c`) is cherry-picked onto it first. The branch head before the cherry-pick was `a56aa0f` ("chore: stop ignoring uv.lock"), one commit past the brief's `1de4bfb`.
2. **Minimum council size is 1**, following upstream. Task 11 rewrites the fork's minimum-2 test without further confirmation (D8).
3. **Council maximum is 12 everywhere** (D8). **npm only:** `frontend/bun.lock` and the bun launcher in `start.sh` are dropped (D9).
4. **Verification split.** All automated tests, lint and build run in the execution session. The manual UI checks (success criterion 5, which need real API keys and an existing `data/`) are done by the owner locally, from a checklist delivered before the PR merges.
5. **Execution method:** subagent-driven development (one implementer subagent per task, then a spec-compliance review and a code-quality review).
6. **Backup ref.** A durable backup of the branch head just before the Step A merge (`09395e0`) exists on `origin` before the first merge. The session's git proxy rejects tag pushes (HTTP 403), so the backup is the branch `backup/pre-integration-2026-09-25` at `09395e0`, created through the GitHub API. The tag `pre-integration-2026-09-25` exists locally in the execution clone at the same commit, and the owner may push it later with `git push origin 09395e0c31e7ca024779fc25b67f1eab60e1f0b3:refs/tags/pre-integration-2026-09-25`. The off-repo bundle is optional, because a cloud container is not durable.
7. **Pull request.** `integrate/ai-counsel` → `main`, merged with a merge commit (no squash or rebase). The PR is opened and left open for the owner.
8. **Local cutover (Task 15)** is the owner's. The execution session supplies the commands and does not run them.
9. **Upstream head.** If `upstream/main` has moved past v0.13.1 (`614dfb9`), merge the newer head and update the plan. At execution start (2026-09-25) upstream `main` was still `614dfb9`.
10. **Ollama scheduling follows upstream** (confirmed 2026-09-25, during execution). The fork ran Ollama models one at a time in Stages 1 and 2 (`_is_ollama_model`, `_run_ollama_sequential`), a feature the F1–F9 list missed. The owner does not use Ollama and chose to stay in sync with upstream to keep the code simple, so it is dropped: all council models run in parallel, as in v0.13.1. Docs must not describe sequential Ollama execution.
11. **Re-attaching to a running council polls; it does not replay the stream** (confirmed 2026-09-25, after the Task 12 reviews). A send from this tab streams its run live (`startRun`, then `streamRun` from event 0). A tab that reloads or switches back to a conversation with a live run follows it through upstream's existing `/progress` polling (about every 3 s), which reports the run's `run_id`, so Stop can still cancel it. Upstream's council event handler stays inline in `App.jsx`, and the fork hooks into it with one-line calls (the stream method for council turns, an early return in the send `catch`, Stop, delete, and guards so a stale `/progress` answer for one conversation never touches another). `GET /api/runs/{id}/stream?from_event=N` stays in the API but the UI does not use it to re-attach.

## Goal

Move the fork to The AI Counsel v0.13.1, keep every local feature, and keep history in a form where future upstream updates are a plain `git fetch upstream && git merge upstream/main`.

## Where things are now

All facts below were checked on 2026-09-25 in `root-reindeer-flotilla/the-ai-counsel` (full, unshallowed clone).

| Ref | Commit | Contents |
| --- | --- | --- |
| `origin/main` | `614dfb9` | Upstream `jacob-bd/the-ai-counsel` v0.13.1 (2026-09-18). Same commit as upstream `HEAD`. Its only root commit is `31c21d8` "Initial commit: The AI Counsel v0.7.0". |
| `origin/integrate/ai-counsel` | `1de4bfb` | The fork: `58009fa` (last sync with llm-council-plus) + 7 local commits + the merge `0ffffa4` + the brief. |
| `origin/backup/pre-integration`, `origin/fork-main` | `0ffffa4` | The fork without the brief. This is a remote backup of the local commits. |
| `jacob-bd/llm-council-plus` `main` | `8351aa1` | llm-council-plus v0.7.0, the final release of the deprecated repo. |

- `git merge-base origin/main origin/integrate/ai-counsel` returns nothing. The two histories share no commit.
- The fork is 92 commits behind `8351aa1`.
- **Shallow-clone trap:** a default (shallow) clone shows fake root commits `b6cea98` and `9b879dc`, and `31c21d8` seems missing. Run `git fetch --unshallow` before any graft.

### Trial merges (scratch worktree, 2026-09-25)

- **Step A** (`git merge lcp/main` into the fork): 15 conflicting files, the same list as the brief. Hunk counts: `.gitignore` 1, `CLAUDE.md` 1, `README.md` 2, `backend/council.py` 7, `backend/main.py` 6, `frontend/src/App.jsx` 7, `api.js` 2, `ChatInterface.jsx` 3, `CouncilGrid.jsx` 2, `Settings.jsx` 2, `Stage2.jsx` 4, `settings/CouncilConfig.jsx` 2, `utils/modelHelpers.js` 1, `pyproject.toml` 1, `uv.lock` 17.
- **Step B** (graft `31c21d8` onto `8351aa1`, then `git merge upstream/main`, with Step A resolved by taking the llm-council-plus side of every conflicted file): 12 conflicting files. `backend/ollama_client.py`, `backend/openrouter.py`, `backend/providers/{anthropic,custom_openai,deepseek,google,groq,mistral,openai}.py`, `start.sh`, `backend/tests/conftest.py` (add/add), and `docs/archive/GEMINI-2025-11-27.md` (modify/delete). The merge base resolves to `8351aa1`, which confirms the graft.

## Local features to keep

The brief listed five features. Reading the fork's code and tests found three more (marked **new**).

| # | Feature | Where it lives in the fork | Fork tests |
| --- | --- | --- | --- |
| F1 | Requesty.ai provider | `backend/requesty.py`, `backend/providers/requesty.py`, `config.py` (`REQUESTY_API_URL`, `get_requesty_api_key`), `settings.py` (`requesty` provider toggle, `requesty_api_key`), `main.py` (`/api/models/requesty`, `/api/settings/test-requesty`), `ProviderSettings.jsx`, `api.js` (`getRequestyModels`, `testRequestyKey`) | exercised by `test_stage2_permutation` model IDs |
| F2 | Resumable runs | `backend/runs.py` (`RunManager`, `RunState`), 5 routes in `main.py` (`POST /api/conversations/{id}/runs`, `GET …/runs/active`, `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/stream`, `POST /api/runs/{run_id}/cancel`), `api.js` (`startRun`, `getActiveRun`, `getRun`, `streamRun`, `cancelRun`), `App.jsx` | `test_runs_resume.py`, `api.test.js` |
| F3 | Balanced cyclic-permutation Stage 2 ordering | `council.py`: `_canonical_candidate_id`, `_build_stage2_candidates`, `_deterministic_cyclic_orders`, `_dedupe_valid_order`, and the reworked `stage2_collect_rankings` / `calculate_aggregate_rankings`; `Stage2.jsx` | `test_stage2_permutation.py`, `test_rankings_aggregation.py`, `Stage2.test.js` |
| F4 | Forced temperature 1.0 for specific models | `council.py`: `FORCED_TEMP_ONE_MODELS`, `FORCED_TEMP_ONE_PREFIXES`, `_normalize_model_for_rules`, `_should_force_temperature_one` | `test_temperature_overrides.py` |
| F5 | Thinking-content normalization | `council.py`: `strip_thinking_tags`, `normalize_thinking_content`, `_reasoning_details_to_text`, `_extract_thinking_segments` | `test_council_critical_paths.py` |
| F6 (**new**) | OpenRouter context-overflow detection and Stage 2 retry with `transforms=["middle-out"]` | `openrouter.py`: `is_context_overflow_error`, `is_context_overflow_response`, `_extract_error_info`; the retry in `council.py` Stage 2 | `test_stage2_middle_out.py`, `test_openrouter_critical_paths.py` |
| F7 (**new**) | OpenRouter generation-stats endpoint | `openrouter.fetch_generation`, `GET /api/openrouter/generation?id=…` | `test_main_api_routes.py` (`test_openrouter_generation_*`) |
| F8 (**new**) | OpenRouter reasoning and slug handling | `openrouter.py`: `_strip_openrouter_prefix`, `_resolve_to_canonical_slug`, `_is_openrouter_gemini3_reasoning_target`, `_is_openrouter_deepseek_reasoning_target` | `test_openrouter_critical_paths.py` |
| F9 | 12-member council cap | `main.py` (`> 12` check), `CouncilConfig.jsx` (`councilModels.length >= 12`) | `test_main_api_routes.py::test_put_settings_invalid_council_model_count_returns_400` |

Other fork files: `test_storage_integrity.py`, `test_search_pure_functions.py`, `test_main_api_routes.py` (remaining cases), `modelHelpers.test.js`, `scripts/test_reasoning_all_models.py`, `docs/stage2-ordering-analysis.md`, `docs/HTTP2-HTTP3-Implementation-Report.md`. These carry over. Each test either passes against upstream code or gets adapted to upstream's current API, without losing what it checks.

Fork files that are dropped: `frontend/bun.lock`, `.vscode/settings.json`, `GEMINI.md` (upstream removed it), the bun launcher in `start.sh` (it hardcodes `/home/patrick/.bun/bin/bun`), the `uv.lock` line in `.gitignore` (upstream tracks `uv.lock`), and `INTEGRATION_PLAN.md` (replaced by this spec and its plan).

## Approaches considered

1. **Single graft merge of the fork straight onto v0.13.1.** One commit, but the brief measured 25 conflicting files with fork and upstream edits mixed together. This is hard to review and hard to bisect.
2. **Two merges with features re-applied inside each merge commit (the brief's approach).** Every feature gets resolved twice: once against v0.7.0 and again against v0.13.1. About half the Step A work is thrown away in Step B.
3. **Two mechanical merges, then re-apply each feature once, test-first, on v0.13.1 (chosen).** Step A takes the llm-council-plus side of each conflicted file. Step B takes the upstream side. Each merge commit contains only conflict resolution. After that, each local feature comes back in its own commit, and the fork's own tests act as the failing tests. This follows the brief's rule, "take upstream's structure and re-apply local features on top". Each feature becomes one reviewable commit, and nothing is resolved twice. The cost is that the fork's tests fail between the Step B merge and the task that restores their feature. That is expected, and each task closes its own failures.

## Design decisions

### D1. Where the work happens

The work happens in a full clone of `root-reindeer-flotilla/the-ai-counsel`, on branch `integrate/ai-counsel`. Remotes:

- `origin`: this repo
- `lcp`: `https://github.com/jacob-bd/llm-council-plus.git`
- `upstream`: `https://github.com/jacob-bd/the-ai-counsel.git`

When CI and the manual checks pass, the branch merges into `origin/main` through a pull request. `origin/main` is currently identical to upstream, so after the merge `origin/main` = upstream + fork features, and later syncs are `git merge upstream/main`. Steps that only apply to the original `~/projects/llm-council-plus` machine (the stash, the `data/` backup, MCP re-registration, deleting old local branches) are grouped into one final cutover task.

### D2. The graft is temporary and scoped

`git replace --graft 31c21d8 8351aa1` exists only for the Step B merge. Remove it with `git replace -d 31c21d8` right after the merge commit exists, and never push `refs/replace/*`. Once the merge commit links both histories, `git merge-base HEAD upstream/main` returns upstream's head without the graft.

### D3. Stage 2 keeps upstream's generator contract

Upstream has three consumers of `stage2_collect_rankings`: `main.py:648`, `main.py:920`, and `debate.py:642`. All three treat the first yielded item, a `dict` without a `"model"` key, as a flat `label_to_model` map, and they use `len(label_to_model)` as the progress total. The fork's first yield was `{"type": "stage2_init_data", "label_to_model": …, "stage2_label_maps_by_evaluator": …, "stage2_candidate_maps_by_evaluator": …}`. Under upstream's consumers that gives a total of 4 and a broken label map.

The contract after integration:

- **First yield:** a flat `Dict[str, str]`, `"Response A" → model`, in canonical candidate order. This is unchanged from upstream.
- **Each result item** keeps upstream's keys and adds:
  - `stage2_label_map: Dict[str, str]`: this evaluator's local label → model.
  - `stage2_candidate_label_map: Dict[str, str]`: local label → canonical candidate id (`candidate_01`, …).
  - `parsed_ranking_local: List[str]`: the labels the evaluator actually wrote.
  - `parsed_ranking_candidate_ids: List[str]`
  - `parsed_ranking_models: List[str]`
- **`parsed_ranking`** holds **global** labels: local label → model → the global label for that model. This keeps the heatmap (`RankingHeatmap.jsx` reads `parsed_ranking`) and the MCP output (`stream_buffer.py` forwards `parsed_ranking`) correct without changes.
- **`calculate_aggregate_rankings`** is the fork's version (revised during execution, 2026-09-25). Upstream's version re-parses each evaluator's raw ranking text against the global `label_to_model`. Under balanced ordering that text uses the evaluator's local labels, so upstream's version would credit the wrong models. The fork's version reads `parsed_ranking_models` first, then `parsed_ranking_local` with `stage2_label_map`, and falls back to parsing the text only for legacy ballots. It also keeps the fork's soft weighting: a ballot counts with weight `ranked/expected`, and ballots below `STAGE2_HARD_CAP_MIN_COMPLETION = 0.25` are dropped. Its signature is upstream's plus `return_diagnostics: bool = False`, so upstream callers are unchanged. With complete ballots it gives the same result as upstream.
- **Stage 3 input** (revised after the Task 8 review, 2026-09-25): each evaluator's ranking text goes to the chairman verbatim, prefixed with a legend of its own labels (`(Labels in this evaluation: Response A = <model>; …)`). The first version rewrote `Response X` into global labels, but prose such as "Responses A and B" or a bare "A" escapes that rewrite and leaves one text in two label spaces. Results without `stage2_label_map` get no legend, as upstream.
- **Per-evaluator state is keyed by the evaluator's candidate id**, not its model id, so a model that sits in the council twice keeps two evaluations and distinct global labels.
- **MCP output** (revised after the Task 8 review): `ranking_text` is the evaluator's text in its own labels, so each MCP ranking entry also carries `stage2_label_map`. `parsed_ranking` stays global.
- **New keyword argument** `balanced_order: bool = True`. `debate.py` passes `balanced_order=False`. Debate's claim-verdict and paragraph-annotation parsers read labels out of the ranking text, and debate rounds compare labels across rounds. Both assume one shared label space. With `balanced_order=False`, every evaluator gets canonical order and `stage2_label_map == label_to_model`.
- **Frontend `Stage2.jsx`** de-anonymizes each evaluator's text with `result.stage2_label_map ?? labelToModel`.

### D4. Forced temperature moves into `backend/providers/temperature.py`

Add `FORCED_TEMPERATURE_ONE_MODELS`, `FORCED_TEMPERATURE_ONE_PREFIXES`, `should_force_temperature_one(model_id)`, and `resolve_temperature(model_id, requested)`. They match on the model part returned by `split_upstream_model`, so `openrouter:google/gemini-3-pro-preview`, `google:gemini-3-flash-preview`, and `requesty:google/gemini-2.5-flash` all match. `council.query_model` calls `resolve_temperature` before it dispatches. The provider-level `add_temperature_if_supported` omission rules still apply afterwards. For example, `openai:gpt-5` still sends no temperature.

### D5. Thinking normalization uses upstream helpers where they exist

Upstream has `strip_thinking_blocks` in `council.py`. `strip_thinking_tags` becomes an alias of it. The fork's `normalize_thinking_content` and `_reasoning_details_to_text` are re-added only because `test_council_critical_paths.py` and the Stage 2 display path use them. They are re-added unchanged and call `strip_thinking_blocks`.

Revised during execution (2026-09-25):
- `strip_thinking_tags` is a small fork function, not a bare alias. It runs `strip_thinking_blocks` and then also removes `<thinking>…</thinking>`, which upstream's regex does not match. Upstream's `THINK_BLOCK_RE` and `strip_thinking_blocks` are unchanged.
- Stage 2 and Stage 3 prompts take Stage 1/2 text through `_prompt_safe_field` (the stored `*_prompt_safe` field, or `strip_thinking_tags` of the raw field).
- **Display follows upstream:** hidden reasoning is stripped and not shown (upstream commit `a22d748`, covered by upstream's `test_title_generation.py`). The `<think>`-prepend in Stage 3 came from llm-council-plus v0.7.0 and was not a fork feature. The fork test's single display assertion now expects no `<think>` in the chairman response. To show chairman reasoning again, Stage 3 would return `normalize_thinking_content(...)["display_text"]`, which would reverse `a22d748` and break upstream's tests.

### D5b. OpenRouter extras go back into upstream's `openrouter.py` (F6–F8)

Take upstream's `backend/openrouter.py` and re-add, unchanged:

- `_extract_error_info`, `is_context_overflow_error`, `is_context_overflow_response`
- `_strip_openrouter_prefix`, `_resolve_to_canonical_slug`, the two reasoning-target helpers
- `fetch_generation`
- the `transforms: Optional[List[str]] = None` parameter on `query_model`

The fork's Stage 2 called `openrouter.query_model` directly. That skipped upstream's cost attribution (`attach_cost`) and forced temperature. The retry goes through the normal path instead:

- `OpenRouterProvider.query(..., transforms=None)` forwards `transforms`.
- `council.query_model(..., transforms=None)` passes `transforms` only to `OpenRouterProvider`.
- Stage 2 calls `query_model(m, messages, temperature=…, conversation_id=…)`. If `openrouter.is_context_overflow_response(response)` is true, it retries once with `transforms=["middle-out"]`.
- The result item records `stage2_transform_applied`, `stage2_retry_reason`, and `stage2_middle_out_mode`, the same as the fork.

### D6. Requesty follows upstream's provider wiring

Requesty is wired the same way as NVIDIA, the most recent upstream provider, with one difference: it is an aggregator toggle in `DEFAULT_ENABLED_PROVIDERS` (like `openrouter`), not a direct-provider toggle. The key lives in the credential store as `api:requesty`, with env override `REQUESTY_API_KEY`. `config.get_requesty_api_key()` reads `get_api_key("requesty")`. Costs: add `requesty` to `_SUPPORTED_PROVIDER_PREFIXES`. `_catalog_platform("requesty", …)` returns `None`, the same low-confidence handling as `custom`, so costs are estimated and never presented as exact.

### D7. Resumable runs: scope of "resume"

`RunManager` keeps runs **in memory** (`backend/runs.py` line 1: "In-memory run manager"). A run survives the client going away (page reload, closed tab, dropped network) and can be re-attached through `GET /api/runs/{id}/stream?from_event=N`. A run **cannot** survive a backend restart. The brief's check "kill the backend mid-run and resume" cannot pass with this design. It is replaced by two checks:

- Reloading the page mid-run re-attaches and finishes.
- After a backend restart, the conversation keeps the user message, `GET …/runs/active` returns `200 {"active_run": null}` (the fork's contract; 404 only when the conversation itself is missing), and the UI shows the conversation without a stuck spinner.

Making runs survive a backend restart would be a new feature and is out of scope.

Re-attaching in the UI (decision 11): a live send streams through `/runs/{id}/stream`; after a reload or on switching back, the UI follows the run through upstream's `/progress` polling, which carries `run_id` for Stop, instead of replaying the stream with `from_event`. Deleting a conversation cancels its live run, even one this tab no longer follows (`GET …/runs/active`, then cancel).

`runs.py` is adapted to upstream: the new Stage 2 contract (D3), upstream's `stage1_collect_responses`/`stage2_collect_rankings`/`stage3_synthesize_final` signatures (including `conversation_id=`), and upstream's `_active_runs` progress map, so `GET /api/conversations/{id}/progress` also reports runs started through `/runs`. The existing `/message/stream` and `/message/debate` endpoints stay as they are. Debate (Stage 4) keeps its own endpoints and does not go through `RunManager`. The two paths exclude each other per conversation: `POST …/runs` answers 409 while an upstream stream or advisor debate holds the conversation's `_active_runs` entry, and a small middleware in `main.py`'s fork block answers 409 to upstream's per-conversation turn routes (`/message`, `/message/stream`, `/message/debate`, `/debate/stream`) while a `/runs` run is live, without editing their bodies.

### D8. Council member cap is 12 everywhere

Upstream caps council size at 8 in six places: `backend/main.py:1988`, `backend/settings.py` `MAX_COUNCIL_MEMBERS`, `frontend/src/components/CouncilSetup.jsx` `MAX_MEMBERS`, `the_ai_counsel_mcp/tools/council.py:124`, `the_ai_counsel_mcp/tests/test_tools_council.py`, and the docs (`README.md`, `docs/mcp/TOOLS.md`). The fork's feature is 12. All six places move to one value, `MAX_COUNCIL_MEMBERS = 12`, from `backend.settings`. The frontend uses one constant. Advisor limits (2–4) do not change.

The fork also required a **minimum** of 2 council models (`test_put_settings_invalid_council_model_count_returns_400` asserts "At least two council models"). Upstream has no minimum check and documents councils of 1 model. The integration follows upstream (minimum 1). This was confirmed by the owner (decision 2), so Task 11 rewrites that test.

### D9. Tooling

- **Package manager:** npm (upstream). `frontend/bun.lock` is deleted.
- **Frontend tests:** vitest is added (`"test": "vitest run"`, devDependency `vitest`) so the fork's `api.test.js`, `Stage2.test.js`, and `modelHelpers.test.js` run. Upstream's `fontSize.test.js` uses `node:test`. It keeps running with `node --test` and is excluded from vitest through `vitest.config.js` `test.exclude`.
- **Python:** upstream `pyproject.toml`, plus the fork's `pytest-cov` dev dependency and `pythonpath = ["."]`, then `uv lock`. The fork tests use `@pytest.mark.anyio`, so `conftest.py` = upstream's fixtures + the fork's `anyio_backend` fixture.

### D10. Docs and version

Upstream docs are the base. Add a "Fork additions" section to `README.md` and `AGENTS.md` that lists F1–F9. Update the surfaces required by `docs/DOC-SYNC.md` for the settings and API changes (Requesty key, run routes, cap 12, generation endpoint): `skills/the-ai-counsel-api/SKILL.md`, `docs/mcp/TOOLS.md`, `docs/CREDENTIALS.md`, and `CHANGELOG.md` `## [Unreleased]`. The version stays `0.13.1`, because this is a fork integration and not an upstream release.

## Success criteria

1. `uv run pytest backend/tests the_ai_counsel_mcp/tests -q` passes with every upstream test and every fork test, adapted only where the upstream API changed.
2. `npm test --prefix frontend`, `node --test frontend/src/utils/fontSize.test.js`, `npm run lint --prefix frontend`, and `npm run build --prefix frontend` all pass.
3. `git merge-base HEAD upstream/main` equals `git rev-parse upstream/main`, with no replace refs present (`git replace -l` prints nothing).
4. `git log --graph --oneline` shows the 7 fork commits, the llm-council-plus line, and the The AI Counsel line joined by the two merge commits.
5. Manual checks pass with `./start.sh`:
   - Existing `data/` conversations load.
   - A council run with one Requesty model and one OpenRouter model completes Stages 1–3, and Stage 2 shows per-evaluator ordering.
   - A page reload mid-run re-attaches and finishes.
   - A backend restart mid-run leaves no stuck UI.
   - The Requesty key saves, survives a reload, and shows as set.
   - A 12-member council saves, and a 13-member council is rejected.
   - A debate run completes.

## Out of scope

- Runs that survive a backend restart.
- Contributing any fork feature back to upstream.
- Changing advisor limits, cost-catalog sources, or the upstream UI beyond what the fork features need.
