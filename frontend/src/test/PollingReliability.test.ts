import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePolling } from '../hooks/usePolling';

describe('usePolling Reliability and AbortController', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('passes an AbortSignal to callback and aborts on unmount', async () => {
    let capturedSignal: AbortSignal | undefined;

    const callback = vi.fn().mockImplementation(async (signal: AbortSignal) => {
      capturedSignal = signal;
      return true;
    });

    const { unmount } = renderHook(() => usePolling(callback, 1000, true));

    expect(callback).toHaveBeenCalledTimes(1);
    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);

    // Unmount hook
    unmount();

    // Signal should now be aborted
    expect(capturedSignal?.aborted).toBe(true);

    // Further timers should not trigger callback
    await vi.advanceTimersByTimeAsync(3000);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it('handles AbortError quietly without throwing unhandled exceptions', async () => {
    const abortError = new Error('The user aborted a request.');
    abortError.name = 'AbortError';

    const callback = vi.fn().mockRejectedValue(abortError);

    const { unmount } = renderHook(() => usePolling(callback, 1000, true));

    expect(callback).toHaveBeenCalledTimes(1);
    unmount();

    // Ensure no unhandled rejection occurs
    await vi.advanceTimersByTimeAsync(2000);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it('aborts previous controller and re-creates when enabled toggles off then on', async () => {
    let signal1: AbortSignal | undefined;
    let signal2: AbortSignal | undefined;
    let callIdx = 0;

    const callback = vi.fn().mockImplementation(async (signal: AbortSignal) => {
      callIdx++;
      if (callIdx === 1) signal1 = signal;
      else signal2 = signal;
      return true;
    });

    const { rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => usePolling(callback, 1000, enabled),
      { initialProps: { enabled: true } }
    );

    expect(signal1?.aborted).toBe(false);

    // Disable polling
    rerender({ enabled: false });
    expect(signal1?.aborted).toBe(true);

    // Re-enable polling
    rerender({ enabled: true });
    expect(signal2).toBeDefined();
    expect(signal2?.aborted).toBe(false);
  });
});
