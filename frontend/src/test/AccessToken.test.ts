import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACCESS_TOKEN_STORAGE_KEY,
  ApiClientError,
  apiClient,
  clearAccessToken,
  getAccessToken,
  setAccessToken,
} from '../api/client';

describe('Shared Access Token Session Management', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('stores and retrieves access token strictly in sessionStorage', () => {
    expect(getAccessToken()).toBeNull();

    setAccessToken('sentinel-secret-token');
    expect(getAccessToken()).toBe('sentinel-secret-token');
    expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('sentinel-secret-token');

    // Strict storage isolation: never write to localStorage
    expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.length).toBe(0);

    clearAccessToken();
    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
  });

  it('trims whitespace and ignores empty string tokens', () => {
    setAccessToken('   trimmed-token   ');
    expect(getAccessToken()).toBe('trimmed-token');

    setAccessToken('   ');
    expect(getAccessToken()).toBeNull();
  });

  it('injects X-Access-Token header into request when token is set', async () => {
    setAccessToken('bearer-xyz-123');

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

  it('omits X-Access-Token header when token is not configured', async () => {
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

  it('clears session token and dispatches unauthorized event on HTTP 401', async () => {
    setAccessToken('invalid-expired-token');

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

    // Verify token was purged from sessionStorage
    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();

    // Verify unauthorized event fired
    expect(unauthorizedListener).toHaveBeenCalledTimes(1);

    window.removeEventListener('pr-sentinel:unauthorized', unauthorizedListener);
  });
});
