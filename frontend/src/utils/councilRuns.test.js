import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import {
  advisorConflictMessage,
  classifySendError,
  interruptedRunMessage,
  isRunTurn,
  isSendOnScreen,
  markPolledTurnStopped,
  patchRunTurn,
  polledRunId,
  readStoredConversationId,
  runProgressState,
  RUN_GONE_MESSAGE,
  RUN_STREAM_LOST_MESSAGE,
  writeStoredConversationId,
} from './councilRuns';

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

// Review Focus 4 (frontend): how a council send ends, and a backend restart.
describe('classifySendError', () => {
  const abort = () => new DOMException('Aborted', 'AbortError');
  const httpError = (status) => Object.assign(new Error('x'), { status });

  it('detaches when the user leaves while the run goes on', () => {
    expect(classifySendError({ error: abort(), runStarted: true })).toBe('detach');
  });

  it('never marks a conversation that is no longer on screen', () => {
    expect(classifySendError({ error: abort(), runStarted: false, isCurrent: false })).toBe('detach');
    expect(classifySendError({ error: abort(), stopRequested: true, isCurrent: false })).toBe('detach');
    // Upstream's catch would patch or trim whichever conversation is shown now.
    expect(classifySendError({ error: httpError(409), isCurrent: false })).toBe('detach');
    expect(classifySendError({ error: httpError(500), isCurrent: false })).toBe('detach');
    expect(classifySendError({ error: new Error('dropped'), runStarted: true, isCurrent: false })).toBe('detach');
  });

  it('marks the turn stopped on Stop, on a server-side cancel, and before a run existed', () => {
    expect(classifySendError({ error: abort(), runStarted: true, stopRequested: true })).toBe('stopped');
    expect(classifySendError({ error: abort(), runStarted: true, serverCancelled: true })).toBe('stopped');
    expect(classifySendError({ error: abort(), runStarted: false })).toBe('stopped');
  });

  it('reports a busy conversation (409) as a conflict', () => {
    expect(classifySendError({ error: httpError(409) })).toBe('conflict');
    expect(classifySendError({ error: httpError(409), runStarted: false })).toBe('conflict');
  });

  it('keeps the partial turn when a started run loses its stream', () => {
    const dropped = Object.assign(new Error('dropped'), { code: 'RUN_STREAM_DROPPED' });
    expect(classifySendError({ error: dropped, runStarted: true })).toBe('interrupted');
    expect(classifySendError({ error: httpError(404), runStarted: true })).toBe('interrupted');
  });

  it("leaves other failures to upstream's optimistic-turn removal", () => {
    expect(classifySendError({ error: httpError(500) })).toBe('remove-optimistic');
    expect(classifySendError({ error: new TypeError('Failed to fetch') })).toBe('remove-optimistic');
  });
});

describe('interruptedRunMessage', () => {
  it('does not claim a run the server no longer has is still going', () => {
    expect(interruptedRunMessage(Object.assign(new Error('x'), { status: 404 }))).toBe(RUN_GONE_MESSAGE);
    expect(RUN_GONE_MESSAGE).not.toMatch(/still running|keeps running/i);
    expect(interruptedRunMessage(new Error('dropped'))).toBe(RUN_STREAM_LOST_MESSAGE);
  });
});

describe('runProgressState', () => {
  it('is not loading when /progress reports no run (e.g. after a backend restart)', () => {
    expect(runProgressState({ active: false })).toEqual({ loading: false, runId: null });
    expect(runProgressState(null)).toEqual({ loading: false, runId: null });
  });

  it('carries the run id of a live /runs run, and none for upstream streams or advisors', () => {
    expect(runProgressState({ active: true, mode: 'council', run_id: 'r1' })).toEqual({ loading: true, runId: 'r1' });
    expect(runProgressState({ active: true, mode: 'council' })).toEqual({ loading: true, runId: null });
    expect(runProgressState({ active: true, mode: 'advisors', run_id: 'r1' })).toEqual({ loading: true, runId: null });
  });
});

describe('polledRunId', () => {
  it('returns the run id of a turn followed through /progress polling', () => {
    const conv = {
      id: 'c1',
      messages: [{ role: 'user', content: 'q' }, { role: 'assistant', externalRun: true, runId: 'r1' }],
    };
    expect(polledRunId(conv, 'c1')).toBe('r1');
    expect(polledRunId(conv, 'c2')).toBe(null);
    const streamed = { id: 'c1', messages: [{ role: 'user' }, { role: 'assistant' }] };
    expect(polledRunId(streamed, 'c1')).toBe(null);
  });
});

describe('advisorConflictMessage', () => {
  it('explains a 409 and leaves other errors to upstream', () => {
    expect(advisorConflictMessage(Object.assign(new Error('x'), { status: 409 }))).toMatch(/still running or finishing/);
    expect(advisorConflictMessage(new Error('x'))).toBe(null);
  });
});

describe('isSendOnScreen', () => {
  it('is on screen while its conversation is shown', () => {
    expect(isSendOnScreen({ conversationId: 'a', currentId: 'a' })).toBe(true);
    expect(isSendOnScreen({ conversationId: 'a', currentId: 'b' })).toBe(false);
  });

  it("counts the draft only for the send that started on it (the first send's id is not committed yet)", () => {
    expect(isSendOnScreen({ conversationId: 'new', currentId: 'draft', startedOnDraft: true })).toBe(true);
    // A send from A, then New Conversation: the new draft is not A.
    expect(isSendOnScreen({ conversationId: 'a', currentId: 'draft', startedOnDraft: false })).toBe(false);
  });
});

describe('markPolledTurnStopped', () => {
  it('marks the turn stopped and idle, and takes it off /progress polling', () => {
    const idle = { stage1: false };
    const msg = { role: 'assistant', externalRun: true, runId: 'r1', loading: { stage1: true } };
    expect(markPolledTurnStopped(msg, idle)).toEqual({
      role: 'assistant', externalRun: false, runId: null, aborted: true, loading: idle,
    });
  });
});
