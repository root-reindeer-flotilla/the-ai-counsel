/*
 * Fork: helpers for resumable council runs (backend/runs.py).
 *
 * A council turn runs on the server as a background run. A send from this tab
 * streams the run's events live. Stop cancels the run; switching conversations
 * or closing the tab only detaches, and the run keeps going. Opening the
 * conversation again (or reloading) follows the run through upstream's
 * /progress polling, which reports the run's `run_id` so Stop can cancel it.
 */

import { isRunConflictError } from '../forkApi';

export const RUN_CONFLICT_MESSAGE =
  'Not sent: a council is still running or finishing in this conversation. '
  + 'Your question is back in the message box. Reconnect to follow the council, then send it again when it has finished.';

export const RUN_CONFLICT_ADVISOR_MESSAGE =
  'Not started: a council is still running or finishing in this conversation. Wait for it to finish, then try again.';

/** The advisor debate's error for a busy conversation (409), or null. */
export const advisorConflictMessage = (error) => (isRunConflictError(error) ? RUN_CONFLICT_ADVISOR_MESSAGE : null);

export const RUN_STREAM_LOST_MESSAGE =
  'Lost the connection to this council run. It may still be running on the server: Reconnect to check and follow it.';

export const RUN_GONE_MESSAGE =
  'This council run is no longer on the server (the backend may have restarted). '
  + 'Partial results are kept above. Reconnect to reload what was saved.';

/**
 * What to do when a council send fails or its stream ends early.
 *
 * - `detach`: the user left the conversation (or the tab) before the send
 *   ended; the run goes on, and nothing is marked on screen, because upstream's
 *   catch would patch or trim whichever conversation is shown now.
 * - `stopped`: Stop was pressed (or the run was cancelled on the server);
 *   upstream's abort handling marks the turn as stopped.
 * - `conflict`: the backend answered 409, the conversation is busy.
 * - `interrupted`: a run exists, but its stream failed; keep the partial turn
 *   and offer to reconnect.
 * - `remove-optimistic`: nothing reached the server; upstream removes the
 *   optimistic turn.
 */
export function classifySendError({
  error,
  runStarted = false,
  stopRequested = false,
  serverCancelled = false,
  isCurrent = true,
}) {
  // Never mark anything on a conversation that is no longer shown.
  if (!isCurrent) return 'detach';
  if (error?.name === 'AbortError') {
    if (stopRequested || serverCancelled) return 'stopped';
    return runStarted ? 'detach' : 'stopped';
  }
  if (isRunConflictError(error)) return 'conflict';
  if (runStarted) return 'interrupted';
  return 'remove-optimistic';
}

/** The message for an `interrupted` send: a 404 means the run is gone. */
export function interruptedRunMessage(error) {
  return error?.status === 404 ? RUN_GONE_MESSAGE : RUN_STREAM_LOST_MESSAGE;
}

/**
 * Read a /progress answer. `{active: false}` (no run, e.g. after a backend
 * restart) means the conversation is not loading.
 */
export function runProgressState(progress) {
  const active = !!progress?.active;
  return {
    loading: active,
    runId: active && progress.mode !== 'advisors' ? (progress.run_id || null) : null,
  };
}

/** True when `conversation` is `conversationId` and ends with an assistant turn. */
export function isRunTurn(conversation, conversationId) {
  if (!conversation || conversation.id !== conversationId) return false;
  const messages = conversation.messages || [];
  return messages.length > 0 && messages[messages.length - 1]?.role === 'assistant';
}

/**
 * Apply `patch` to the conversation's last message when it is the in-flight
 * assistant turn of `conversationId`; otherwise return the conversation as is.
 */
export function patchRunTurn(conversation, conversationId, patch) {
  if (!isRunTurn(conversation, conversationId)) return conversation;
  const messages = [...conversation.messages];
  messages[messages.length - 1] = patch(messages[messages.length - 1]);
  return { ...conversation, messages };
}

/** The run id of a turn re-attached through /progress polling, if it is still live. */
export function polledRunId(conversation, conversationId) {
  if (!isRunTurn(conversation, conversationId)) return null;
  const last = conversation.messages[conversation.messages.length - 1];
  return last.externalRun && last.runId ? last.runId : null;
}

// The open conversation is remembered per tab, so a reload reopens it and
// re-attaches its run. Storage can be unavailable (private mode): then a
// reload just shows the start page, as upstream does.
const CURRENT_CONVERSATION_KEY = 'ai-counsel:current-conversation';

export function readStoredConversationId() {
  try {
    return sessionStorage.getItem(CURRENT_CONVERSATION_KEY);
  } catch {
    return null;
  }
}

export function writeStoredConversationId(id) {
  try {
    if (id && id !== 'draft') {
      sessionStorage.setItem(CURRENT_CONVERSATION_KEY, id);
    } else {
      sessionStorage.removeItem(CURRENT_CONVERSATION_KEY);
    }
  } catch {
    // Storage unavailable: nothing to remember.
  }
}
