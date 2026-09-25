/**
 * Fork-only API calls: Requesty and resumable council runs (backend/runs.py).
 *
 * Kept out of api.js so upstream merges into that file stay small; api.js
 * spreads createForkApi(API_BASE) into its `api` object and re-exports
 * parseSseDataChunk.
 */

/** Events that end a run's stream. */
export const RUN_TERMINAL_EVENTS = new Set(['complete', 'cancelled', 'error']);

/**
 * Parse Server-Sent Events text incrementally, one `data:` line at a time.
 * Returns the unparsed remainder to pass back in with the next chunk.
 * Comment lines (`: ping`) and other non-data lines are ignored. Malformed
 * JSON and exceptions thrown by `onEvent` are logged separately.
 */
export const parseSseDataChunk = (buffer, chunk, onEvent) => {
  const text = `${buffer}${chunk}`;
  const lines = text.split('\n');
  const nextBuffer = lines.pop() || '';

  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;

    let event;
    try {
      event = JSON.parse(line.slice(6));
    } catch (e) {
      console.error('Failed to parse SSE event:', e);
      continue;
    }
    // A handler bug is not a parse error; like upstream, keep reading the stream.
    try {
      onEvent(event.type, event);
    } catch (e) {
      console.error('SSE event handler failed:', e);
    }
  }

  return nextBuffer;
};

/**
 * Build an Error from a failed response. It carries `status` and the
 * backend's `detail` so callers can tell a 409 (conversation busy) apart.
 */
export async function responseError(response, fallbackMessage) {
  let detail = null;
  try {
    const body = await response.json();
    detail = typeof body?.detail === 'string' ? body.detail : null;
  } catch {
    // Not JSON: keep the fallback message.
  }
  const error = new Error(detail || fallbackMessage);
  error.status = response.status;
  error.detail = detail;
  return error;
}

/** True when an error is the backend's "conversation already has an active run" 409. */
export const isRunConflictError = (error) => error?.status === 409;

export function createForkApi(apiBase) {
  return {
    /**
     * Get available models from Requesty (ids are prefixed `requesty:`).
     */
    async getRequestyModels() {
      const response = await fetch(`${apiBase}/api/models/requesty`);
      if (!response.ok) {
        throw new Error('Failed to get Requesty models');
      }
      return response.json();
    },

    /**
     * Test a Requesty API key; with no key the backend tests the saved one.
     */
    async testRequestyKey(apiKey) {
      const response = await fetch(`${apiBase}/api/settings/test-requesty`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey || null }),
      });
      if (!response.ok) {
        throw new Error('Failed to test API key');
      }
      return response.json();
    },

    /**
     * Start a resumable council run. Takes the same options as
     * api.sendMessageStream and returns the run snapshot ({run_id, ...}).
     * Throws an Error with `status` 409 while the conversation is busy.
     */
    async startRun(conversationId, options) {
      const {
        content,
        searchProvider = null,
        executionMode = 'full',
        councilModels = null,
        chairmanModel = null,
        documents = null,
      } = options;
      const body = {
        content,
        search_provider: searchProvider,
        execution_mode: executionMode,
      };
      if (councilModels && councilModels.length > 0) {
        body.council_models = councilModels;
      }
      if (chairmanModel) {
        body.chairman_model = chairmanModel;
      }
      if (documents && documents.length > 0) {
        body.documents = documents;
      }
      const response = await fetch(`${apiBase}/api/conversations/${conversationId}/runs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-cache',
        },
        body: JSON.stringify(body),
        cache: 'no-store',
      });
      if (!response.ok) {
        throw await responseError(response, 'Failed to start run');
      }
      return response.json();
    },

    /**
     * Get the live run of a conversation: {active_run: snapshot | null}.
     */
    async getActiveRun(conversationId) {
      const response = await fetch(`${apiBase}/api/conversations/${conversationId}/runs/active`);
      if (!response.ok) {
        throw await responseError(response, 'Failed to get active run');
      }
      return response.json();
    },

    /**
     * Get a run snapshot.
     */
    async getRun(runId) {
      const response = await fetch(`${apiBase}/api/runs/${runId}`);
      if (!response.ok) {
        throw await responseError(response, 'Failed to get run');
      }
      return response.json();
    },

    /**
     * Stream a run's events, replaying from event index `fromEvent`.
     * Resolves once a terminal event (complete, cancelled, error) arrived;
     * rejects if the stream ends before one did (e.g. the backend went away).
     */
    async streamRun(runId, onEvent, signal, fromEvent = 0) {
      const response = await fetch(
        `${apiBase}/api/runs/${runId}/stream?from_event=${encodeURIComponent(fromEvent)}&_t=${Date.now()}`,
        {
          method: 'GET',
          headers: { 'Cache-Control': 'no-cache' },
          signal,
          cache: 'no-store',
        }
      );
      if (!response.ok) {
        throw await responseError(response, 'Failed to stream run');
      }

      let finished = false;
      const handleEvent = (eventType, event) => {
        if (RUN_TERMINAL_EVENTS.has(eventType)) finished = true;
        onEvent(eventType, event);
      };

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = '';
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          sseBuffer = parseSseDataChunk(sseBuffer, decoder.decode(value, { stream: true }), handleEvent);
        }
        const tail = decoder.decode();
        if (tail || sseBuffer) {
          parseSseDataChunk('', `${sseBuffer}${tail}\n`, handleEvent);
        }
      } finally {
        reader.releaseLock();
      }

      if (!finished) {
        const error = new Error('The run stream ended before the run finished');
        error.code = 'RUN_STREAM_DROPPED';
        throw error;
      }
    },

    /**
     * Stop a run: {run_id, status}.
     */
    async cancelRun(runId) {
      const response = await fetch(`${apiBase}/api/runs/${runId}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!response.ok) {
        throw await responseError(response, 'Failed to cancel run');
      }
      return response.json();
    },
  };
}
