import React from 'react';
import { Bot, Terminal } from 'lucide-react';
import type { Source } from '../../types/api';

interface SourceBadgeProps {
  source: Source;
  toolName?: string;
  ruleId?: string;
}

export const SourceBadge: React.FC<SourceBadgeProps> = ({
  source,
  toolName,
  ruleId,
}) => {
  const isAi = source === 'ai';
  const icon = isAi ? (
    <Bot size={13} className="badge-icon" aria-hidden="true" />
  ) : (
    <Terminal size={13} className="badge-icon" aria-hidden="true" />
  );

  let label = isAi ? 'AI Review' : 'Static Analysis';
  if (toolName && toolName !== 'gemini' && toolName !== 'ai') {
    label = toolName.toUpperCase();
  }

  const tooltipText = ruleId ? `Rule: ${ruleId}` : undefined;

  return (
    <span
      className={`badge badge-source badge-source-${isAi ? 'ai' : 'static'}`}
      title={tooltipText}
      role="note"
      aria-label={`Source: ${label}`}
    >
      {icon}
      <span>{label}</span>
    </span>
  );
};
