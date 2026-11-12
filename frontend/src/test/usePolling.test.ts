import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePolling } from '../hooks/usePolling';

describe('usePolling Hook', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('invokes callback immediately on mount', async () => {
    const callback = vi.fn().mockResolvedValue(true);
    renderHook(() => usePolling(callback, 2000, true));

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it('polls at designated interval while callback returns true', async () => {
    const callback = vi.fn().mockResolvedValue(true);
    renderHook(() => usePolling(callback, 2000, true));

    expect(callback).toHaveBeenCalledTimes(1);

    // Fast-forward 2000ms
    await vi.advanceTimersByTimeAsync(2000);
    expect(callback).toHaveBeenCalledTimes(2);

    // Fast-forward another 2000ms
    await vi.advanceTimersByTimeAsync(2000);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it('stops polling when callback resolves to false', async () => {
    let callCount = 0;
    const callback = vi.fn().mockImplementation(async () => {
      callCount++;
      // Return false on 2nd call to signal terminal review state (completed/failed)
      return callCount < 2;
    });

    renderHook(() => usePolling(callback, 1000, true));

    expect(callback).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(callback).toHaveBeenCalledTimes(2);

    // Subsequent timers should NOT trigger further calls
    await vi.advanceTimersByTimeAsync(3000);
    expect(callback).toHaveBeenCalledTimes(2);
  });

  it('cleans up timer on unmount', async () => {
    const callback = vi.fn().mockResolvedValue(true);
    const { unmount } = renderHook(() => usePolling(callback, 1000, true));

    expect(callback).toHaveBeenCalledTimes(1);

    unmount();

    await vi.advanceTimersByTimeAsync(3000);
    expect(callback).toHaveBeenCalledTimes(1);
  });
});
