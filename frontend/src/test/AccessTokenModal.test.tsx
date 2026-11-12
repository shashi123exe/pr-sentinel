import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessTokenModal } from '../components/common/AccessTokenModal';
import { ACCESS_TOKEN_STORAGE_KEY, getAccessToken, setAccessToken } from '../api/client';

describe('AccessTokenModal Component', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('remains hidden by default when no unauthorized event occurred', () => {
    render(<AccessTokenModal />);
    expect(screen.queryByTestId('access-token-modal')).not.toBeInTheDocument();
  });

  it('opens automatically when pr-sentinel:unauthorized event is dispatched', () => {
    render(<AccessTokenModal />);
    expect(screen.queryByTestId('access-token-modal')).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new CustomEvent('pr-sentinel:unauthorized'));
    });

    expect(screen.getByTestId('access-token-modal')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /api access token required/i })).toBeInTheDocument();
  });

  it('saves token strictly in sessionStorage and closes modal on submit', () => {
    const mockSuccess = vi.fn();
    const tokenUpdatedListener = vi.fn();
    window.addEventListener('pr-sentinel:token-updated', tokenUpdatedListener);

    render(<AccessTokenModal isOpen={true} onSuccess={mockSuccess} />);

    const input = screen.getByLabelText(/x-access-token/i);
    fireEvent.change(input, { target: { value: 'sentinel-token-secret-123' } });

    const submitBtn = screen.getByRole('button', { name: /save & continue/i });
    fireEvent.click(submitBtn);

    // Stored strictly in sessionStorage
    expect(getAccessToken()).toBe('sentinel-token-secret-123');
    expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('sentinel-token-secret-123');

    // Never stored in localStorage
    expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.length).toBe(0);

    // Dispatches token-updated event without reloading page
    expect(tokenUpdatedListener).toHaveBeenCalledTimes(1);
    expect(mockSuccess).toHaveBeenCalledTimes(1);

    window.removeEventListener('pr-sentinel:token-updated', tokenUpdatedListener);
  });

  it('allows clearing the stored token from sessionStorage', () => {
    setAccessToken('existing-session-token');
    render(<AccessTokenModal isOpen={true} />);

    expect(getAccessToken()).toBe('existing-session-token');

    const clearBtn = screen.getByRole('button', { name: /clear token/i });
    fireEvent.click(clearBtn);

    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
  });

  it('closes on cancel button click or Escape key press', () => {
    const handleClose = vi.fn();
    render(<AccessTokenModal isOpen={true} onClose={handleClose} />);

    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(handleClose).toHaveBeenCalledTimes(2);
  });
});
