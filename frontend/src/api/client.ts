/**
 * Typed API Client for PR Sentinel backend endpoints.
 * Communicates via relative `/api` path.
 */

import type {
  ApiErrorResponse,
  FindingOut,
  FindingStatusUpdate,
  HealthResponse,
  ListFilesParams,
  ListFindingsParams,
  ListReviewsParams,
  Page,
  ReviewCreate,
  ReviewCreateResponse,
  ReviewFileOut,
  ReviewListItem,
  ReviewOut,
  SettingsOut,
  SettingsUpdate,
} from '../types/api';

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter?: number;

  constructor(status: number, code: string, message: string, retryAfter?: number) {
    super(message);
    this.name = 'ApiClientError';
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

const BASE_URL = '/api';
export const ACCESS_TOKEN_STORAGE_KEY = 'pr_sentinel_access_token';
export const REMEMBER_DEVICE_STORAGE_KEY = 'pr_sentinel_remember_device';

/**
 * Check whether persistent "Remember this device" storage is enabled.
 */
export function isDeviceRemembered(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage?.getItem(REMEMBER_DEVICE_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

/**
 * Retrieve the shared access token.
 * Priority:
 * 1. Check tab-scoped sessionStorage first.
 * 2. If absent and "Remember this device" was enabled, restore from persistent localStorage
 *    and hydrate sessionStorage for active session.
 */
export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;

  // 1. Check tab-scoped sessionStorage
  try {
    const sessionToken = window.sessionStorage?.getItem(ACCESS_TOKEN_STORAGE_KEY);
    if (sessionToken && sessionToken.trim()) {
      return sessionToken.trim();
    }
  } catch {
    // sessionStorage may be restricted
  }

  // 2. Fallback to localStorage if device was remembered
  try {
    const isRemembered = window.localStorage?.getItem(REMEMBER_DEVICE_STORAGE_KEY) === 'true';
    if (isRemembered) {
      const localToken = window.localStorage?.getItem(ACCESS_TOKEN_STORAGE_KEY);
      if (localToken && localToken.trim()) {
        const trimmed = localToken.trim();
        try {
          window.sessionStorage?.setItem(ACCESS_TOKEN_STORAGE_KEY, trimmed);
        } catch {
          // sessionStorage may be restricted
        }
        return trimmed;
      }
    }
  } catch {
    // localStorage may be restricted
  }

  return null;
}

/**
 * Store the shared access token.
 * Always stores in sessionStorage for the active session.
 * If rememberDevice is true, also persists to localStorage so it survives browser restarts.
 * If rememberDevice is false, removes any stored token in localStorage.
 */
export function setAccessToken(token: string | null, rememberDevice: boolean = false): void {
  if (typeof window === 'undefined') return;

  const trimmed = token ? token.trim() : '';

  if (trimmed) {
    try {
      window.sessionStorage?.setItem(ACCESS_TOKEN_STORAGE_KEY, trimmed);
    } catch {
      // Storage access may be restricted
    }

    if (rememberDevice) {
      try {
        window.localStorage?.setItem(ACCESS_TOKEN_STORAGE_KEY, trimmed);
        window.localStorage?.setItem(REMEMBER_DEVICE_STORAGE_KEY, 'true');
      } catch {
        // Storage access may be restricted
      }
    } else {
      try {
        window.localStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
        window.localStorage?.removeItem(REMEMBER_DEVICE_STORAGE_KEY);
      } catch {
        // Storage access may be restricted
      }
    }
  } else {
    clearAccessToken();
  }
}

/**
 * Remove stored access token from both sessionStorage and localStorage.
 */
export function clearAccessToken(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  } catch {}
  try {
    window.localStorage?.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    window.localStorage?.removeItem(REMEMBER_DEVICE_STORAGE_KEY);
  } catch {}
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = `${BASE_URL}${endpoint}`;
  const headers = new Headers(options.headers || {});

  // Inject access token from sessionStorage via X-Access-Token header
  const token = getAccessToken();
  if (token && !headers.has('X-Access-Token')) {
    headers.set('X-Access-Token', token);
  }

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }

  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (response.status === 204) {
    return undefined as unknown as T;
  }

  if (!response.ok) {
    if (response.status === 401) {
      clearAccessToken();
      if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
        window.dispatchEvent(new CustomEvent('pr-sentinel:unauthorized'));
      }
    }

    let errorCode = 'unknown_error';
    let errorMessage = `HTTP error ${response.status}: ${response.statusText}`;
    let retryAfter: number | undefined;

    const retryHeader = response.headers.get('Retry-After');
    if (retryHeader) {
      const parsed = parseInt(retryHeader, 10);
      if (!isNaN(parsed)) {
        retryAfter = parsed;
      }
    }

    try {
      const errorJson = (await response.json()) as ApiErrorResponse | { detail?: unknown };
      if ('error' in errorJson && errorJson.error) {
        errorCode = errorJson.error.code || errorCode;
        errorMessage = errorJson.error.message || errorMessage;
      } else if ('detail' in errorJson) {
        if (typeof errorJson.detail === 'string') {
          errorMessage = errorJson.detail;
        } else if (Array.isArray(errorJson.detail)) {
          // FastAPI validation errors
          errorMessage = errorJson.detail
            .map((d: { msg?: string; loc?: string[] }) => d.msg || JSON.stringify(d))
            .join('; ');
          errorCode = 'validation_error';
        }
      }
    } catch {
      // Body was not JSON
    }

    throw new ApiClientError(response.status, errorCode, errorMessage, retryAfter);
  }

  return response.json() as Promise<T>;
}

export const apiClient = {
  /**
   * Health check endpoint
   */
  async getHealth(options?: RequestInit): Promise<HealthResponse> {
    return request<HealthResponse>('/health', options);
  },

  /**
   * Submit a new PR for review
   */
  async createReview(payload: ReviewCreate, options?: RequestInit): Promise<ReviewCreateResponse> {
    return request<ReviewCreateResponse>('/reviews', {
      ...options,
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /**
   * List reviews with optional status, owner, repo filters and pagination
   */
  async listReviews(
    params: ListReviewsParams = {},
    options?: RequestInit
  ): Promise<Page<ReviewListItem>> {
    const searchParams = new URLSearchParams();
    if (params.status) searchParams.set('status', params.status);
    if (params.owner) searchParams.set('owner', params.owner);
    if (params.repo) searchParams.set('repo', params.repo);
    if (params.page !== undefined) searchParams.set('page', String(params.page));
    if (params.page_size !== undefined) searchParams.set('page_size', String(params.page_size));

    const qs = searchParams.toString();
    return request<Page<ReviewListItem>>(`/reviews${qs ? `?${qs}` : ''}`, options);
  },

  /**
   * Get single review details
   */
  async getReview(id: string, options?: RequestInit): Promise<ReviewOut> {
    return request<ReviewOut>(`/reviews/${encodeURIComponent(id)}`, options);
  },

  /**
   * List findings for a review with filtering and sorting
   */
  async listReviewFindings(
    id: string,
    params: ListFindingsParams = {},
    options?: RequestInit
  ): Promise<Page<FindingOut>> {
    const searchParams = new URLSearchParams();

    if (params.severity && params.severity.length > 0) {
      for (const sev of params.severity) {
        searchParams.append('severity', sev);
      }
    }
    if (params.category) searchParams.set('category', params.category);
    if (params.source) searchParams.set('source', params.source);
    if (params.status) searchParams.set('status', params.status);
    if (params.file_path) searchParams.set('file_path', params.file_path);
    if (params.min_confidence !== undefined) {
      searchParams.set('min_confidence', String(params.min_confidence));
    }
    if (params.sort_by) searchParams.set('sort_by', params.sort_by);
    if (params.sort_order) searchParams.set('sort_order', params.sort_order);
    if (params.page !== undefined) searchParams.set('page', String(params.page));
    if (params.page_size !== undefined) searchParams.set('page_size', String(params.page_size));

    const qs = searchParams.toString();
    return request<Page<FindingOut>>(
      `/reviews/${encodeURIComponent(id)}/findings${qs ? `?${qs}` : ''}`,
      options
    );
  },

  /**
   * List touched files for a review
   */
  async listReviewFiles(
    id: string,
    params: ListFilesParams = {},
    options?: RequestInit
  ): Promise<Page<ReviewFileOut>> {
    const searchParams = new URLSearchParams();
    if (params.include_skipped !== undefined) {
      searchParams.set('include_skipped', String(params.include_skipped));
    }
    if (params.page !== undefined) searchParams.set('page', String(params.page));
    if (params.page_size !== undefined) searchParams.set('page_size', String(params.page_size));

    const qs = searchParams.toString();
    return request<Page<ReviewFileOut>>(
      `/reviews/${encodeURIComponent(id)}/files${qs ? `?${qs}` : ''}`,
      options
    );
  },

  /**
   * Delete a review by ID
   */
  async deleteReview(id: string, options?: RequestInit): Promise<void> {
    await request<void>(`/reviews/${encodeURIComponent(id)}`, {
      ...options,
      method: 'DELETE',
    });
  },

  /**
   * Retrieve a single finding
   */
  async getFinding(id: number, options?: RequestInit): Promise<FindingOut> {
    return request<FindingOut>(`/findings/${id}`, options);
  },

  /**
   * Update finding status (triage: open, approved, dismissed)
   */
  async patchFinding(
    id: number,
    update: FindingStatusUpdate,
    options?: RequestInit
  ): Promise<FindingOut> {
    return request<FindingOut>(`/findings/${id}`, {
      ...options,
      method: 'PATCH',
      body: JSON.stringify(update),
    });
  },

  /**
   * Retrieve global application settings
   */
  async getSettings(options?: RequestInit): Promise<SettingsOut> {
    return request<SettingsOut>('/settings', options);
  },

  /**
   * Update global application settings
   */
  async updateSettings(update: SettingsUpdate, options?: RequestInit): Promise<SettingsOut> {
    return request<SettingsOut>('/settings', {
      ...options,
      method: 'PUT',
      body: JSON.stringify(update),
    });
  },

  /**
   * Session and persistent access token management
   */
  getAccessToken,
  setAccessToken,
  clearAccessToken,
  isDeviceRemembered,
};
