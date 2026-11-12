import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FindingDetailDrawer } from '../components/findings/FindingDetailDrawer';
import { apiClient } from '../api/client';
import type { FindingOut } from '../types/api';

const mockFinding: FindingOut = {
  id: 101,
  review_id: 'rev-xyz',
  file_path: 'backend/app/db.py',
  line_start: 25,
  line_end: 28,
  title: 'Raw SQL String Formatting Detected',
  severity: 'high',
  category: 'security',
  source: 'ai',
  tool_name: 'gemini',
  rule_id: 'sql_injection_risk',
  evidence: 'query = f"SELECT * FROM users WHERE id = {user_id}"',
  explanation: 'Direct interpolation allows SQL injection attack vectors.',
  suggested_fix: 'query = text("SELECT * FROM users WHERE id = :id")',
  confidence: 0.92,
  fingerprint: 'sql-inj-fp-101',
  status: 'open',
  created_at: '2026-11-04T00:00:00Z',
  updated_at: null,
};

describe('FindingDetailDrawer Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders all finding details including evidence and suggested fix', () => {
    render(
      <FindingDetailDrawer
        finding={mockFinding}
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText('Raw SQL String Formatting Detected')).toBeInTheDocument();
    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getByText('AI Review')).toBeInTheDocument();
    expect(screen.getByText(/Direct interpolation allows SQL injection/)).toBeInTheDocument();
    expect(
      screen.getByText('query = f"SELECT * FROM users WHERE id = {user_id}"')
    ).toBeInTheDocument();
    expect(
      screen.getByText('query = text("SELECT * FROM users WHERE id = :id")')
    ).toBeInTheDocument();
  });

  it('performs triage approval and invokes update callback', async () => {
    const updatedFinding: FindingOut = {
      ...mockFinding,
      status: 'approved',
      updated_at: '2026-11-04T01:00:00Z',
    };
    vi.spyOn(apiClient, 'patchFinding').mockResolvedValueOnce(updatedFinding);

    const handleUpdated = vi.fn();
    render(
      <FindingDetailDrawer
        finding={mockFinding}
        isOpen={true}
        onClose={vi.fn()}
        onFindingUpdated={handleUpdated}
      />
    );

    const approveBtn = screen.getByRole('button', { name: /approve/i });
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(apiClient.patchFinding).toHaveBeenCalledWith(101, {
        status: 'approved',
      });
      expect(handleUpdated).toHaveBeenCalledWith(updatedFinding);
    });
  });

  it('closes on close button click', () => {
    const handleClose = vi.fn();
    render(
      <FindingDetailDrawer
        finding={mockFinding}
        isOpen={true}
        onClose={handleClose}
      />
    );

    const closeBtn = screen.getByRole('button', { name: /close drawer/i });
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalled();
  });
});
