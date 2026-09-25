/*
 * Fork: "Reconnect" on a council turn whose run lost its stream or could not
 * start (resumable runs). Reconnecting reloads the conversation, so this
 * button goes away; keyboard focus then moves to the Stop button (a live run)
 * or the message box instead of dropping to the page.
 */
const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));

export default function ReconnectRunButton({ onReconnect }) {
  const handleClick = async () => {
    await onReconnect();
    // Let React commit the reloaded conversation first.
    await nextFrame();
    await nextFrame();
    const active = document.activeElement;
    if (active && active !== document.body && document.contains(active)) return;
    const target = document.querySelector('.stop-button')
      || document.querySelector('.message-input:not(:disabled)');
    target?.focus();
  };

  return (
    <button type="button" className="council-error-action" onClick={handleClick}>
      Reconnect
    </button>
  );
}
