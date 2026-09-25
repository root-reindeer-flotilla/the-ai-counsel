import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import {
  compactRunMetadata,
  isRunTurn,
  patchRunTurn,
  readStoredConversationId,
  writeStoredConversationId,
} from './councilRuns';

describe('compactRunMetadata', () => {
  it('keeps only non-empty values', () => {
    expect(compactRunMetadata({
      label_to_model: { 'Response A': 'm/a' },
      stage2_label_maps_by_evaluator: {},
      aggregate_rankings: [],
      search_query: '',
      search_context: 'ctx',
      aborted: false,
      error_message: null,
      generation_time_ms: 0,
    })).toEqual({
      label_to_model: { 'Response A': 'm/a' },
      search_context: 'ctx',
      generation_time_ms: 0,
    });
    expect(compactRunMetadata(undefined)).toEqual({});
  });
});

describe('isRunTurn / patchRunTurn', () => {
  const conv = {
    id: 'c1',
    messages: [{ role: 'user', content: 'q' }, { role: 'assistant', stage1: [] }],
  };

  it('patches only the in-flight assistant turn of the given conversation', () => {
    const next = patchRunTurn(conv, 'c1', (msg) => ({ ...msg, aborted: true }));
    expect(next).not.toBe(conv);
    expect(next.messages[1]).toEqual({ role: 'assistant', stage1: [], aborted: true });
    expect(conv.messages[1].aborted).toBeUndefined();
  });

  it('leaves other conversations and user-last conversations untouched', () => {
    expect(isRunTurn(conv, 'c2')).toBe(false);
    expect(patchRunTurn(conv, 'c2', () => ({}))).toBe(conv);
    const userLast = { id: 'c1', messages: [{ role: 'user', content: 'q' }] };
    expect(isRunTurn(userLast, 'c1')).toBe(false);
    expect(patchRunTurn(userLast, 'c1', () => ({}))).toBe(userLast);
    expect(patchRunTurn(null, 'c1', () => ({}))).toBe(null);
  });
});

describe('stored conversation id', () => {
  let store;
  beforeEach(() => {
    store = new Map();
    globalThis.sessionStorage = {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    };
  });
  afterEach(() => {
    delete globalThis.sessionStorage;
  });

  it('remembers real conversations and forgets drafts', () => {
    writeStoredConversationId('abc');
    expect(readStoredConversationId()).toBe('abc');
    writeStoredConversationId('draft');
    expect(readStoredConversationId()).toBe(null);
    writeStoredConversationId('abc');
    writeStoredConversationId(null);
    expect(readStoredConversationId()).toBe(null);
  });

  it('survives unavailable storage', () => {
    delete globalThis.sessionStorage;
    expect(readStoredConversationId()).toBe(null);
    expect(() => writeStoredConversationId('abc')).not.toThrow();
  });
});
