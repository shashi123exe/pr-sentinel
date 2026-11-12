import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle,
  FileCode,
  RotateCcw,
  Tag,
  X,
  XCircle,
} from 'lucide-react';
import { apiClient } from '../../api/client';
import type { FindingOut, FindingStatus } from '../../types/api';
import { SeverityBadge } from '../badges/SeverityBadge';
import { SourceBadge } from '../badges/SourceBadge';
import { StatusPill } from '../badges/StatusPill';
import { CodeBlock } from '../common/CodeBlock';
import { ConfidenceMeter } from '../common/ConfidenceMeter';

interface FindingDetailDrawerProps {
  finding: FindingOut | null;
  isOpen: boolean;
  onClose: () => void;
  onFindingUpdated?: (updated: FindingOut) => void;
}

interface ContentProps {
  finding: FindingOut;
  onClose: () => void;
  onFindingUpdated?: (updated: FindingOut) => void;
}

const FindingDetailContent: React.FC<ContentProps> = ({
  finding,
  onClose,
  onFindingUpdated,
}) => {
  const [currentFinding, setCurrentFinding] = useState<FindingOut>(finding);
  const [isUpdating, setIsUpdating] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const handleTriage = async (newStatus: FindingStatus) => {
    if (isUpdating || currentFinding.status === newStatus) return;

    const previousFinding = { ...currentFinding };
    // Optimistic update
    const optimisticFinding: FindingOut = {
      ...currentFinding,
      status: newStatus,
      updated_at: new Date().toISOString(),
    };
    setCurrentFinding(optimisticFinding);
    setIsUpdating(true);
    setErrorMessage(null);

    try {
      const persisted = await apiClient.patchFinding(currentFinding.id, {
        status: newStatus,
      });
      setCurrentFinding(persisted);
      onFindingUpdated?.(persisted);
    } catch (err) {
      // Rollback on failure
      setCurrentFinding(previousFinding);
      const msg = err instanceof Error ? err.message : 'Failed to update finding status.';
      setErrorMessage(msg);
    } finally {
      setIsUpdating(false);
    }
  };

  const lineRangeText =
    currentFinding.line_start === currentFinding.line_end
      ? `Line ${currentFinding.line_start}`
      : `Lines ${currentFinding.line_start}–${currentFinding.line_end}`;

  return (
    <div
      className="drawer-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="drawer-container"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
      >
        {/* Drawer Header */}
        <div className="drawer-header">
          <div className="drawer-header-content">
            <div className="drawer-badges-row">
              <SeverityBadge severity={currentFinding.severity} />
              <SourceBadge
                source={currentFinding.source}
                toolName={currentFinding.tool_name}
                ruleId={currentFinding.rule_id}
              />
              <StatusPill status={currentFinding.status} />
              <span className="badge badge-category">
                <Tag size={12} className="badge-icon" aria-hidden="true" />
                <span>{currentFinding.category}</span>
              </span>
            </div>
            <h2 id="drawer-title" className="drawer-title">
              {currentFinding.title}
            </h2>
          </div>
          <button
            type="button"
            className="btn-icon"
            onClick={onClose}
            aria-label="Close drawer"
          >
            <X size={20} />
          </button>
        </div>

        {/* Error notification */}
        {errorMessage && (
          <div className="drawer-error-alert" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Drawer Scrollable Body */}
        <div className="drawer-body">
          {/* Location & Confidence meta */}
          <div className="drawer-meta-card">
            <div className="meta-item">
              <span className="meta-label">File & Location</span>
              <div className="meta-value flex-align-center">
                <FileCode size={15} className="text-muted" aria-hidden="true" />
                <span className="font-mono text-sm">{currentFinding.file_path}</span>
                <span className="meta-separator">:</span>
                <span className="text-sm font-semibold">{lineRangeText}</span>
              </div>
            </div>
            <div className="meta-item">
              <span className="meta-label">Confidence Score</span>
              <div className="meta-value">
                <ConfidenceMeter confidence={currentFinding.confidence} />
              </div>
            </div>
          </div>

          {/* Explanation */}
          <div className="drawer-section">
            <h3 className="section-heading">Explanation</h3>
            <p className="drawer-explanation-text">{currentFinding.explanation}</p>
          </div>

          {/* Evidence Code */}
          <div className="drawer-section">
            <h3 className="section-heading">Evidence</h3>
            <CodeBlock
              code={currentFinding.evidence}
              startLine={currentFinding.line_start}
              filename={currentFinding.file_path}
            />
          </div>

          {/* Suggested Fix (if provided) */}
          {currentFinding.suggested_fix && (
            <div className="drawer-section">
              <h3 className="section-heading">Suggested Fix</h3>
              <CodeBlock
                code={currentFinding.suggested_fix}
                filename="Recommended Change"
              />
            </div>
          )}

          {/* Details & Metadata */}
          <div className="drawer-section">
            <h3 className="section-heading">Technical Metadata</h3>
            <div className="metadata-grid">
              <div className="metadata-cell">
                <span className="cell-label">Rule ID</span>
                <span className="cell-value font-mono">{currentFinding.rule_id}</span>
              </div>
              <div className="metadata-cell">
                <span className="cell-label">Tool Name</span>
                <span className="cell-value font-mono">{currentFinding.tool_name}</span>
              </div>
              <div className="metadata-cell">
                <span className="cell-label">Fingerprint</span>
                <span
                  className="cell-value font-mono text-xs text-muted"
                  title={currentFinding.fingerprint}
                >
                  {currentFinding.fingerprint.slice(0, 16)}...
                </span>
              </div>
              <div className="metadata-cell">
                <span className="cell-label">Detected At</span>
                <span className="cell-value">
                  {new Date(currentFinding.created_at).toLocaleString()}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Triage Actions Footer */}
        <div className="drawer-footer">
          <div className="triage-status-info">
            <span className="text-muted text-sm">Status:</span>
            <span className="font-semibold text-sm capitalize">
              {currentFinding.status}
            </span>
          </div>
          <div className="triage-buttons-group">
            {currentFinding.status !== 'open' && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => handleTriage('open')}
                disabled={isUpdating}
              >
                <RotateCcw size={14} />
                <span>Reopen</span>
              </button>
            )}
            {currentFinding.status !== 'approved' && (
              <button
                type="button"
                className="btn btn-success btn-sm"
                onClick={() => handleTriage('approved')}
                disabled={isUpdating}
                title="Mark as acceptable or reviewed"
              >
                <CheckCircle size={14} />
                <span>Approve</span>
              </button>
            )}
            {currentFinding.status !== 'dismissed' && (
              <button
                type="button"
                className="btn btn-secondary btn-sm btn-dismiss"
                onClick={() => handleTriage('dismissed')}
                disabled={isUpdating}
                title="Dismiss or ignore this issue"
              >
                <XCircle size={14} />
                <span>Dismiss</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export const FindingDetailDrawer: React.FC<FindingDetailDrawerProps> = ({
  finding,
  isOpen,
  onClose,
  onFindingUpdated,
}) => {
  if (!isOpen || !finding) {
    return null;
  }

  return (
    <FindingDetailContent
      key={finding.id}
      finding={finding}
      onClose={onClose}
      onFindingUpdated={onFindingUpdated}
    />
  );
};
