import React from 'react';
import { Check, Loader2, XCircle } from 'lucide-react';
import type { ReviewStatus } from '../../types/api';
import { STAGE_DISPLAY_NAMES } from '../../types/api';

interface ProgressStepperProps {
  currentStage: string;
  status: ReviewStatus;
  progressPercent: number;
}

interface StageStep {
  key: string;
  label: string;
}

const ORDERED_STAGES: StageStep[] = [
  { key: 'fetch_pr', label: 'Fetch PR' },
  { key: 'process_diff', label: 'Process Diff' },
  { key: 'static_analysis', label: 'Static Analysis' },
  { key: 'retrieve_context', label: 'Context Retrieval' },
  { key: 'ai_review', label: 'AI Review' },
  { key: 'validate_findings', label: 'Validate Findings' },
  { key: 'persist_results', label: 'Persist Results' },
];

export const ProgressStepper: React.FC<ProgressStepperProps> = ({
  currentStage,
  status,
  progressPercent,
}) => {
  const isCompleted = status === 'completed';
  const isFailed = status === 'failed';

  // Determine stage index
  const currentIndex = ORDERED_STAGES.findIndex((s) => s.key === currentStage);

  return (
    <div className="stepper-card" role="region" aria-label="Review Workflow Progress">
      <div className="stepper-header">
        <div className="stepper-title-row">
          <span className="stepper-label font-semibold">Workflow Execution</span>
          <span className="stepper-percentage font-mono text-primary font-bold">
            {progressPercent}%
          </span>
        </div>
        <div className="stepper-progress-bar">
          <div
            className={`stepper-progress-fill ${isFailed ? 'bg-danger' : 'bg-primary'}`}
            style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
          />
        </div>
      </div>

      <ol className="stepper-steps-list">
        {ORDERED_STAGES.map((step, idx) => {
          let stepStatus: 'completed' | 'active' | 'pending' | 'failed' = 'pending';

          if (isCompleted) {
            stepStatus = 'completed';
          } else if (isFailed && idx === currentIndex) {
            stepStatus = 'failed';
          } else if (idx < currentIndex) {
            stepStatus = 'completed';
          } else if (idx === currentIndex) {
            stepStatus = 'active';
          } else {
            stepStatus = 'pending';
          }

          return (
            <li
              key={step.key}
              className={`stepper-step step-${stepStatus}`}
              aria-current={stepStatus === 'active' ? 'step' : undefined}
            >
              <div className="step-icon-wrapper" aria-hidden="true">
                {stepStatus === 'completed' && <Check size={14} className="step-check" />}
                {stepStatus === 'active' && (
                  <Loader2 size={14} className="step-spinner animate-spin" />
                )}
                {stepStatus === 'failed' && <XCircle size={14} className="step-fail" />}
                {stepStatus === 'pending' && <span className="step-num">{idx + 1}</span>}
              </div>
              <span className="step-title">
                {STAGE_DISPLAY_NAMES[step.key] || step.label}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
};
