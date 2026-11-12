import React from 'react';
import { AlertOctagon, AlertTriangle, HelpCircle, Info } from 'lucide-react';
import type { Severity } from '../../types/api';

interface SeverityBadgeProps {
  severity: Severity;
  size?: 'sm' | 'md';
  showIcon?: boolean;
}

export const SeverityBadge: React.FC<SeverityBadgeProps> = ({
  severity,
  size = 'md',
  showIcon = true,
}) => {
  const normalized = severity.toLowerCase() as Severity;

  const renderIcon = () => {
    if (!showIcon) return null;
    const iconProps = {
      size: size === 'sm' ? 12 : 14,
      className: 'badge-icon',
      'aria-hidden': 'true' as const,
    };
    switch (normalized) {
      case 'critical':
        return <AlertOctagon {...iconProps} />;
      case 'high':
        return <AlertTriangle {...iconProps} />;
      case 'medium':
        return <Info {...iconProps} />;
      case 'low':
      default:
        return <HelpCircle {...iconProps} />;
    }
  };

  const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);

  return (
    <span
      className={`badge badge-severity badge-${normalized} ${size === 'sm' ? 'badge-sm' : ''}`}
      role="status"
      aria-label={`Severity: ${label}`}
    >
      {renderIcon()}
      <span>{label}</span>
    </span>
  );
};
