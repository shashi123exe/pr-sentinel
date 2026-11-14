import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../components/layout/Sidebar';
import {
  apiClient,
  clearAccessToken,
  getAccessToken,
  isDeviceRemembered,
  setAccessToken,
} from '../api/client';

describe('Sidebar Navigation & Authentication', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'getHealth').mockResolvedValue({ status: 'ok', database: 'ok' });
  });

  it('renders standard navigation links without logout when no token is stored', async () => {
    clearAccessToken();
    render(
      <BrowserRouter>
        <Sidebar theme="dark" onToggleTheme={vi.fn()} />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Backend Online')).toBeInTheDocument();
    });

    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('New Review')).toBeInTheDocument();
    expect(screen.getByText('Review History')).toBeInTheDocument();
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-logout-btn')).not.toBeInTheDocument();
  });

  it('displays logout button when an access token is configured', async () => {
    setAccessToken('sidebar-auth-token', true);

    render(
      <BrowserRouter>
        <Sidebar theme="dark" onToggleTheme={vi.fn()} />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Backend Online')).toBeInTheDocument();
    });

    const logoutBtn = screen.getByTestId('sidebar-logout-btn');
    expect(logoutBtn).toBeInTheDocument();
    expect(logoutBtn).toHaveTextContent(/log out/i);
  });

  it('clears token from all storages and dispatches unauthorized event when logout button is clicked', async () => {
    setAccessToken('sidebar-auth-token', true);
    const unauthorizedListener = vi.fn();
    window.addEventListener('pr-sentinel:unauthorized', unauthorizedListener);

    render(
      <BrowserRouter>
        <Sidebar theme="dark" onToggleTheme={vi.fn()} />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Backend Online')).toBeInTheDocument();
    });

    const logoutBtn = screen.getByTestId('sidebar-logout-btn');
    await act(async () => {
      fireEvent.click(logoutBtn);
    });

    expect(getAccessToken()).toBeNull();
    expect(isDeviceRemembered()).toBe(false);
    expect(unauthorizedListener).toHaveBeenCalledTimes(1);

    window.removeEventListener('pr-sentinel:unauthorized', unauthorizedListener);
  });
});
