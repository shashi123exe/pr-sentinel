import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { apiClient } from './api/client';

describe('PR Sentinel App Smoke Test', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders application navigation and dashboard view', async () => {
    vi.spyOn(apiClient, 'getHealth').mockResolvedValueOnce({
      status: 'ok',
      database: 'ok',
    });
    vi.spyOn(apiClient, 'listReviews').mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 10,
    });

    render(<App />);

    expect(screen.getAllByText('PR Sentinel')[0]).toBeInTheDocument();
    expect(screen.getByText('Dashboard Overview')).toBeInTheDocument();
    expect(screen.getByText('Total Reviews')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Backend Online')).toBeInTheDocument();
    });
  });
});
