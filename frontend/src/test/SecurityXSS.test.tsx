import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FindingDetailDrawer } from '../components/findings/FindingDetailDrawer';
import { DiffViewer } from '../components/diff/DiffViewer';
import type { FindingOut, ReviewFileOut } from '../types/api';

describe('Frontend XSS Protection Security Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders malicious scripts in finding titles and explanations as plain text without DOM injection', () => {
    const maliciousFinding: FindingOut = {
      id: 999,
      review_id: 'rev-xss-test',
      file_path: 'src/components/<script>alert("path-xss")</script>.tsx',
      line_start: 1,
      line_end: 5,
      title: '<script>alert("xss-title")</script>',
      severity: 'high',
      category: 'security',
      source: 'ai',
      tool_name: 'gemini',
      rule_id: 'xss-payload',
      evidence: '<img src=x onerror="alert(\'xss-evidence\')" />',
      explanation: '<svg onload="alert(\'xss-explanation\')">Exploit</svg>',
      suggested_fix: '<iframe src="javascript:alert(\'xss-iframe\')"></iframe>',
      confidence: 0.99,
      fingerprint: 'xss-test-fingerprint',
      status: 'open',
      created_at: '2026-11-04T00:00:00Z',
      updated_at: null,
    };

    const { container } = render(
      <FindingDetailDrawer
        finding={maliciousFinding}
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    // Verify raw strings are safely rendered as text content
    expect(screen.getByText('<script>alert("xss-title")</script>')).toBeInTheDocument();
    expect(screen.getByText(/Exploit/)).toBeInTheDocument();

    // Verify NO script, img with onerror, or iframe elements were injected into the DOM
    const scripts = container.querySelectorAll('script');
    expect(scripts.length).toBe(0);

    const iframes = container.querySelectorAll('iframe');
    expect(iframes.length).toBe(0);

    const images = container.querySelectorAll('img');
    images.forEach((img) => {
      expect(img.getAttribute('onerror')).toBeNull();
    });
  });

  it('safely renders diff hunks containing adversarial HTML and scripts without executing them', () => {
    const maliciousFile: ReviewFileOut = {
      id: 501,
      review_id: 'rev-xss-test',
      path: 'src/views/Dashboard.tsx',
      language: 'tsx',
      status: 'modified',
      additions: 2,
      deletions: 1,
      skipped_reason: null,
      findings_count: 0,
      patch: '@@ -1,1 +1,2 @@\n-<script>\n+<script>alert(1)</script>',
    };

    const { container } = render(
      <DiffViewer
        file={maliciousFile}
        findings={[]}
        onSelectFinding={vi.fn()}
      />
    );

    // Verify malicious strings render as text
    expect(screen.getByText(/alert\(1\)/)).toBeInTheDocument();

    // Verify no executable DOM elements injected
    expect(container.querySelectorAll('script').length).toBe(0);
    expect(container.querySelectorAll('img[onerror]').length).toBe(0);
    expect((window as unknown as { __xss_fired?: boolean }).__xss_fired).toBeUndefined();
  });
});
