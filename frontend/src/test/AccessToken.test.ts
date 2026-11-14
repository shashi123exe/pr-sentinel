import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACCESS_TOKEN_STORAGE_KEY,
  ApiClientError,
  apiClient,
  clearAccessToken,
  getAccessToken,
  isDeviceRemembered,
  REMEMBER_DEVICE_STORAGE_KEY,
  setAccessToken,
} from '../api/client';

describe('Shared Access Token Session & Persistent Storage Management', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  describe('Session-only persistence (rememberDevice = false)', () => {
    it('stores token strictly in sessionStorage when rememberDevice is false or omitted', () => {
      expect(getAccessToken()).toBeNull();
      expect(isDeviceRemembered()).toBe(false);

      setAccessToken('session-only-token', false);

      expect(getAccessToken()).toBe('session-only-token');
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('session-only-token');

      // Never written to localStorage
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
      expect(localStorage.getItem(REMEMBER_DEVICE_STORAGE_KEY)).toBeNull();
      expect(isDeviceRemembered()).toBe(false);
    });

    it('removes any previous localStorage persistence if saved with rememberDevice = false', () => {
      // First save with rememberDevice = true
      setAccessToken('token-first', true);
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('token-first');
      expect(isDeviceRemembered()).toBe(true);

      // Overwrite with rememberDevice = false
      setAccessToken('token-second', false);
      expect(getAccessToken()).toBe('token-second');
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('token-second');

      // Persistent storage cleared
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
      expect(localStorage.getItem(REMEMBER_DEVICE_STORAGE_KEY)).toBeNull();
      expect(isDeviceRemembered()).toBe(false);
    });
  });

  describe('Remember-device persistence (rememberDevice = true)', () => {
    it('persists token to both sessionStorage and localStorage when rememberDevice is true', () => {
      setAccessToken('persistent-device-token', true);

      expect(getAccessToken()).toBe('persistent-device-token');
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('persistent-device-token');
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('persistent-device-token');
      expect(localStorage.getItem(REMEMBER_DEVICE_STORAGE_KEY)).toBe('true');
      expect(isDeviceRemembered()).toBe(true);
    });
  });

  describe('Application reload and token restoration', () => {
    it('restores token from localStorage and hydrates sessionStorage when device was remembered', () => {
      // Setup persistent state as if saved in an earlier browser session
      localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, 'restored-token-999');
      localStorage.setItem(REMEMBER_DEVICE_STORAGE_KEY, 'true');

      // Verify sessionStorage is currently empty (simulating browser restart / new tab)
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();

      // Application startup call to getAccessToken()
      const restored = getAccessToken();
      expect(restored).toBe('restored-token-999');

      // Verify sessionStorage was hydrated for subsequent fast lookups
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('restored-token-999');
    });

    it('does NOT restore from localStorage if rememberDevice flag is absent or not "true"', () => {
      // LocalStorage has leftover token but remember flag is not true
      localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, 'abandoned-token');
      localStorage.removeItem(REMEMBER_DEVICE_STORAGE_KEY);
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();

      expect(getAccessToken()).toBeNull();
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
    });
  });

  describe('Internal navigation and session continuity', () => {
    it('preserves authentication across multiple requests and consecutive calls without re-prompting', () => {
      setAccessToken('continuous-token-abc', false);

      // Simulate multiple views/components accessing the token during client-side navigation
      for (let i = 0; i < 5; i++) {
        expect(getAccessToken()).toBe('continuous-token-abc');
      }
    });
  });

  describe('Logout & token clearing', () => {
    it('clears token from both sessionStorage and localStorage on clearAccessToken()', () => {
      setAccessToken('full-auth-token', true);
      expect(getAccessToken()).toBe('full-auth-token');
      expect(isDeviceRemembered()).toBe(true);

      clearAccessToken();

      expect(getAccessToken()).toBeNull();
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
      expect(localStorage.getItem(REMEMBER_DEVICE_STORAGE_KEY)).toBeNull();
      expect(isDeviceRemembered()).toBe(false);
    });
  });

  describe('Invalid token handling on HTTP 401', () => {
    it('purges both storages and dispatches unauthorized event when backend responds with 401', async () => {
      setAccessToken('invalid-expired-token', true);
      expect(isDeviceRemembered()).toBe(true);

      const unauthorizedListener = vi.fn();
      window.addEventListener('pr-sentinel:unauthorized', unauthorizedListener);

      const mockFetch = vi.fn().mockResolvedValue({
        status: 401,
        statusText: 'Unauthorized',
        ok: false,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          error: { code: 'unauthorized', message: 'Valid access token required.' },
        }),
      });
      vi.stubGlobal('fetch', mockFetch);

      await expect(apiClient.getHealth()).rejects.toThrow(ApiClientError);

      // Verify token was purged from BOTH sessionStorage and localStorage
      expect(getAccessToken()).toBeNull();
      expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
      expect(localStorage.getItem(REMEMBER_DEVICE_STORAGE_KEY)).toBeNull();
      expect(isDeviceRemembered()).toBe(false);

      // Verify unauthorized event fired to trigger modal
      expect(unauthorizedListener).toHaveBeenCalledTimes(1);

      window.removeEventListener('pr-sentinel:unauthorized', unauthorizedListener);
    });
  });

  describe('Header injection and whitespace trimming', () => {
    it('trims whitespace and ignores empty string tokens', () => {
      setAccessToken('   trimmed-token   ', true);
      expect(getAccessToken()).toBe('trimmed-token');
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('trimmed-token');

      setAccessToken('   ', true);
      expect(getAccessToken()).toBeNull();
      expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
    });

    it('injects X-Access-Token header into requests when token is configured', async () => {
      setAccessToken('bearer-xyz-123', false);

      const mockFetch = vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({ status: 'ok', database: 'ok' }),
      });
      vi.stubGlobal('fetch', mockFetch);

      await apiClient.getHealth();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [, options] = mockFetch.mock.calls[0];
      const headers = options?.headers as Headers;
      expect(headers.get('X-Access-Token')).toBe('bearer-xyz-123');
    });

    it('omits X-Access-Token header when token is null', async () => {
      clearAccessToken();

      const mockFetch = vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({ status: 'ok', database: 'ok' }),
      });
      vi.stubGlobal('fetch', mockFetch);

      await apiClient.getHealth();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [, options] = mockFetch.mock.calls[0];
      const headers = options?.headers as Headers;
      expect(headers.get('X-Access-Token')).toBeNull();
    });
  });
});
