# Stage 1 -> Stage 2 Ordering and Tracking Analysis

## Scope

This report analyzes how Stage 1 responses are currently passed into Stage 2 for grading, whether anonymized labels are stable, and what would be required for each Stage 2 model to receive a different response order while preserving accurate tracking and aggregate scoring.

## Direct answers

- **Are Stage 1 responses sent to Stage 2 in the same order every time?**  
Not guaranteed. Stage 2 labels are assigned from the order of `stage1_results`, and `stage1_results` is populated in completion order (as model calls finish), not fixed configured order.
- **Do all Stage 2 models currently receive the same anonymized order inside one run?**  
Yes. One shared `responses_text` prompt is built once and sent to every Stage 2 model.
- **Can each Stage 2 model receive a different order and still be tracked correctly?**  
Yes, but this requires per-evaluator label mappings and canonical response IDs so local labels can be translated back before aggregation.

## Current implementation flow

### 1) Stage 1 list order source

`stage1_collect_responses()` yields results as async tasks complete. In `backend/runs.py`, each emitted item is appended to `run.stage1_results` immediately.

This means Stage 1 list order is influenced by runtime completion timing (network latency, provider speed, local scheduling), not a strict fixed model order.

### 2) Stage 2 labeling/anonymization

In `backend/council.py`:

- Successful Stage 1 responses are filtered:
  - `successful_results = [r for r in stage1_results if not r.get('error')]`
- Labels are generated sequentially from this filtered list:
  - `Response A`, `Response B`, `Response C`, ...
- `label_to_model` is built once for the run by zipping labels with `successful_results`.
- `responses_text` is built once using that same zip and included in one `ranking_prompt`.
- Every Stage 2 model receives the same `messages = [{"role":"user","content": ranking_prompt}]`.

### 3) Stage 2 aggregation assumptions

`calculate_aggregate_rankings()` uses a single global `label_to_model` dictionary. Parsed labels from each Stage 2 model ballot (e.g., `Response B`) are mapped through that one dictionary to assign rank contributions.

Therefore, current aggregation logic assumes that `Response X` means the same underlying Stage 1 response for every Stage 2 evaluator in that run.

## Consequences for position bias

The current design creates one shared order per run for all evaluators. If LLM attention degrades for middle prompt content, that can introduce systematic position effects across all Stage 2 ballots in a run.

Even though the shared order may vary between runs (due to Stage 1 completion timing), there is no per-evaluator balancing within the same run.

## What would be required for per-evaluator shuffled order

## Backend requirements

- **Canonical response identity**
  - Assign stable internal IDs for Stage 1 candidates (for example `candidate_01...candidate_12`).
  - Keep a canonical map (candidate -> originating Stage 1 model).
- **Per-evaluator permutation**
  - For each Stage 2 evaluator model, generate a unique permutation of candidate IDs.
  - Build evaluator-specific prompt text where local labels (`Response A`, etc.) map to permuted candidates.
- **Per-evaluator label map persistence**
  - Store a map for each evaluator:
    - `local_label_to_candidate[evaluator_model]["Response A"] = "candidate_07"`
- **Ballot normalization before scoring**
  - Parse each evaluator's local labels from its ranking output.
  - Translate local labels -> canonical candidate IDs using that evaluator's map.
  - Aggregate scores only after translation to canonical identity.
- **Diagnostics/auditability**
  - Persist per-evaluator mappings and normalized parsed rankings to make results debuggable.

These changes satisfy:

1. each response is tracked by the application, and
2. `Response A` can differ per evaluator while still being separated and scored correctly.

## Frontend impact

Frontend changes are likely moderate and mostly confined to Stage 2 display logic.

Current `Stage2.jsx` de-anonymizes using one global `labelToModel` map. With per-evaluator permutations:

- raw ranking text for a tab should be de-anonymized using that tab evaluator's local map;
- extracted ranking list in the tab should also use evaluator-specific mapping;
- aggregate leaderboard can remain mostly unchanged if backend continues returning canonical model-level aggregate rows.

So frontend does need updates, but not a broad architecture rewrite.

## Seeded shuffling recommendation

Seeded shuffling is a practical default:

- reproducible for debugging and A/B checks;
- still provides position balancing;
- can be keyed by run identifiers and evaluator model (for example: conversation ID + turn index + evaluator model).

Unseeded pure randomness is possible but harder to reproduce and diagnose.

## Alternatives to full per-evaluator permutation

- **Run-level single shuffle/rotation**
  - One reordered prompt for everyone each run. Easy, but does not balance positions across evaluators within a run.
- **Balanced design (Latin-square style)**
  - Systematically distribute candidates across positions across evaluators; better statistical fairness than ad-hoc random.
- **Pairwise/subset judging**
  - Compare smaller sets instead of full 1-12 list; may reduce middle-loss but changes scoring behavior.
- **Prompt-level mitigation**
  - Normalize response lengths and structure, or force staged evaluation passes. Easier to implement but weaker than true order balancing.

## Bottom line

- Current behavior uses one shared anonymized order per run for all Stage 2 evaluators.
- That order comes from Stage 1 result list order (completion-driven), not a strict fixed council index.
- Supporting per-evaluator different `Response A/B/C...` semantics is feasible and clean if canonical IDs + evaluator-specific maps + normalization-before-aggregation are added.

