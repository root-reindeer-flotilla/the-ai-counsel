import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { deanonymizeStage2Text } from '../utils/stage2Labels';

describe('Stage2 reasoning renderer wiring', () => {
  it('uses ThinkBlockRenderer for ranking content', () => {
    const src = readFileSync(resolve(__dirname, 'Stage2.jsx'), 'utf8');
    expect(src).toContain("import ThinkBlockRenderer from './ThinkBlockRenderer';");
    expect(src).toContain('<ThinkBlockRenderer');
  });
});

describe('Stage2 leaderboard metric formatting', () => {
  it('renders average, time, and token metrics when present', () => {
    // The metrics moved to the fork component LeaderboardMetrics.jsx, which
    // Stage2.jsx renders in each leaderboard row (smaller upstream diff).
    const stage2Src = readFileSync(new URL('./Stage2.jsx', import.meta.url), 'utf8');
    expect(stage2Src).toContain('<LeaderboardMetrics agg={agg} />');
    const src = readFileSync(resolve(__dirname, 'LeaderboardMetrics.jsx'), 'utf8');
    expect(src).toContain('agg?.generation_time_seconds');
    expect(src).toContain('agg?.generation_total_tokens');
    expect(src).toContain('safeRowTokens.toLocaleString(\'en-US\')');
    expect(src).toContain('{" | "}');
    expect(src).toContain('{generationTimeLabel}');
    expect(src).toContain('{generationTokensLabel}');
  });
});

describe('Stage2 evaluator label map selection', () => {
  it('prefers evaluator-specific mapping and falls back to global mapping', () => {
    // The selection moved to utils/stage2Labels.js: Stage2.jsx may only export
    // components (react-refresh lint rule), and the helper is now unit-tested.
    const src = readFileSync(resolve(__dirname, '../utils/stage2Labels.js'), 'utf8');
    expect(src).toContain('function getEvaluatorLabelMap');
    expect(src).toContain('currentRanking?.stage2_label_model_map');
    expect(src).toContain('stage2LabelMapsByEvaluator[evaluatorModel]');
    expect(src).toContain('return labelToModel || {}');
    // Stage2.jsx renders each evaluator's text with that selector.
    const stage2Src = readFileSync(new URL('./Stage2.jsx', import.meta.url), 'utf8');
    expect(stage2Src).toContain('getEvaluatorLabelMap(currentRanking, labelToModel, stage2LabelMapsByEvaluator)');
    expect(stage2Src).toContain('replaceResponseLabels(rankingText, currentLabelMap)');
  });
});


describe('deanonymizeStage2Text', () => {
  const global = { 'Response A': 'm/one', 'Response B': 'm/two' };

  it('uses the evaluator-local map when present', () => {
    const result = { stage2_label_map: { 'Response A': 'm/two', 'Response B': 'm/one' } };
    expect(deanonymizeStage2Text('Response A beats Response B', result, global))
      .toBe('**two** beats **one**');
  });

  it('falls back to the conversation map for pre-integration results', () => {
    expect(deanonymizeStage2Text('Response A beats Response B', {}, global))
      .toBe('**one** beats **two**');
  });
});
