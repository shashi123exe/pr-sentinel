import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, ApiClientError } from '../api/client';
import { NewReview } from '../pages/NewReview';

describe('NewReview Edge Cases', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('handles HTTP 422 unprocessable entity from backend', async () => {
    vi.spyOn(apiClient, 'createReview').mockRejectedValueOnce(
      new ApiClientError(
        422,
        'VALIDATION_ERROR',
        'Pull request not found or repository is private.'
      )
    );

    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByLabelText(/GitHub Pull Request URL/i);
    fireEvent.change(input, {
      target: { value: 'https://github.com/google/private-repo/pull/999' },
    });

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(
        screen.getByText('Pull request not found or repository is private.')
      ).toBeInTheDocument();
    });
  });

  it('handles HTTP 429 rate limit with retryAfter from backend headers', async () => {
    vi.spyOn(apiClient, 'createReview').mockRejectedValueOnce(
      new ApiClientError(
        429,
        'RATE_LIMIT_EXCEEDED',
        'Rate limit exceeded for GitHub API.',
        45
      )
    );

    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByLabelText(/GitHub Pull Request URL/i);
    fireEvent.change(input, {
      target: { value: 'https://github.com/google/guava/pull/123' },
    });

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(
        screen.getByText('Rate limit exceeded for GitHub API. (Retry after 45 seconds)')
      ).toBeInTheDocument();
    });
  });

  it('handles HTTP 429 rate limit without retryAfter without fabricating a delay', async () => {
    vi.spyOn(apiClient, 'createReview').mockRejectedValueOnce(
      new ApiClientError(429, 'RATE_LIMIT_EXCEEDED', 'Rate limit exceeded.')
    );

    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByLabelText(/GitHub Pull Request URL/i);
    fireEvent.change(input, {
      target: { value: 'https://github.com/google/guava/pull/123' },
    });

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText('Rate limit exceeded.')).toBeInTheDocument();
      expect(screen.queryByText(/retry after/i)).not.toBeInTheDocument();
    });
  });

  it('prevents duplicate submissions while request is in flight', async () => {
    let resolvePromise: (value: any) => void;
    const slowPromise = new Promise((resolve) => {
      resolvePromise = resolve;
    });

    const createSpy = vi
      .spyOn(apiClient, 'createReview')
      .mockImplementation(() => slowPromise as any);

    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByLabelText(/GitHub Pull Request URL/i);
    fireEvent.change(input, {
      target: { value: 'https://github.com/google/guava/pull/123' },
    });

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });

    // First click
    fireEvent.click(submitBtn);

    // Button should now be disabled with "Queuing Review..."
    expect(screen.getByRole('button', { name: /queuing review/i })).toBeDisabled();

    // Second click should be ignored
    fireEvent.click(submitBtn);

    expect(createSpy).toHaveBeenCalledTimes(1);

    // Resolve the promise
    resolvePromise!({ id: 'rev-created' });
  });

  it('preserves form draft on 401 unauthorized and allows smooth retry after token update', async () => {
    const createSpy = vi
      .spyOn(apiClient, 'createReview')
      .mockRejectedValueOnce(
        new ApiClientError(401, 'UNAUTHORIZED', 'Missing or invalid access token.')
      )
      .mockResolvedValueOnce({
        id: 'rev-retry-success-123',
        pr_url: 'https://github.com/google/guava/pull/123',
        status: 'queued',
        created_at: new Date().toISOString(),
        findings_count: 0,
      } as any);

    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByLabelText(/GitHub Pull Request URL/i) as HTMLInputElement;
    fireEvent.change(input, {
      target: { value: 'https://github.com/google/guava/pull/123' },
    });
    expect(input.value).toBe('https://github.com/google/guava/pull/123');

    // First attempt -> 401 Unauthorized
    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText('Missing or invalid access token.')).toBeInTheDocument();
    });

    // Verify form input draft is intact (not wiped by a reload)
    expect(input.value).toBe('https://github.com/google/guava/pull/123');

    // Simulate user entering valid token in AccessTokenModal (fires token-updated event)
    fireEvent(window, new CustomEvent('pr-sentinel:token-updated'));

    // Verify error banner is cleared
    await waitFor(() => {
      expect(screen.queryByText('Missing or invalid access token.')).not.toBeInTheDocument();
    });

    // Form input draft is still intact
    expect(input.value).toBe('https://github.com/google/guava/pull/123');

    // User clicks submit again to retry
    const retrySubmitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(retrySubmitBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledTimes(2);
      expect(createSpy).toHaveBeenLastCalledWith({
        pr_url: 'https://github.com/google/guava/pull/123',
      });
    });
  });
});
