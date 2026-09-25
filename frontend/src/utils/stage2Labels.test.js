import { describe, it, expect } from 'vitest';

import {
  deanonymizeStage2Text,
  getEvaluatorLabelMap,
  getRankedEntries,
  replaceResponseLabels,
} from './stage2Labels';

const GLOBAL = { 'Response A': 'openrouter:m/one', 'Response B': 'openrouter:m/two', 'Response C': 'requesty:x/three' };

describe('getEvaluatorLabelMap', () => {
  it("prefers the result's own stage2_label_map", () => {
    const local = { 'Response A': 'openrouter:m/two' };
    expect(getEvaluatorLabelMap({ model: 'e', stage2_label_map: local }, GLOBAL, { e: {} })).toBe(local);
  });

  it("reads the fork's pre-integration keys", () => {
    const legacy = { 'Response A': 'openrouter:m/two' };
    expect(getEvaluatorLabelMap({ model: 'e', stage2_label_model_map: legacy }, GLOBAL)).toBe(legacy);
    expect(getEvaluatorLabelMap({ model: 'e' }, GLOBAL, { e: legacy })).toBe(legacy);
  });

  it('falls back to the conversation map, or an empty map', () => {
    expect(getEvaluatorLabelMap({ model: 'e' }, GLOBAL)).toBe(GLOBAL);
    expect(getEvaluatorLabelMap(undefined, undefined)).toEqual({});
  });
});

describe('replaceResponseLabels / deanonymizeStage2Text', () => {
  it('swaps labels in one pass (A<->B never chains)', () => {
    const swapped = { 'Response A': 'm/two', 'Response B': 'm/one' };
    expect(replaceResponseLabels('Response A > Response B > Response A', swapped))
      .toBe('**two** > **one** > **two**');
  });

  it('leaves unknown labels and look-alike words alone', () => {
    expect(replaceResponseLabels('Response Z, Response Alpha, Response C', GLOBAL))
      .toBe('Response Z, Response Alpha, **three**');
  });

  it('coerces non-string content and tolerates a missing map', () => {
    expect(replaceResponseLabels(null, GLOBAL)).toBe('');
    expect(replaceResponseLabels('Response A', null)).toBe('Response A');
  });

  it('renders an old (pre-integration) result exactly as upstream did', () => {
    const text = 'FINAL RANKING:\n1. Response B\n2. Response A';
    expect(deanonymizeStage2Text(text, { model: 'e', ranking: text }, GLOBAL))
      .toBe('FINAL RANKING:\n1. **two**\n2. **one**');
  });
});

describe('getRankedEntries', () => {
  it('uses parsed_ranking_models when present', () => {
    const result = {
      parsed_ranking: ['Response C', 'Response A'],
      parsed_ranking_models: ['requesty:x/three', 'openrouter:m/one'],
    };
    expect(getRankedEntries(result, GLOBAL)).toEqual([
      { label: 'Response C', model: 'requesty:x/three' },
      { label: 'Response A', model: 'openrouter:m/one' },
    ]);
  });

  it("maps a fork result's local parsed_ranking through its ranked models, not the global map", () => {
    // Fork pre-integration: parsed_ranking was local; "Response A" here meant m/two.
    const result = {
      parsed_ranking: ['Response A'],
      parsed_ranking_models: ['openrouter:m/two'],
      stage2_label_model_map: { 'Response A': 'openrouter:m/two' },
    };
    expect(getRankedEntries(result, GLOBAL).map((e) => e.model)).toEqual(['openrouter:m/two']);
  });

  it('reads an upstream result against the global map and drops unknown labels', () => {
    const result = { parsed_ranking: ['Response B', 'Response Z', 'Response A'] };
    expect(getRankedEntries(result, GLOBAL).map((e) => e.model))
      .toEqual(['openrouter:m/two', 'openrouter:m/one']);
    expect(getRankedEntries(result, GLOBAL, { dropUnknown: false }).map((e) => e.model))
      .toEqual(['openrouter:m/two', null, 'openrouter:m/one']);
  });

  it('handles empty and missing rankings', () => {
    expect(getRankedEntries({}, GLOBAL)).toEqual([]);
    expect(getRankedEntries(null, null)).toEqual([]);
  });
});
