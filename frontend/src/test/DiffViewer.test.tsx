import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DiffViewer } from '../components/diff/DiffViewer';
import type { FindingOut, ReviewFileOut } from '../types/api';

const mockFile: ReviewFileOut = {
  id: 1,
  review_id: 'rev-123',
  path: 'backend/app/auth.py',
  status: 'modified',
  additions: 2,
  deletions: 1,
  language: 'python',
  skipped_reason: null,
  findings_count: 1,
  patch: `@@ -10,4 +10,5 @@ def authenticate():
 context_line_before
-old_insecure_call()
+new_secure_call()
+extra_validation_step()
 context_line_after`,
 };

const mockFinding: FindingOut = {
  id: 42,
  review_id: 'rev-123',
  file_path: 'backend/app/auth.py',
  line_start: 11,
  line_end: 11,
  title: 'Insecure Authentication Call',
  severity: 'critical',
  category: 'security',
  source: 'static',
  tool_name: 'bandit',
  rule_id: 'B101',
  evidence: 'old_insecure_call()',
  explanation: 'Use of deprecated or unsafe authentication routine.',
  suggested_fix: 'new_secure_call()',
  confidence: 0.95,
  fingerprint: 'abc1234567890',
  status: 'open',
  created_at: '2026-11-04T00:00:00Z',
  updated_at: null,
};

describe('DiffViewer Component', () => {
  it('parses unified diff patch and displays hunk header, additions, deletions', () => {
    render(<DiffViewer file={mockFile} findings={[]} />);

    expect(screen.getByText('backend/app/auth.py')).toBeInTheDocument();
    expect(screen.getByText('+2')).toBeInTheDocument();
    expect(screen.getByText('-1')).toBeInTheDocument();

    // Context, added and deleted lines
    expect(screen.getByText('context_line_before')).toBeInTheDocument();
    expect(screen.getByText('old_insecure_call()')).toBeInTheDocument();
    expect(screen.getByText('new_secure_call()')).toBeInTheDocument();
    expect(screen.getByText('extra_validation_step()')).toBeInTheDocument();
  });

  it('renders inline finding annotation marker and triggers onSelectFinding', () => {
    const handleSelect = vi.fn();
    render(
      <DiffViewer
        file={mockFile}
        findings={[mockFinding]}
        onSelectFinding={handleSelect}
      />
    );

    // Finding badge & title should appear in the table
    expect(screen.getByText('Insecure Authentication Call')).toBeInTheDocument();

    const findingCard = screen.getByRole('button', {
      name: /Finding: Insecure Authentication Call/i,
    });
    fireEvent.click(findingCard);

    expect(handleSelect).toHaveBeenCalledWith(mockFinding);
  });

  it('handles null patch gracefully with informational message', () => {
    const fileWithoutPatch: ReviewFileOut = {
      ...mockFile,
      patch: null,
      skipped_reason: 'binary_file',
    };

    render(<DiffViewer file={fileWithoutPatch} findings={[]} />);

    expect(screen.getByText('No diff patch available for this file.')).toBeInTheDocument();
    expect(screen.getByText(/binary_file/)).toBeInTheDocument();
  });
});
