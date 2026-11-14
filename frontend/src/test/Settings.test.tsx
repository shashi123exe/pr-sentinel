import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Settings } from '../pages/Settings';
import {
  ACCESS_TOKEN_STORAGE_KEY,
  apiClient,
  getAccessToken,
  isDeviceRemembered,
  REMEMBER_DEVICE_STORAGE_KEY,
  setAccessToken,
} from '../api/client';
import type { SettingsOut } from '../types/api';

const mockSettings: SettingsOut = {
  id: 1,
  max_files: 50,
  max_diff_bytes: 200000,
  min_confidence: 0.7,
  enabled_categories: ['security', 'logic_bug'],
  gemini_model: 'gemini-2.5-flash',
};

describe('Settings Page', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('loads and displays settings from API', async () => {
    vi.spyOn(apiClient, 'getSettings').mockResolvedValueOnce(mockSettings);

    render(<Settings />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('50')).toBeInTheDocument();
      expect(screen.getByDisplayValue('200000')).toBeInTheDocument();
      expect(screen.getByDisplayValue('gemini-2.5-flash')).toBeInTheDocument();
    });
  });

  it('updates settings and shows success feedback banner', async () => {
    vi.spyOn(apiClient, 'getSettings').mockResolvedValueOnce(mockSettings);
    const updatedSettings: SettingsOut = {
      ...mockSettings,
      max_files: 75,
    };
    vi.spyOn(apiClient, 'updateSettings').mockResolvedValueOnce(updatedSettings);

    render(<Settings />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('50')).toBeInTheDocument();
    });

    const maxFilesInput = screen.getByLabelText(/maximum files per pr/i);
    fireEvent.change(maxFilesInput, { target: { value: '75' } });

    const saveBtn = screen.getByRole('button', { name: /save settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(apiClient.updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({ max_files: 75 })
      );
      expect(
        screen.getByText('Application analysis settings successfully updated.')
      ).toBeInTheDocument();
    });
  });

  it('saves token with remember-device option and persists across browser restart simulation', async () => {
    vi.spyOn(apiClient, 'getSettings').mockResolvedValueOnce(mockSettings);

    render(<Settings />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('50')).toBeInTheDocument();
    });

    const tokenInput = screen.getByPlaceholderText(/enter shared access token.../i);
    fireEvent.change(tokenInput, { target: { value: 'settings-secret-token-789' } });

    const rememberCheckbox = screen.getByTestId('settings-remember-device-checkbox') as HTMLInputElement;
    fireEvent.click(rememberCheckbox);
    expect(rememberCheckbox.checked).toBe(true);

    const saveTokenBtn = screen.getByRole('button', { name: /save token/i });
    await act(async () => {
      fireEvent.click(saveTokenBtn);
    });

    expect(getAccessToken()).toBe('settings-secret-token-789');
    expect(isDeviceRemembered()).toBe(true);
    expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBe('settings-secret-token-789');
    expect(localStorage.getItem(REMEMBER_DEVICE_STORAGE_KEY)).toBe('true');
  });

  it('provides a logout action that clears token from all storages and dispatches unauthorized event', async () => {
    vi.spyOn(apiClient, 'getSettings').mockResolvedValueOnce(mockSettings);
    setAccessToken('logged-in-user-token', true);

    const unauthorizedListener = vi.fn();
    window.addEventListener('pr-sentinel:unauthorized', unauthorizedListener);

    render(<Settings />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('50')).toBeInTheDocument();
    });

    const logoutBtn = screen.getByTestId('logout-btn');
    expect(logoutBtn).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(logoutBtn);
    });

    expect(getAccessToken()).toBeNull();
    expect(isDeviceRemembered()).toBe(false);
    expect(sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)).toBeNull();
    expect(unauthorizedListener).toHaveBeenCalledTimes(1);

    window.removeEventListener('pr-sentinel:unauthorized', unauthorizedListener);
  });
});
