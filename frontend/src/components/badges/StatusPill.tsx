import React from 'react';
import type { FindingStatus, ReviewStatus } from '../../types/api';

interface StatusPillProps {
  status: ReviewStatus | FindingStatus | string;
  size?: 'sm' | 'md';
}

export const StatusPill: React.FC<StatusPillProps> = ({
  status,
  size = 'md',
}) => {
  const normalized = status.toLowerCase();
  const isPulsing = normalized === 'running' || normalized === 'queued';

  const formatLabel = (val: string) => {
    switch (val) {
      case 'in_progress':
      case 'running':
        return 'Running';
      case 'completed':
        return 'Completed';
      case 'queued':
        return 'Queued';
      case 'failed':
        return 'Failed';
      case 'approved':
        return 'Approved';
      case 'dismissed':
        return 'Dismissed';
      case 'open':
        return 'Open';
      default:
        return val.charAt(0).toUpperCase() + val.slice(1);
    }
  };

  return (
    <span
      className={`status-pill status-pill-${normalized} ${size === 'sm' ? 'status-pill-sm' : ''}`}
      role="status"
    >
      <span
        className={`status-dot status-dot-${normalized} ${isPulsing ? 'pulse' : ''}`}
        aria-hidden="true"
      />
      <span>{formatLabel(normalized)}</span>
    </span>
  );
};
