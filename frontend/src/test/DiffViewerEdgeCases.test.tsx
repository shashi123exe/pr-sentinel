import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DiffViewer } from '../components/diff/DiffViewer';
import type { FindingOut, ReviewFileOut } from '../types/api';

const baseFile: ReviewFileOut = {
  id: 1,
  review_id: 'rev-1',
  path: 'src/auth/service.py',
  status: 'modified',
  additions: 5,
  deletions: 3,
  patch: '',
  language: 'python',
  skipped_reason: null,
  findings_count: 0,
};

const mockFindingA: FindingOut = {
  id: 101,
  review_id: 'rev-1',
  rule_id: 'B105',
  tool_name: 'bandit',
  source: 'static',
  category: 'security',
  severity: 'critical',
  confidence: 0.95,
  title: 'Hardcoded Password in Service',
  explanation: 'Hardcoded password was detected in authentication function.',
  file_path: 'src/auth/service.py',
  line_start: 12,
  line_end: 12,
  evidence: 'password = "secret"',
  suggested_fix: null,
  status: 'open',
  fingerprint: 'fp-101',
  created_at: '2026-11-04T10:00:00Z',
  updated_at: '2026-11-04T10:00:00Z',
};

const mockFindingB: FindingOut = {
  id: 102,
  review_id: 'rev-1',
  rule_id: 'SEC-LOGIC',
  tool_name: 'gemini',
  source: 'ai',
  category: 'logic_bug',
  severity: 'high',
  confidence: 0.88,
  title: 'Missing Validation on Token Expiry',
  explanation: 'Token validity is checked without considering expiration timestamp.',
  file_path: 'src/auth/service.py',
  line_start: 12,
  line_end: 12,
  evidence: 'if not token: return False',
  suggested_fix: null,
  status: 'open',
  fingerprint: 'fp-102',
  created_at: '2026-11-04T10:00:00Z',
  updated_at: '2026-11-04T10:00:00Z',
};

describe('DiffViewer Edge Cases', () => {
  it('handles empty patch and displays unavailable empty state', () => {
    const fileWithEmptyPatch: ReviewFileOut = {
      ...baseFile,
      patch: '',
      skipped_reason: 'binary_file',
    };

    render(<DiffViewer file={fileWithEmptyPatch} findings={[]} />);

    expect(
      screen.getByText(/no diff patch available for this file/i)
    ).toBeInTheDocument();
    expect(screen.getByText('binary_file')).toBeInTheDocument();
  });

  it('renders diff headers/metadata before first hunk as meta lines', () => {
    const patchWithPreamble = `diff --git a/src/auth/service.py b/src/auth/service.py
index 1234567..89abcdef 100644
--- a/src/auth/service.py
+++ b/src/auth/service.py
@@ -10,3 +10,3 @@ def verify():
 context line
-old line
+new line`;

    const file: ReviewFileOut = {
      ...baseFile,
      patch: patchWithPreamble,
    };

    const { container } = render(<DiffViewer file={file} findings={[]} />);

    // Meta rows exist and contain git diff headers
    const metaRows = container.querySelectorAll('.diff-row-meta');
    expect(metaRows.length).toBe(4);
    expect(metaRows[0].textContent).toContain('diff --git');
    expect(metaRows[2].textContent).toContain('--- a/src/auth/service.py');
    expect(metaRows[3].textContent).toContain('+++ b/src/auth/service.py');
  });

  it('handles zero-added line hunk (+0,0) and zero-removed line hunk (-0,0)', () => {
    // Pure addition hunk: -0,0 +1,2
    const pureAddPatch = `@@ -0,0 +1,2 @@
+line one
+line two`;

    const file: ReviewFileOut = {
      ...baseFile,
      patch: pureAddPatch,
    };

    const { container } = render(<DiffViewer file={file} findings={[]} />);

    const hunkRow = container.querySelector('.diff-row-hunk');
    expect(hunkRow?.textContent).toContain('@@ -0,0 +1,2 @@');

    const addRows = container.querySelectorAll('.diff-row-add');
    expect(addRows.length).toBe(2);
    // Line numbers should start at 1
    expect(addRows[0].querySelector('.new-num')?.textContent).toBe('1');
    expect(addRows[1].querySelector('.new-num')?.textContent).toBe('2');
  });

  it('renders multiple findings on the same line and allows selecting each', () => {
    const patch = `@@ -10,4 +10,4 @@
 def check_auth():
-    legacy_pass()
+    password = "secret"
     return True`;

    const file: ReviewFileOut = {
      ...baseFile,
      patch,
    };

    const onSelect = vi.fn();

    render(
      <DiffViewer
        file={file}
        findings={[mockFindingA, mockFindingB]}
        onSelectFinding={onSelect}
      />
    );

    // Both finding titles should be rendered
    const cardA = screen.getByText('Hardcoded Password in Service');
    const cardB = screen.getByText('Missing Validation on Token Expiry');

    expect(cardA).toBeInTheDocument();
    expect(cardB).toBeInTheDocument();

    // Clicking card A calls onSelect with finding A
    fireEvent.click(cardA);
    expect(onSelect).toHaveBeenCalledWith(mockFindingA);

    // Clicking card B calls onSelect with finding B
    fireEvent.click(cardB);
    expect(onSelect).toHaveBeenCalledWith(mockFindingB);
  });

  it('correctly tracks line numbers across multiple hunks', () => {
    const multiHunkPatch = `@@ -5,3 +5,3 @@
 line 5
-line 6 old
+line 6 new
 line 7
@@ -50,3 +50,3 @@
 line 50
-line 51 old
+line 51 new
 line 52`;

    const file: ReviewFileOut = {
      ...baseFile,
      patch: multiHunkPatch,
    };

    const { container } = render(<DiffViewer file={file} findings={[]} />);

    const hunkRows = container.querySelectorAll('.diff-row-hunk');
    expect(hunkRows.length).toBe(2);

    const addRows = container.querySelectorAll('.diff-row-add');
    expect(addRows.length).toBe(2);
    // Hunk 1 added line number is 6
    expect(addRows[0].querySelector('.new-num')?.textContent).toBe('6');
    // Hunk 2 added line number is 51
    expect(addRows[1].querySelector('.new-num')?.textContent).toBe('51');
  });
});
