import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('Stage2 reasoning renderer wiring', () => {
  it('uses ThinkBlockRenderer for ranking content', () => {
    const src = readFileSync(resolve(__dirname, 'Stage2.jsx'), 'utf8');
    expect(src).toContain("import ThinkBlockRenderer from './ThinkBlockRenderer';");
    expect(src).toContain('<ThinkBlockRenderer');
  });
});

describe('Stage2 leaderboard metric formatting', () => {
  it('renders average, time, and token metrics when present', () => {
    const src = readFileSync(resolve(__dirname, 'Stage2.jsx'), 'utf8');
    expect(src).toContain('agg?.generation_time_seconds');
    expect(src).toContain('agg?.generation_total_tokens');
    expect(src).toContain('safeRowTokens.toLocaleString(\'en-US\')');
    expect(src).toContain('{" | "}');
    expect(src).toContain('{generationTimeLabel}');
    expect(src).toContain('{generationTokensLabel}');
  });
});
