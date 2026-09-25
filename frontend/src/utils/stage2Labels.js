import { getShortModelName } from './modelHelpers';

/*
 * Stage 2 label spaces (fork: balanced cyclic Stage 2 ordering, spec D3).
 *
 * Each evaluator sees the Stage 1 answers in its own order, so the raw
 * `ranking` text of a Stage 2 result uses that evaluator's LOCAL labels
 * ("Response A" may be a different model for every evaluator). The result's
 * `stage2_label_map` maps those local labels to models. `parsed_ranking`
 * holds GLOBAL labels (the conversation's `label_to_model`), and
 * `parsed_ranking_models` holds the ranked model ids.
 */

const RESPONSE_LABEL_RE = /\bResponse [A-Z]\b/g;

/**
 * Label map for one evaluator's raw ranking text.
 *
 * Order: the result's own `stage2_label_map`; then the fork's pre-integration
 * keys (`stage2_label_model_map` on the result, or the message's
 * `stage2_label_maps_by_evaluator` keyed by model); then the conversation's
 * global map, which is right for upstream results saved before the
 * integration (one shared label space).
 */
export function getEvaluatorLabelMap(currentRanking, labelToModel, stage2LabelMapsByEvaluator) {
  if (currentRanking?.stage2_label_map) {
    return currentRanking.stage2_label_map;
  }
  if (currentRanking?.stage2_label_model_map) {
    return currentRanking.stage2_label_model_map;
  }
  const evaluatorModel = currentRanking?.model;
  if (
    evaluatorModel &&
    stage2LabelMapsByEvaluator &&
    stage2LabelMapsByEvaluator[evaluatorModel]
  ) {
    return stage2LabelMapsByEvaluator[evaluatorModel];
  }
  return labelToModel || {};
}

/**
 * Replace "Response X" labels in `text` with **short model names** from
 * `labelMap`. One pass, so a label is never rewritten twice; labels missing
 * from the map are left as written.
 */
export function replaceResponseLabels(text, labelMap) {
  const source = typeof text === 'string' ? text : String(text || '');
  if (!labelMap || Object.keys(labelMap).length === 0) return source;
  return source.replace(RESPONSE_LABEL_RE, (label) => (
    labelMap[label] ? `**${getShortModelName(labelMap[label])}**` : label
  ));
}

/** De-anonymize one evaluator's ranking text with its own label map. */
export function deanonymizeStage2Text(text, result, labelToModel, stage2LabelMapsByEvaluator) {
  return replaceResponseLabels(
    text,
    getEvaluatorLabelMap(result, labelToModel, stage2LabelMapsByEvaluator),
  );
}

/**
 * The models an evaluator ranked, best first, as [{ label, model }].
 *
 * Prefers `parsed_ranking_models` (present on every result since the
 * integration and on the fork's older results, whose `parsed_ranking` was in
 * local labels). Otherwise `parsed_ranking` is read against the global map.
 * Labels the map does not know are dropped (upstream's Extracted Ranking), or
 * kept with `model: null` when `dropUnknown` is false (upstream's heatmap,
 * which counts them in the positions).
 */
export function getRankedEntries(result, labelToModel, { dropUnknown = true } = {}) {
  const parsed = Array.isArray(result?.parsed_ranking) ? result.parsed_ranking : [];
  const models = Array.isArray(result?.parsed_ranking_models) ? result.parsed_ranking_models : [];
  if (models.length > 0) {
    return models.map((model, i) => ({ label: parsed[i] || null, model }));
  }
  const known = labelToModel ? new Set(Object.keys(labelToModel)) : null;
  return parsed
    .filter((label) => !dropUnknown || !known || known.has(label))
    .map((label) => ({ label, model: labelToModel?.[label] || null }));
}
