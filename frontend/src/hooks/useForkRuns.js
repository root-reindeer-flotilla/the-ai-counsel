/*
 * Fork: resumable council runs (backend/runs.py) in App.jsx.
 *
 * Kept out of App.jsx so upstream merges into that file stay small. App keeps
 * upstream's send flow and event handler; this hook supplies:
 *
 * - streamRun: the stream call for council turns (starts a run, then streams
 *   its events live from event 0);
 * - handleSendError: the fork's early return in upstream's catch (detach,
 *   409, a dropped stream);
 * - stop: Stop cancels the run on the server, whether this tab streams it or
 *   follows it through upstream's /progress polling;
 * - cancelBeforeDelete, resume (the Reconnect button), isCurrent (guards after
 *   awaits), restoreSelection (reopen this tab's conversation after a reload).
 *
 * A run this tab did not start (after a reload, or on switching back) is
 * followed through upstream's /progress polling, which carries the run's
 * `run_id` (owner decision 2026-09-25: poll to re-attach, no stream replay).
 * useRestoredInput is ChatInterface's side of a 409 (the question comes back).
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from '../api';
import {
  classifySendError,
  interruptedRunMessage,
  isSendOnScreen,
  markPolledTurnStopped,
  patchRunTurn,
  polledRunId,
  readStoredConversationId,
  runProgressState,
  RUN_CONFLICT_MESSAGE,
  writeStoredConversationId,
} from '../utils/councilRuns';

export { advisorConflictMessage } from '../utils/councilRuns';

const TERMINAL_RUN_STATUSES = new Set(['completed', 'cancelled', 'failed']);

const abortError = () => new DOMException('Aborted', 'AbortError');

const logCancelError = (error) => console.error('Failed to cancel run:', error);

/**
 * @param {object} deps
 * @param {string|null} deps.currentConversationId
 * @param {Function} deps.setCurrentConversation
 * @param {Function} deps.setCurrentConversationId
 * @param {Function} deps.setAppMode
 * @param {Function} deps.setIsLoading
 * @param {{current: number}} deps.conversationVersionRef
 * @param {object} deps.idleLoading App's IDLE_LOADING
 * @param {Function} deps.finalizeTimers App's finalizeTimers
 * @param {Function} deps.getConversationMode App's getConversationMode
 * @param {Function} deps.app returns App's { loadConversation, checkForActiveRun }
 *   (called lazily: they are defined after the hook call)
 */
export function useForkRuns({
  currentConversationId,
  setCurrentConversation,
  setCurrentConversationId,
  setAppMode,
  setIsLoading,
  conversationVersionRef,
  idleLoading,
  finalizeTimers,
  getConversationMode,
  app,
}) {
  const currentIdRef = useRef(currentConversationId);
  const liveRunRef = useRef(null); // { conversationId, runId, stopRequested, startedOnDraft }
  const selectionRestoredRef = useRef(false);
  const [restoredInput, setRestoredInput] = useState(null);

  useLayoutEffect(() => {
    currentIdRef.current = currentConversationId;
  }, [currentConversationId]);

  // Remember the open conversation (per tab) so a reload reopens it.
  useEffect(() => {
    if (currentConversationId === null && !selectionRestoredRef.current) return;
    writeStoredConversationId(currentConversationId);
  }, [currentConversationId]);

  /** True while `conversationId` is the conversation on screen. */
  const isCurrent = (conversationId) => currentIdRef.current === conversationId;

  /** After the first conversation list load, reopen the conversation this tab had open. */
  const restoreSelection = (conversations) => {
    if (selectionRestoredRef.current) return;
    selectionRestoredRef.current = true;
    const storedId = readStoredConversationId();
    const stored = storedId ? conversations.find((c) => c.id === storedId) : null;
    if (!stored) return;
    setCurrentConversationId((current) => current ?? stored.id);
    setAppMode((current) => current ?? getConversationMode(stored));
  };

  /**
   * Stream method for council turns, with api.sendMessageStream's signature:
   * start a background run, then stream its events live.
   */
  const streamRun = async (conversationId, options, onEvent, signal) => {
    // Stop pressed while a draft conversation was being created.
    if (signal?.aborted) throw abortError();
    // A first send from the draft: the screen may still show 'draft' for it.
    const startedOnDraft = currentIdRef.current === 'draft';
    const live = { conversationId, runId: null, stopRequested: false, startedOnDraft };
    liveRunRef.current = live;
    let runStarted = false;
    let serverCancelled = false;
    try {
      const run = await api.startRun(conversationId, options);
      runStarted = true;
      live.runId = run.run_id;
      if (signal?.aborted) {
        // Stop (or leaving the conversation) while the run was being created.
        if (live.stopRequested) api.cancelRun(run.run_id).catch(logCancelError);
        throw abortError();
      }
      await api.streamRun(run.run_id, (eventType, event) => {
        // Stopped on the server (Stop in another tab, deleted, or a shutdown).
        if (eventType === 'cancelled') {
          serverCancelled = true;
          return;
        }
        onEvent(eventType, event);
      }, signal, 0);
      if (serverCancelled) throw abortError();
    } catch (error) {
      error.forkRun = { runStarted, stopRequested: live.stopRequested, serverCancelled, startedOnDraft };
      throw error;
    } finally {
      if (liveRunRef.current === live) liveRunRef.current = null;
    }
  };

  /**
   * The fork's early return in upstream's catch. Returns true when the error
   * is handled here; false leaves it to upstream (Stop, or nothing was sent).
   */
  const handleSendError = (error, { conversationId, content }) => {
    const forkRun = error?.forkRun;
    const outcome = classifySendError({
      error,
      ...(forkRun || {}),
      isCurrent: isSendOnScreen({
        conversationId,
        currentId: currentIdRef.current,
        // Debate sends (no forkRun) keep the draft rule they had.
        startedOnDraft: forkRun ? forkRun.startedOnDraft : true,
      }),
    });
    if (outcome === 'detach') return true;
    if (outcome === 'conflict') {
      markResumable(conversationId, RUN_CONFLICT_MESSAGE);
      setRestoredInput({ text: content });
      return true;
    }
    if (outcome === 'interrupted') {
      console.error('Lost the council run stream:', error);
      markResumable(conversationId, interruptedRunMessage(error));
      return true;
    }
    return false;
  };

  /** End the in-flight turn with `message` and a Reconnect button. */
  const markResumable = (conversationId, message) => {
    setCurrentConversation((prev) => patchRunTurn(prev, conversationId, (msg) => ({
      ...msg,
      error: message,
      resumable: true,
      loading: idleLoading,
      timers: finalizeTimers(msg.timers),
    })));
    setIsLoading(false);
  };

  /** Stop: cancel this conversation's council run on the server. */
  const stop = (conversationId, conversation) => {
    const live = liveRunRef.current;
    if (live && isSendOnScreen({ conversationId: live.conversationId, currentId: conversationId, startedOnDraft: live.startedOnDraft })) {
      // Upstream then aborts the stream; handleSendError sees stopRequested.
      live.stopRequested = true;
      if (live.runId) api.cancelRun(live.runId).catch(logCancelError);
      return;
    }
    // A run re-attached through /progress polling (after a reload or reopening).
    const runId = polledRunId(conversation, conversationId);
    if (!runId) return;
    setCurrentConversation((prev) => patchRunTurn(prev, conversationId, (msg) => markPolledTurnStopped(msg, idleLoading)));
    setIsLoading(false);
    api.cancelRun(runId).catch(logCancelError);
  };

  /** Before deleting a conversation, stop its run (even one this tab is not following). */
  const cancelBeforeDelete = async (conversationId) => {
    try {
      const { active_run: run } = await api.getActiveRun(conversationId);
      if (run?.run_id && !TERMINAL_RUN_STATUSES.has(run.status)) {
        await api.cancelRun(run.run_id);
      }
    } catch (error) {
      console.warn('Could not stop the run of the deleted conversation:', error);
    }
  };

  /** "Reconnect": reload the open conversation and follow its run, if one is live. */
  const resume = async () => {
    const conversationId = currentIdRef.current;
    if (!conversationId || conversationId === 'draft') return;
    const { loadConversation, checkForActiveRun } = app();
    await loadConversation(conversationId, conversationVersionRef.current);
    if (!isCurrent(conversationId)) return;
    const progress = await api.getConversationProgress(conversationId).catch(() => null);
    if (!isCurrent(conversationId)) return;
    if (!runProgressState(progress).loading) {
      setIsLoading(false);
      return;
    }
    await checkForActiveRun(conversationId);
  };

  /** True while this tab streams a run of `conversationId`. */
  const hasLiveRun = (conversationId) => liveRunRef.current?.conversationId === conversationId;

  return {
    streamRun,
    handleSendError,
    stop,
    cancelBeforeDelete,
    resume,
    isCurrent,
    hasLiveRun,
    restoreSelection,
    restoredInput,
  };
}

/**
 * Put `restoredInput.text` into a message box's state whenever a new
 * `restoredInput` object arrives (a 409 hands the unsent question back).
 */
export function useRestoredInput(restoredInput, setInput) {
  const [seen, setSeen] = useState(restoredInput);
  if (restoredInput !== seen) {
    // Adjusting state while rendering, as React recommends over an effect.
    setSeen(restoredInput);
    if (restoredInput) setInput(restoredInput.text);
  }
}
