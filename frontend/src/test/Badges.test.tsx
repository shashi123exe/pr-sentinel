import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SeverityBadge } from '../components/badges/SeverityBadge';
import { SourceBadge } from '../components/badges/SourceBadge';
import { StatusPill } from '../components/badges/StatusPill';

describe('SeverityBadge Component', () => {
  it('renders critical severity with correct aria label and class', () => {
    render(<SeverityBadge severity="critical" />);
    const badge = screen.getByRole('status', { name: /severity: critical/i });
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveClass('badge-critical');
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });

  it('renders high, medium, low severities', () => {
    const { rerender } = render(<SeverityBadge severity="high" />);
    expect(screen.getByText('High')).toBeInTheDocument();

    rerender(<SeverityBadge severity="medium" />);
    expect(screen.getByText('Medium')).toBeInTheDocument();

    rerender(<SeverityBadge severity="low" />);
    expect(screen.getByText('Low')).toBeInTheDocument();
  });
});

describe('SourceBadge Component', () => {
  it('renders AI Review source', () => {
    render(<SourceBadge source="ai" />);
    expect(screen.getByText('AI Review')).toBeInTheDocument();
  });

  it('renders Static Analysis source with custom tool name', () => {
    render(<SourceBadge source="static" toolName="bandit" ruleId="B101" />);
    expect(screen.getByText('BANDIT')).toBeInTheDocument();
    const badge = screen.getByRole('note');
    expect(badge).toHaveAttribute('title', 'Rule: B101');
  });
});

describe('StatusPill Component', () => {
  it('renders running status with pulsing dot', () => {
    render(<StatusPill status="running" />);
    expect(screen.getByText('Running')).toBeInTheDocument();
    const dot = document.querySelector('.status-dot');
    expect(dot).toHaveClass('pulse');
  });

  it('renders completed status without pulsing dot', () => {
    render(<StatusPill status="completed" />);
    expect(screen.getByText('Completed')).toBeInTheDocument();
    const dot = document.querySelector('.status-dot');
    expect(dot).not.toHaveClass('pulse');
  });

  it('renders triage statuses: approved and dismissed', () => {
    const { rerender } = render(<StatusPill status="approved" />);
    expect(screen.getByText('Approved')).toBeInTheDocument();

    rerender(<StatusPill status="dismissed" />);
    expect(screen.getByText('Dismissed')).toBeInTheDocument();
  });
});
