import { useEffect, useRef } from 'react';

/**
 * Custom hook to poll an async callback at a set interval.
 * Returns early or stops if the callback returns false.
 * Supports AbortController to cancel pending in-flight requests on unmount or disable.
 *
 * @param callback Async function to execute. Takes AbortSignal. Return false to cancel further polling.
 * @param intervalMs Polling frequency in milliseconds (default 2000).
 * @param enabled Whether polling is active.
 */
export function usePolling(
  callback: (signal: AbortSignal) => Promise<boolean | void>,
  intervalMs = 2000,
  enabled = true
): void {
  const savedCallback = useRef(callback);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;

    const abortController = new AbortController();
    const state = {
      cancelled: false,
      timerId: null as ReturnType<typeof setTimeout> | null,
    };

    const execute = async () => {
      if (state.cancelled || abortController.signal.aborted) return;

      try {
        const shouldContinue = await savedCallback.current(abortController.signal);
        if (shouldContinue === false || state.cancelled || abortController.signal.aborted) {
          return;
        }
      } catch (err) {
        // If aborted, stop quietly
        if (abortController.signal.aborted || (err instanceof Error && err.name === 'AbortError')) {
          return;
        }
        // Recoverable network failure: continue polling unless stopped
      }

      if (!state.cancelled && !abortController.signal.aborted) {
        state.timerId = setTimeout(execute, intervalMs);
      }
    };

    // Execute immediately on mount or when enabled becomes true
    void execute();

    return () => {
      state.cancelled = true;
      abortController.abort();
      if (state.timerId !== null) {
        clearTimeout(state.timerId);
      }
    };
  }, [enabled, intervalMs]);
}
