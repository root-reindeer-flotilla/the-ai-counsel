import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

import {
  getAddSlot,
  getCouncilLayoutClass,
  getMemberSlot,
  getProviderInfo,
  LINEUP_COLS,
  PROVIDER_CONFIG,
} from './councilGridUtils';
import { MAX_COUNCIL_MEMBERS } from '../constants/council';

// Upstream v0.13.1's 8-seat slot rules, kept here as the reference for n <= 8.
const UPSTREAM_SLOTS = 8;
function upstreamAddSlot(memberCount) {
  if (memberCount >= 8) return null;
  if (memberCount < LINEUP_COLS) return LINEUP_COLS - 1 - memberCount;
  return UPSTREAM_SLOTS - 1 - (memberCount - LINEUP_COLS);
}
function upstreamMemberSlot(memberIndex, memberCount) {
  if (memberCount <= LINEUP_COLS) return LINEUP_COLS - 1 - memberIndex;
  const row2Count = memberCount - LINEUP_COLS;
  if (memberIndex < row2Count) return UPSTREAM_SLOTS - 1 - memberIndex;
  return memberIndex - row2Count;
}

const range = (n) => Array.from({ length: n }, (_, i) => i);

describe('council lineup slots', () => {
  it('uses the shared 12-member cap', () => {
    expect(MAX_COUNCIL_MEMBERS).toBe(12);
  });

  it('gives every member of a 1..12 member council its own slot', () => {
    for (let n = 1; n <= MAX_COUNCIL_MEMBERS; n += 1) {
      const slots = range(n).map((i) => getMemberSlot(i, n));
      expect(new Set(slots).size).toBe(n);
      slots.forEach((slot) => {
        expect(slot).toBeGreaterThanOrEqual(0);
        expect(slot).toBeLessThan(MAX_COUNCIL_MEMBERS);
      });
    }
  });

  it('offers a free add slot until the cap, and none at 12', () => {
    for (let n = 0; n < MAX_COUNCIL_MEMBERS; n += 1) {
      const add = getAddSlot(n);
      expect(add).not.toBeNull();
      const taken = range(n).map((i) => getMemberSlot(i, n));
      expect(taken).not.toContain(add);
    }
    expect(getAddSlot(MAX_COUNCIL_MEMBERS)).toBeNull();
    expect(getAddSlot(MAX_COUNCIL_MEMBERS + 1)).toBeNull();
  });

  it("keeps upstream's positions for councils of up to 8", () => {
    for (let n = 0; n <= 8; n += 1) {
      if (n < 8) expect(getAddSlot(n)).toBe(upstreamAddSlot(n));
      range(n).forEach((i) => {
        expect(getMemberSlot(i, n)).toBe(upstreamMemberSlot(i, n));
      });
    }
  });

  it('adds the 9th member on a third row', () => {
    expect(getAddSlot(8)).toBe(11);
    expect(getMemberSlot(0, 9)).toBe(11);
  });
});

describe('getCouncilLayoutClass', () => {
  it('returns a layout per member count up to 12', () => {
    for (let n = 3; n <= 12; n += 1) {
      expect(getCouncilLayoutClass(n)).toBe(`layout-${n}-members`);
    }
    expect(getCouncilLayoutClass(13)).toBe('layout-12-members');
  });

  it("keeps upstream's layouts without a chairman", () => {
    expect(getCouncilLayoutClass(4, false)).toBe('');
    expect(getCouncilLayoutClass(6, false)).toBe('layout-5-members');
    expect(getCouncilLayoutClass(11, false)).toBe('layout-8-members');
  });
});

describe('getProviderInfo', () => {
  it('detects Requesty before the OpenRouter path fallback', () => {
    expect(getProviderInfo('requesty:openai/gpt-4o-mini')).toBe(PROVIDER_CONFIG.requesty);
    expect(getProviderInfo('requesty:anthropic/claude-sonnet-4')).toBe(PROVIDER_CONFIG.requesty);
    expect(PROVIDER_CONFIG.requesty.label).toBe('Requesty');
  });

  it('still maps OpenRouter and bare paths to OpenRouter', () => {
    expect(getProviderInfo('openrouter:openai/gpt-4o')).toBe(PROVIDER_CONFIG.openrouter);
    expect(getProviderInfo('meta-llama/llama-3-70b-instruct')).toBe(PROVIDER_CONFIG.openrouter);
  });
});

describe('read-only grid CSS for 9–12 members', () => {
  // Upstream's `grid-row: 1 / -1` collapses to row 1 (the grid has no explicit
  // rows), so the fork stylesheet makes the chairman span the three member rows.
  it('spans the chairman over three rows and is loaded by CouncilGrid.jsx', () => {
    const css = readFileSync(new URL('../components/CouncilGrid.fork.css', import.meta.url), 'utf8');
    for (let n = 9; n <= MAX_COUNCIL_MEMBERS; n += 1) {
      expect(css).toContain(`.council-grid.layout-${n}-members .council-card.chairman.chairman`);
    }
    expect(css).toMatch(/grid-row:\s*1 \/ span 3;/);
    expect(Math.ceil(MAX_COUNCIL_MEMBERS / LINEUP_COLS)).toBe(3);
    const jsx = readFileSync(new URL('../components/CouncilGrid.jsx', import.meta.url), 'utf8');
    expect(jsx).toContain("import './CouncilGrid.fork.css';");
  });
});
