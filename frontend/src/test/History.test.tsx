import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '../api/client';
import { History } from '../pages/History';
import type { Page, ReviewListItem } from '../types/api';

const mockReviewsData: Page<ReviewListItem> = {
  items: [
    {
      id: 'rev-001',
      pr_url: 'https://github.com/google/guava/pull/42',
      owner: 'google',
      repo: 'guava',
      pr_number: 42,
      pr_title: 'Add safe hashing utility',
      pr_author: 'octocat',
      status: 'completed',
      current_stage: 'completed',
      created_at: '2026-11-04T10:00:00Z',
      completed_at: '2026-11-04T10:05:00Z',
      files_changed: 3,
      additions: 120,
      deletions: 15,
      findings_summary: {
        critical: 1,
        high: 2,
        medium: 0,
        low: 0,
        total: 3,
      },
    },
    {
      id: 'rev-002',
      pr_url: 'https://github.com/facebook/react/pull/101',
      owner: 'facebook',
      repo: 'react',
      pr_number: 101,
      pr_title: 'Fix hydration mismatch',
      pr_author: 'dan_abramov',
      status: 'running',
      current_stage: 'ai_review',
      created_at: '2026-11-04T09:30:00Z',
      completed_at: null,
      files_changed: 1,
      additions: 10,
      deletions: 2,
      findings_summary: {
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
        total: 0,
      },
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
};

describe('History Page', () => {
  beforeEach(() => {
    vi.spyOn(apiClient, 'listReviews').mockResolvedValue(mockReviewsData);
    vi.spyOn(apiClient, 'deleteReview').mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders review list with PR metadata and findings counts', async () => {
    render(
      <BrowserRouter>
        <History />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Add safe hashing utility')).toBeInTheDocument();
      expect(screen.getByText('Fix hydration mismatch')).toBeInTheDocument();
    });

    expect(screen.getByText('google/guava')).toBeInTheDocument();
    expect(screen.getByText('@octocat')).toBeInTheDocument();
    expect(screen.getByText('1C')).toBeInTheDocument();
    expect(screen.getByText('2H')).toBeInTheDocument();
  });

  it('filters reviews by status dropdown', async () => {
    render(
      <BrowserRouter>
        <History />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Add safe hashing utility')).toBeInTheDocument();
    });

    const statusSelect = screen.getByLabelText(/filter by status/i);
    fireEvent.change(statusSelect, { target: { value: 'completed' } });

    await waitFor(() => {
      expect(apiClient.listReviews).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'completed' })
      );
    });
  });

  it('opens delete confirmation dialog and cancels without calling deleteReview', async () => {
    render(
      <BrowserRouter>
        <History />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Add safe hashing utility')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByLabelText('Delete review for PR #42');
    fireEvent.click(deleteBtn);

    // Dialog should be open
    expect(
      screen.getByText(/Are you sure you want to delete the review for google\/guava#42/i)
    ).toBeInTheDocument();

    // Click Cancel
    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);

    expect(apiClient.deleteReview).not.toHaveBeenCalled();
    expect(
      screen.queryByText(/Are you sure you want to delete the review for google\/guava#42/i)
    ).not.toBeInTheDocument();
  });

  it('confirms review deletion and calls deleteReview API', async () => {
    render(
      <BrowserRouter>
        <History />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Add safe hashing utility')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByLabelText('Delete review for PR #42');
    fireEvent.click(deleteBtn);

    const confirmBtn = screen.getByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(apiClient.deleteReview).toHaveBeenCalledWith('rev-001');
    });
  });

  it('displays error banner if deletion API fails', async () => {
    vi.spyOn(apiClient, 'deleteReview').mockRejectedValueOnce(
      new Error('Database delete error')
    );

    render(
      <BrowserRouter>
        <History />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Add safe hashing utility')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByLabelText('Delete review for PR #42');
    fireEvent.click(deleteBtn);

    const confirmBtn = screen.getByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(screen.getByText('Database delete error')).toBeInTheDocument();
    });
  });
});
