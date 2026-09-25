/*
 * Fork: helpers for resumable council runs (backend/runs.py).
 *
 * A council turn runs on the server as a background run. The UI streams its
 * events; Stop cancels the run, while switching conversations or closing the
 * tab only detaches, and the run is re-attached (replaying from an event
 * index) when the conversation is opened again.
 */

export const RUN_CONFLICT_MESSAGE =
  'Not sent: a council run is already in progress in this conversation (in another tab or from an MCP client). '
  + 'Your question is kept above. Reconnect to follow the running council, then send again.';

export const RUN_CONFLICT_ADVISOR_MESSAGE =
  'Not started: a council run is already in progress in this conversation. Wait for it to finish, then try again.';

export const RUN_STREAM_LOST_MESSAGE =
  'Lost the connection to this council run. It keeps running on the server: reconnect (or reload the page) to resume it.';

/**
 * Drop empty values from a run snapshot's metadata, so merging it never
 * overwrites streamed state with null, false, '' or an empty list/object.
 */
export function compactRunMetadata(metadata) {
  const result = {};
  Object.entries(metadata || {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '' || value === false) return;
    if (Array.isArray(value) && value.length === 0) return;
    if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 0) return;
    result[key] = value;
  });
  return result;
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
