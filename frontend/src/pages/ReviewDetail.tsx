import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Clock,
  ExternalLink,
  FileCode2,
  Filter,
  GitBranch,
  GitCommit,
  GitPullRequest,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Trash2,
  X,
} from 'lucide-react';
import { apiClient, ApiClientError } from '../api/client';
import type {
  Category,
  FindingOut,
  FindingStatus,
  ReviewOut,
  Severity,
  Source,
} from '../types/api';
import { ALL_CATEGORIES } from '../types/api';
import { usePolling } from '../hooks/usePolling';
import { SeverityBadge } from '../components/badges/SeverityBadge';
import { SourceBadge } from '../components/badges/SourceBadge';
import { StatusPill } from '../components/badges/StatusPill';
import { ConfidenceMeter } from '../components/common/ConfidenceMeter';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { EmptyState } from '../components/common/EmptyState';
import { ErrorBanner } from '../components/common/ErrorBanner';
import { Pagination } from '../components/common/Pagination';
import { Skeleton, TableSkeleton } from '../components/common/Skeleton';
import { FindingDetailDrawer } from '../components/findings/FindingDetailDrawer';
import { ProgressStepper } from '../components/workflow/ProgressStepper';

export const ReviewDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [review, setReview] = useState<ReviewOut | null>(null);
  const [findings, setFindings] = useState<FindingOut[]>([]);
  const [totalFindings, setTotalFindings] = useState<number>(0);
  const [selectedFinding, setSelectedFinding] = useState<FindingOut | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  // Loading & Error states
  const [isLoadingReview, setIsLoadingReview] = useState(true);
  const [isLoadingFindings, setIsLoadingFindings] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isNotFound, setIsNotFound] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  // Filters state
  const [selectedSeverities, setSelectedSeverities] = useState<Severity[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<Category | ''>('');
  const [selectedSource, setSelectedSource] = useState<Source | ''>('');
  const [selectedStatus, setSelectedStatus] = useState<FindingStatus | ''>('');
  const [filePathFilter, setFilePathFilter] = useState('');
  const [minConfidence, setMinConfidence] = useState<number>(0);
  const [sortBy, setSortBy] = useState<'severity' | 'confidence' | 'file'>('severity');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const pageSize = 25;

  // Polling function: accepts AbortSignal, stops on 404/completion, continues on transient network error
  const pollReview = useCallback(
    async (signal: AbortSignal): Promise<boolean> => {
      if (!id) return false;
      try {
        const data = await apiClient.getReview(id, { signal });
        if (signal.aborted) return false;
        setReview(data);
        if (data.status === 'completed' || data.status === 'failed') {
          return false;
        }
        return true;
      } catch (err) {
        if (signal.aborted) return false;
        if (err instanceof ApiClientError && err.status === 404) {
          setIsNotFound(true);
          return false;
        }
        // Recoverable network error: return true to keep polling
        return true;
      }
    },
    [id]
  );

  // Initial and trigger-based load for Review
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();

    apiClient
      .getReview(id, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setReview(data);
          setErrorMessage(null);
          setIsNotFound(false);
          setIsLoadingReview(false);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          if (err instanceof ApiClientError && err.status === 404) {
            setIsNotFound(true);
          } else {
            setErrorMessage(
              err instanceof Error ? err.message : 'Failed to retrieve review details.'
            );
          }
          setIsLoadingReview(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [id, refreshTrigger]);

  // Load findings whenever filters or review transition out of queued
  const reviewStatus = review?.status;
  useEffect(() => {
    if (!id || reviewStatus === 'queued' || isNotFound) return;
    const controller = new AbortController();

    apiClient
      .listReviewFindings(
        id,
        {
          severity: selectedSeverities.length > 0 ? selectedSeverities : undefined,
          category: selectedCategory ? selectedCategory : undefined,
          source: selectedSource ? selectedSource : undefined,
          status: selectedStatus ? selectedStatus : undefined,
          file_path: filePathFilter.trim() || undefined,
          min_confidence: minConfidence > 0 ? minConfidence : undefined,
          sort_by: sortBy,
          sort_order: sortOrder,
          page,
          page_size: pageSize,
        },
        { signal: controller.signal }
      )
      .then((data) => {
        if (!controller.signal.aborted) {
          setFindings(data.items);
          setTotalFindings(data.total);
          setIsLoadingFindings(false);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setErrorMessage(
            err instanceof Error ? err.message : 'Failed to load review findings.'
          );
          setIsLoadingFindings(false);
        }
      });

    return () => {
      controller.abort();
    };
  }, [
    id,
    reviewStatus,
    isNotFound,
    selectedSeverities,
    selectedCategory,
    selectedSource,
    selectedStatus,
    filePathFilter,
    minConfidence,
    sortBy,
    sortOrder,
    page,
    refreshTrigger,
  ]);

  // Auto-polling for active reviews (runs every 2000ms while queued/running)
  const isPollingActive =
    !isNotFound &&
    review !== null &&
    (review.status === 'queued' || review.status === 'running');
  usePolling(pollReview, 2000, isPollingActive);

  // Toggle severity filter item
  const handleToggleSeverity = (sev: Severity) => {
    setPage(1);
    setIsLoadingFindings(true);
    setSelectedSeverities((prev) =>
      prev.includes(sev) ? prev.filter((s) => s !== sev) : [...prev, sev]
    );
  };

  const handleClearFilters = () => {
    setSelectedSeverities([]);
    setSelectedCategory('');
    setSelectedSource('');
    setSelectedStatus('');
    setFilePathFilter('');
    setMinConfidence(0);
    setSortBy('severity');
    setSortOrder('desc');
    setPage(1);
    setIsLoadingFindings(true);
  };

  const handleManualRefresh = () => {
    setIsLoadingReview(true);
    setIsLoadingFindings(true);
    setRefreshTrigger((prev) => prev + 1);
  };

  // Delete review handler
  const handleDeleteReview = async () => {
    if (!id) return;
    setIsDeleting(true);
    try {
      await apiClient.deleteReview(id);
      navigate('/history');
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to delete review.'
      );
      setIsDeleting(false);
      setIsDeleteDialogOpen(false);
    }
  };

  // Inspect finding
  const handleInspectFinding = (finding: FindingOut) => {
    setSelectedFinding(finding);
    setIsDrawerOpen(true);
  };

  // Handle finding triage update from drawer
  const handleFindingUpdated = (updated: FindingOut) => {
    setFindings((prev) =>
      prev.map((f) => (f.id === updated.id ? updated : f))
    );
    setSelectedFinding(updated);
    // Refresh review to update summary counters
    const controller = new AbortController();
    void pollReview(controller.signal);
  };

  if (isLoadingReview && !review && !isNotFound) {
    return (
      <div className="review-detail-loading">
        <div className="flex-align-center gap-2 mb-4">
          <Skeleton width="120px" height="24px" />
        </div>
        <Skeleton width="60%" height="36px" className="mb-2" />
        <Skeleton width="40%" height="20px" className="mb-6" />
        <TableSkeleton rows={6} cols={5} />
      </div>
    );
  }

  if (isNotFound) {
    return (
      <div className="page-review-detail">
        <EmptyState
          title="404 — Review Not Found"
          description={`Review with ID "${id}" does not exist in the database or has been deleted.`}
          action={
            <Link to="/" className="btn btn-primary">
              <ArrowLeft size={16} />
              <span>Return to Dashboard</span>
            </Link>
          }
        />
      </div>
    );
  }

  if (!review) {
    return (
      <EmptyState
        title="Review Not Found"
        description="The requested review could not be found or may have been deleted."
        action={
          <Link to="/" className="btn btn-primary">
            <ArrowLeft size={16} />
            <span>Return to Dashboard</span>
          </Link>
        }
      />
    );
  }

  const isCompleted = review.status === 'completed';
  const isFailed = review.status === 'failed';
  const summary = review.findings_summary || {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    total: 0,
  };

  const hasActiveFilters =
    selectedSeverities.length > 0 ||
    selectedCategory !== '' ||
    selectedSource !== '' ||
    selectedStatus !== '' ||
    filePathFilter.trim() !== '' ||
    minConfidence > 0;

  return (
    <div className="page-review-detail">
      {/* Back button and navigation */}
      <div className="detail-top-nav">
        <Link to="/" className="back-link">
          <ArrowLeft size={16} />
          <span>Back to Dashboard</span>
        </Link>
        <div className="detail-top-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleManualRefresh}
            title="Refresh current review"
            aria-label="Refresh review"
          >
            <RefreshCw size={14} className={isLoadingFindings ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>
          <Link
            to={`/reviews/${review.id}/files`}
            className="btn btn-primary btn-sm"
            title="View changed files and diff"
          >
            <FileCode2 size={15} />
            <span>Files & Diff ({review.files_changed})</span>
          </Link>
          <button
            type="button"
            className="btn btn-danger-outline btn-sm"
            onClick={() => setIsDeleteDialogOpen(true)}
            aria-label="Delete review"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {errorMessage && (
        <ErrorBanner
          message={errorMessage}
          onDismiss={() => setErrorMessage(null)}
        />
      )}

      {/* Main PR Header Card */}
      <div className="card review-header-card">
        <div className="review-header-main">
          <div className="flex-align-center gap-2 mb-1">
            <StatusPill status={review.status} />
            <a
              href={review.pr_url}
              target="_blank"
              rel="noreferrer"
              className="repo-link text-sm font-mono"
            >
              <span>{review.owner}/{review.repo}#{review.pr_number}</span>
              <ExternalLink size={12} aria-hidden="true" />
            </a>
          </div>

          <h1 className="review-pr-title">
            {review.pr_title || `Pull Request #${review.pr_number}`}
          </h1>

          <div className="review-meta-row">
            <span className="meta-badge">
              <GitPullRequest size={13} aria-hidden="true" />
              <span>Author: @{review.pr_author || 'unknown'}</span>
            </span>
            {review.base_sha && review.head_sha && (
              <span className="meta-badge font-mono" title={`Base: ${review.base_sha} -> Head: ${review.head_sha}`}>
                <GitCommit size={13} aria-hidden="true" />
                <span>{review.base_sha.slice(0, 7)}...{review.head_sha.slice(0, 7)}</span>
              </span>
            )}
            <span className="meta-badge">
              <GitBranch size={13} aria-hidden="true" />
              <span>+{review.additions} / -{review.deletions} in {review.files_changed} files</span>
            </span>
            <span className="meta-badge">
              <Clock size={13} aria-hidden="true" />
              <span>
                {new Date(review.created_at).toLocaleTimeString(undefined, {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
            </span>
          </div>
        </div>

        {/* Workflow Execution Stepper */}
        <div className="review-stepper-section">
          <ProgressStepper
            currentStage={review.current_stage}
            status={review.status}
            progressPercent={review.progress_percent}
          />
        </div>
      </div>

      {/* Error or Warnings Notice */}
      {isFailed && review.error_message && (
        <div className="review-alert alert-failed mb-4" role="alert">
          <AlertTriangle size={18} className="text-danger flex-shrink-0" />
          <div>
            <span className="font-semibold text-danger">Review Workflow Failed: </span>
            <span>{review.error_message}</span>
            {review.error_code && (
              <span className="font-mono text-xs ml-2 text-muted">
                (Code: {review.error_code})
              </span>
            )}
          </div>
        </div>
      )}

      {review.warnings && review.warnings.length > 0 && (
        <div className="review-alert alert-warning mb-4" role="region" aria-label="Review Warnings">
          <AlertTriangle size={18} className="text-warning flex-shrink-0" />
          <div>
            <span className="font-semibold text-warning">Notice: </span>
            <span>{review.warnings.join(' ')}</span>
            {review.truncated && (
              <span className="ml-1 text-sm font-medium">
                (PR diff exceeded size limits and was truncated for processing safety)
              </span>
            )}
          </div>
        </div>
      )}

      {/* Findings Section */}
      <div className="card findings-section-card">
        {/* Severity summary counter cards */}
        <div className="findings-summary-strip">
          <div className="summary-item">
            <span className="summary-count font-bold">{summary.total}</span>
            <span className="summary-label">Total Findings</span>
          </div>
          <div className="summary-divider" />
          <div className="summary-item text-danger">
            <span className="summary-count font-bold">{summary.critical}</span>
            <span className="summary-label">Critical</span>
          </div>
          <div className="summary-divider" />
          <div className="summary-item text-warning">
            <span className="summary-count font-bold">{summary.high}</span>
            <span className="summary-label">High</span>
          </div>
          <div className="summary-divider" />
          <div className="summary-item text-info">
            <span className="summary-count font-bold">{summary.medium}</span>
            <span className="summary-label">Medium</span>
          </div>
          <div className="summary-divider" />
          <div className="summary-item text-muted">
            <span className="summary-count font-bold">{summary.low}</span>
            <span className="summary-label">Low</span>
          </div>
          {review.dropped_findings_count > 0 && (
            <>
              <div className="summary-divider" />
              <div
                className="summary-item text-muted"
                title="Invalid or unverifiable findings discarded during evidence verification"
              >
                <span className="summary-count font-bold">{review.dropped_findings_count}</span>
                <span className="summary-label">Dropped (Unverified)</span>
              </div>
            </>
          )}
        </div>

        {/* Filter and Search Bar */}
        <div className="findings-filter-toolbar">
          <div className="filter-row-primary">
            {/* Severity multi-buttons */}
            <div className="severity-toggle-group" role="group" aria-label="Filter by severity">
              {(['critical', 'high', 'medium', 'low'] as const).map((sev) => {
                const isSelected = selectedSeverities.includes(sev);
                return (
                  <button
                    key={sev}
                    type="button"
                    className={`btn-severity-toggle btn-toggle-${sev} ${isSelected ? 'active' : ''}`}
                    onClick={() => handleToggleSeverity(sev)}
                    aria-pressed={isSelected}
                  >
                    <span className="capitalize">{sev}</span>
                  </button>
                );
              })}
            </div>

            {/* Category dropdown */}
            <select
              className="form-select filter-select"
              value={selectedCategory}
              onChange={(e) => {
                setPage(1);
                setIsLoadingFindings(true);
                setSelectedCategory(e.target.value as Category | '');
              }}
              aria-label="Filter by category"
            >
              <option value="">All Categories</option>
              {ALL_CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>
                  {cat.replace('_', ' ')}
                </option>
              ))}
            </select>

            {/* Source dropdown */}
            <select
              className="form-select filter-select"
              value={selectedSource}
              onChange={(e) => {
                setPage(1);
                setIsLoadingFindings(true);
                setSelectedSource(e.target.value as Source | '');
              }}
              aria-label="Filter by detection source"
            >
              <option value="">All Sources</option>
              <option value="ai">AI Review</option>
              <option value="static">Static Analysis</option>
            </select>

            {/* Triage status dropdown */}
            <select
              className="form-select filter-select"
              value={selectedStatus}
              onChange={(e) => {
                setPage(1);
                setIsLoadingFindings(true);
                setSelectedStatus(e.target.value as FindingStatus | '');
              }}
              aria-label="Filter by triage status"
            >
              <option value="">All Statuses</option>
              <option value="open">Open</option>
              <option value="approved">Approved</option>
              <option value="dismissed">Dismissed</option>
            </select>

            {/* File search */}
            <div className="filter-search-box">
              <Search size={14} className="search-icon" aria-hidden="true" />
              <input
                type="text"
                className="form-input filter-search-input"
                placeholder="Filter by file path..."
                value={filePathFilter}
                onChange={(e) => {
                  setPage(1);
                  setIsLoadingFindings(true);
                  setFilePathFilter(e.target.value);
                }}
              />
              {filePathFilter && (
                <button
                  type="button"
                  className="search-clear-btn"
                  onClick={() => {
                    setIsLoadingFindings(true);
                    setFilePathFilter('');
                  }}
                  aria-label="Clear file search"
                >
                  <X size={12} />
                </button>
              )}
            </div>

            {hasActiveFilters && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={handleClearFilters}
              >
                <span>Clear All</span>
              </button>
            )}
          </div>

          {/* Secondary Sorting and Confidence Bar */}
          <div className="filter-row-secondary">
            <div className="confidence-slider-group">
              <SlidersHorizontal size={14} className="text-muted" aria-hidden="true" />
              <span className="text-xs text-muted">Min Confidence:</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={minConfidence}
                onChange={(e) => {
                  setPage(1);
                  setIsLoadingFindings(true);
                  setMinConfidence(parseFloat(e.target.value));
                }}
                className="range-slider"
                aria-label="Minimum confidence threshold slider"
              />
              <span className="text-xs font-mono font-semibold">
                {Math.round(minConfidence * 100)}%
              </span>
            </div>

            <div className="sort-controls">
              <span className="text-xs text-muted">Sort:</span>
              <select
                className="form-select form-select-sm"
                value={sortBy}
                onChange={(e) => {
                  setPage(1);
                  setIsLoadingFindings(true);
                  setSortBy(e.target.value as 'severity' | 'confidence' | 'file');
                }}
                aria-label="Sort findings by"
              >
                <option value="severity">Severity</option>
                <option value="confidence">Confidence</option>
                <option value="file">File Path</option>
              </select>
              <select
                className="form-select form-select-sm"
                value={sortOrder}
                onChange={(e) => {
                  setPage(1);
                  setIsLoadingFindings(true);
                  setSortOrder(e.target.value as 'asc' | 'desc');
                }}
                aria-label="Sort order"
              >
                <option value="desc">Desc</option>
                <option value="asc">Asc</option>
              </select>
            </div>
          </div>
        </div>

        {/* Findings Table / List */}
        {isLoadingFindings ? (
          <div className="p-4">
            <TableSkeleton rows={5} cols={5} />
          </div>
        ) : findings.length === 0 ? (
          <EmptyState
            title={
              hasActiveFilters
                ? 'No findings match current filters'
                : isCompleted
                  ? 'No issues detected'
                  : 'Review in progress'
            }
            description={
              hasActiveFilters
                ? 'Try adjusting your severity, category, or confidence filters.'
                : isCompleted
                  ? 'Sentinel completed the review and found no security or quality issues.'
                  : 'Findings will appear here once analysis and validation stages finish.'
            }
            icon={<Filter size={32} />}
            action={
              hasActiveFilters ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleClearFilters}
                >
                  Clear Filters
                </button>
              ) : undefined
            }
          />
        ) : (
          <div className="table-responsive">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Severity</th>
                  <th scope="col">Issue / Rule</th>
                  <th scope="col">Location</th>
                  <th scope="col">Source</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {findings.map((f) => (
                  <tr
                    key={f.id}
                    className="cursor-pointer hover-row"
                    onClick={() => handleInspectFinding(f)}
                  >
                    <td>
                      <SeverityBadge severity={f.severity} size="sm" />
                    </td>
                    <td>
                      <div className="finding-title-cell">
                        <span className="finding-table-title font-semibold">
                          {f.title}
                        </span>
                        <span className="finding-table-category text-muted text-xs">
                          {f.category.replace('_', ' ')} &bull; {f.rule_id}
                        </span>
                      </div>
                    </td>
                    <td className="font-mono text-xs">
                      <span>{f.file_path}:{f.line_start}</span>
                    </td>
                    <td>
                      <SourceBadge source={f.source} toolName={f.tool_name} />
                    </td>
                    <td>
                      <ConfidenceMeter confidence={f.confidence} size="sm" />
                    </td>
                    <td>
                      <StatusPill status={f.status} size="sm" />
                    </td>
                    <td className="text-right" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleInspectFinding(f)}
                        aria-label={`Inspect finding: ${f.title}`}
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {totalFindings > pageSize && (
          <div className="card-pagination-bar">
            <Pagination
              page={page}
              pageSize={pageSize}
              total={totalFindings}
              onPageChange={(p) => {
                setIsLoadingFindings(true);
                setPage(p);
              }}
            />
          </div>
        )}
      </div>

      {/* Slide-over Finding Detail Drawer */}
      <FindingDetailDrawer
        finding={selectedFinding}
        isOpen={isDrawerOpen}
        onClose={() => {
          setIsDrawerOpen(false);
          setSelectedFinding(null);
        }}
        onFindingUpdated={handleFindingUpdated}
      />

      {/* Delete Review Dialog */}
      <ConfirmDialog
        isOpen={isDeleteDialogOpen}
        title="Delete Review"
        message={`Are you sure you want to delete this review for ${review.owner}/${review.repo}#${review.pr_number}? All findings and file records will be permanently removed.`}
        confirmLabel="Delete Review"
        tone="danger"
        isLoading={isDeleting}
        onConfirm={handleDeleteReview}
        onCancel={() => setIsDeleteDialogOpen(false)}
      />
    </div>
  );
};
