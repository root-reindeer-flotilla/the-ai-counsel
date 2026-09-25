import { describe, it, expect, vi, afterEach } from 'vitest';

import { createForkApi, isRunConflictError, parseSseDataChunk } from './forkApi';
import { api } from './api';
import { isRequestySourceEnabled, loadRequestyModels } from './utils/requesty';

const BASE = 'http://backend';

function streamResponse(text, init = {}) {
  return new Response(new Blob([text]).stream(), { status: 200, ...init });
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fork API wiring', () => {
  it('adds the Requesty and run calls to the upstream api object', () => {
    for (const name of ['getRequestyModels', 'testRequestyKey', 'startRun', 'getActiveRun', 'getRun', 'streamRun', 'cancelRun']) {
      expect(typeof api[name]).toBe('function');
    }
  });
});

describe('startRun', () => {
  it("sends upstream's SendMessageRequest body to /runs", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ run_id: 'r1', event_count: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    const run = await createForkApi(BASE).startRun('c1', {
      content: 'Q?',
      searchProvider: 'tavily',
      executionMode: 'chat_ranking',
      councilModels: ['requesty:openai/gpt-4o-mini'],
      chairmanModel: undefined,
      documents: [{ name: 'a.txt', content: 'x' }],
    });
    expect(run.run_id).toBe('r1');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/conversations/c1/runs`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      content: 'Q?',
      search_provider: 'tavily',
      execution_mode: 'chat_ranking',
      council_models: ['requesty:openai/gpt-4o-mini'],
      documents: [{ name: 'a.txt', content: 'x' }],
    });
  });

  it('rejects a busy conversation with a 409 error carrying the detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Conversation already has an active run' }, 409)));
    const error = await createForkApi(BASE).startRun('c1', { content: 'Q?' }).catch((e) => e);
    expect(isRunConflictError(error)).toBe(true);
    expect(error.message).toBe('Conversation already has an active run');
  });

  it('keeps other failures distinct from a conflict', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('oops', { status: 500 })));
    const error = await createForkApi(BASE).startRun('c1', { content: 'Q?' }).catch((e) => e);
    expect(isRunConflictError(error)).toBe(false);
    expect(error.status).toBe(500);
    expect(error.message).toBe('Failed to start run');
  });
});

describe('streamRun', () => {
  it('replays from the given event index and resolves on a terminal event', async () => {
    const fetchMock = vi.fn(async () => streamResponse(
      ': ping\n\ndata: {"type":"stage1_start"}\n\ndata: {"type":"complete","metadata":{}}\n\n'
    ));
    vi.stubGlobal('fetch', fetchMock);
    const events = [];
    await createForkApi(BASE).streamRun('r1', (type) => events.push(type), undefined, 7);
    expect(fetchMock.mock.calls[0][0]).toMatch(new RegExp(`^${BASE}/api/runs/r1/stream\\?from_event=7&`));
    expect(events).toEqual(['stage1_start', 'complete']);
  });

  it('treats cancelled and error as terminal too', async () => {
    for (const type of ['cancelled', 'error']) {
      vi.stubGlobal('fetch', vi.fn(async () => streamResponse(`data: {"type":"${type}"}\n\n`)));
      await expect(createForkApi(BASE).streamRun('r1', () => {})).resolves.toBeUndefined();
    }
  });

  it('rejects when the stream ends before the run finished', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse('data: {"type":"stage1_start"}\n\n')));
    const error = await createForkApi(BASE).streamRun('r1', () => {}).catch((e) => e);
    expect(error.code).toBe('RUN_STREAM_DROPPED');
  });

  it('rejects an unknown run with its status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Run not found' }, 404)));
    const error = await createForkApi(BASE).streamRun('nope', () => {}).catch((e) => e);
    expect(error.status).toBe(404);
  });
});

describe('cancelRun / getActiveRun / Requesty', () => {
  it('posts to the cancel route', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ run_id: 'r1', status: 'running' }));
    vi.stubGlobal('fetch', fetchMock);
    await createForkApi(BASE).cancelRun('r1');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/runs/r1/cancel`);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
  });

  it('returns active_run null after a backend restart', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ active_run: null })));
    await expect(createForkApi(BASE).getActiveRun('c1')).resolves.toEqual({ active_run: null });
  });

  it('tests the saved Requesty key when no key is typed', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ success: true, message: 'API key is valid' }));
    vi.stubGlobal('fetch', fetchMock);
    await createForkApi(BASE).testRequestyKey('');
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/settings/test-requesty`);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ api_key: null });
  });

  it('offers Requesty models only with a key and the source switched on', () => {
    expect(isRequestySourceEnabled({ requesty_api_key_set: true, enabled_providers: { requesty: true } })).toBe(true);
    expect(isRequestySourceEnabled({ requesty_api_key_set: true, enabled_providers: { requesty: false } })).toBe(false);
    expect(isRequestySourceEnabled({ requesty_api_key_set: false, enabled_providers: { requesty: true } })).toBe(false);
    expect(isRequestySourceEnabled(null)).toBe(false);
    // One rule with Settings.jsx and the backend default: off unless saved as true.
    expect(isRequestySourceEnabled({ requesty_api_key_set: true, enabled_providers: {} })).toBe(false);
    expect(isRequestySourceEnabled({ requesty_api_key_set: true })).toBe(false);
  });
});

describe('loadRequestyModels', () => {
  it('does not call the backend without a saved key or with the source off', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ models: [{ id: 'requesty:x' }] }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(loadRequestyModels({ requesty_api_key_set: false, enabled_providers: { requesty: true } })).resolves.toEqual([]);
    await expect(loadRequestyModels({ requesty_api_key_set: true, enabled_providers: {} })).resolves.toEqual([]);
    await expect(loadRequestyModels(null)).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('loads the models with a saved key and the source on, and gives [] when the call fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ models: [{ id: 'requesty:x' }] })));
    const on = { requesty_api_key_set: true, enabled_providers: { requesty: true } };
    await expect(loadRequestyModels(on)).resolves.toEqual([{ id: 'requesty:x' }]);
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'x' }, 500)));
    await expect(loadRequestyModels(on)).resolves.toEqual([]);
  });
});

describe('parseSseDataChunk handler errors', () => {
  it('logs a throwing handler as a handler failure, not a parse error, and keeps reading', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const seen = [];
    const onEvent = (type) => {
      seen.push(type);
      if (type === 'boom') throw new Error('handler bug');
    };
    parseSseDataChunk('', 'data: {"type":"boom"}\ndata: {"type":"next"}\n', onEvent);
    expect(seen).toEqual(['boom', 'next']);
    expect(consoleSpy).toHaveBeenCalledOnce();
    expect(consoleSpy.mock.calls[0][0]).toBe('SSE event handler failed:');
    consoleSpy.mockRestore();
  });
});
