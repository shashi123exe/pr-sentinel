import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '../api/client';
import { FindingDetailDrawer } from '../components/findings/FindingDetailDrawer';
import type { FindingOut } from '../types/api';

const mockFinding: FindingOut = {
  id: 42,
  review_id: 'rev-test',
  rule_id: 'SEC001',
  tool_name: 'gemini',
  source: 'ai',
  category: 'security',
  severity: 'critical',
  confidence: 0.92,
  title: 'SQL Injection Vulnerability',
  explanation: 'Unsanitized user parameter passed directly to raw SQL query.',
  file_path: 'backend/app/db/query.py',
  line_start: 45,
  line_end: 48,
  evidence: 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")',
  suggested_fix: 'cursor.execute("SELECT * FROM users WHERE id = :id", {"id": user_id})',
  status: 'open',
  fingerprint: 'fp-sql-injection-42',
  created_at: '2026-11-04T10:00:00Z',
  updated_at: '2026-11-04T10:00:00Z',
};

describe('Finding Triage Edge Cases', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('sends strictly status in patchFinding payload (forbid extra fields)', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patchFinding').mockResolvedValue({
      ...mockFinding,
      status: 'approved',
    });

    const onUpdate = vi.fn();

    render(
      <FindingDetailDrawer
        finding={mockFinding}
        isOpen={true}
        onClose={vi.fn()}
        onFindingUpdated={onUpdate}
      />
    );

    const approveBtn = screen.getByRole('button', { name: /approve/i });
    fireEvent.click(approveBtn);

    await waitFor(() => {
      // Must only contain { status: 'approved' }, no extra fields
      expect(patchSpy).toHaveBeenCalledWith(42, { status: 'approved' });
      expect(onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ id: 42, status: 'approved' })
      );
    });
  });

  it('rolls back optimistic update when patchFinding fails', async () => {
    vi.spyOn(apiClient, 'patchFinding').mockRejectedValueOnce(
      new Error('Internal server error 500')
    );

    const onUpdate = vi.fn();

    render(
      <FindingDetailDrawer
        finding={mockFinding}
        isOpen={true}
        onClose={vi.fn()}
        onFindingUpdated={onUpdate}
      />
    );

    expect(screen.getByText('open')).toBeInTheDocument();

    const dismissBtn = screen.getByRole('button', { name: /dismiss/i });
    fireEvent.click(dismissBtn);

    // After failure, error banner appears and status rolls back to open
    await waitFor(() => {
      expect(screen.getByText('Internal server error 500')).toBeInTheDocument();
      expect(screen.getByText('open')).toBeInTheDocument();
    });

    // onFindingUpdated callback should NOT have been called with failure
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('disables triage buttons while mutation is in progress', async () => {
    let resolveMutation: (val: any) => void;
    const slowMutation = new Promise((resolve) => {
      resolveMutation = resolve;
    });

    vi.spyOn(apiClient, 'patchFinding').mockImplementation(() => slowMutation as any);

    render(
      <FindingDetailDrawer
        finding={mockFinding}
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    const approveBtn = screen.getByRole('button', { name: /approve/i });
    const dismissBtn = screen.getByRole('button', { name: /dismiss/i });

    expect(approveBtn).not.toBeDisabled();
    expect(dismissBtn).not.toBeDisabled();

    fireEvent.click(approveBtn);

    // Due to optimistic update, status became 'approved'.
    // The newly visible 'Reopen' button and the 'Dismiss' button must both be disabled while mutation is in-flight.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /dismiss/i })).toBeDisabled();
      expect(screen.getByRole('button', { name: /reopen/i })).toBeDisabled();
    });

    // Complete mutation
    resolveMutation!({ ...mockFinding, status: 'approved' });

    await waitFor(() => {
      // Once resolved, isUpdating is false, so buttons are enabled
      expect(screen.getByRole('button', { name: /dismiss/i })).not.toBeDisabled();
      expect(screen.getByRole('button', { name: /reopen/i })).not.toBeDisabled();
    });
  });
});
