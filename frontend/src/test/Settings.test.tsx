import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Settings } from '../pages/Settings';
import { apiClient } from '../api/client';
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
});
