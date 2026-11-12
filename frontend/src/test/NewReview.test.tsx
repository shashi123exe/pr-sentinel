import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { NewReview } from '../pages/NewReview';
import { apiClient } from '../api/client';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('NewReview Page', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockNavigate.mockReset();
  });

  it('validates empty submission', async () => {
    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    expect(
      screen.getByText('Please enter a GitHub Pull Request URL.')
    ).toBeInTheDocument();
  });

  it('validates invalid URL format', async () => {
    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByPlaceholderText(/https:\/\/github\.com\/owner\/repo\/pull\/42/i);
    fireEvent.change(input, { target: { value: 'https://gitlab.com/wrong/repo/merge_requests/1' } });

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    expect(
      screen.getByText(/Invalid PR URL format/i)
    ).toBeInTheDocument();
  });

  it('submits valid GitHub PR URL and redirects to review execution page', async () => {
    vi.spyOn(apiClient, 'createReview').mockResolvedValueOnce({
      id: 'mock-review-uuid-123',
      status: 'queued',
    });

    render(
      <MemoryRouter>
        <NewReview />
      </MemoryRouter>
    );

    const input = screen.getByPlaceholderText(/https:\/\/github\.com\/owner\/repo\/pull\/42/i);
    fireEvent.change(input, {
      target: { value: 'https://github.com/fastapi/fastapi/pull/1234' },
    });

    const submitBtn = screen.getByRole('button', { name: /launch sentinel review/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(apiClient.createReview).toHaveBeenCalledWith({
        pr_url: 'https://github.com/fastapi/fastapi/pull/1234',
      });
      expect(mockNavigate).toHaveBeenCalledWith('/reviews/mock-review-uuid-123');
    });
  });
});
