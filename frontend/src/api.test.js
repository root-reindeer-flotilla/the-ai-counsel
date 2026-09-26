import { describe, it, expect, vi } from 'vitest';

import { parseSseDataChunk } from './api';


describe('parseSseDataChunk', () => {
  it('parses an event split across chunks', () => {
    const onEvent = vi.fn();

    let buffer = parseSseDataChunk('', 'data: {"type":"stage1","value":', onEvent);
    expect(onEvent).not.toHaveBeenCalled();

    buffer = parseSseDataChunk(buffer, '1}\n', onEvent);
    expect(buffer).toBe('');
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith('stage1', { type: 'stage1', value: 1 });
  });

  it('parses multiple data events in one chunk and ignores non-data lines', () => {
    const onEvent = vi.fn();

    const chunk = [
      'event: message',
      'data: {"type":"stage1","a":1}',
      '',
      'data: {"type":"stage2","b":2}',
      '',
    ].join('\n');
    const buffer = parseSseDataChunk('', chunk, onEvent);

    expect(buffer).toBe('');
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent).toHaveBeenNthCalledWith(1, 'stage1', { type: 'stage1', a: 1 });
    expect(onEvent).toHaveBeenNthCalledWith(2, 'stage2', { type: 'stage2', b: 2 });
  });

  it('skips malformed json lines without throwing', () => {
    const onEvent = vi.fn();
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const buffer = parseSseDataChunk(
      '',
      'data: {"type":"ok"}\ndata: {not-json}\ndata: {"type":"done"}\n',
      onEvent
    );

    expect(buffer).toBe('');
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent).toHaveBeenNthCalledWith(1, 'ok', { type: 'ok' });
    expect(onEvent).toHaveBeenNthCalledWith(2, 'done', { type: 'done' });
    expect(consoleSpy).toHaveBeenCalledOnce();

    consoleSpy.mockRestore();
  });
});
