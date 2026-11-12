import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight,
  ExternalLink,
  History as HistoryIcon,
  PlusCircle,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { ReviewListItem, ReviewStatus } from '../types/api';
import { StatusPill } from '../components/badges/StatusPill';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { EmptyState } from '../components/common/EmptyState';
import { ErrorBanner } from '../components/common/ErrorBanner';
import { Pagination } from '../components/common/Pagination';
import { TableSkeleton } from '../components/common/Skeleton';

export const History: React.FC = () => {
  const [reviews, setReviews] = useState<ReviewListItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  // Filters
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | ''>('');
  const [ownerFilter, setOwnerFilter] = useState('');
  const [repoFilter, setRepoFilter] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 20;

  // Delete state
  const [deleteTarget, setDeleteTarget] = useState<ReviewListItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    const active = { current: true };

    apiClient
      .listReviews({
        status: statusFilter || undefined,
        owner: ownerFilter.trim() || undefined,
        repo: repoFilter.trim() || undefined,
        page,
        page_size: pageSize,
      })
      .then((data) => {
        if (active.current) {
          setReviews(data.items);
          setTotalCount(data.total);
          setErrorMessage(null);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (active.current) {
          setErrorMessage(
            err instanceof Error ? err.message : 'Failed to retrieve review history.'
          );
          setIsLoading(false);
        }
      });

    return () => {
      active.current = false;
    };
  }, [statusFilter, ownerFilter, repoFilter, page, refreshTrigger]);

  const handleManualRefresh = () => {
    setIsLoading(true);
    setRefreshTrigger((prev) => prev + 1);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await apiClient.deleteReview(deleteTarget.id);
      setDeleteTarget(null);
      handleManualRefresh();
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to delete review record.'
      );
    } finally {
      setIsDeleting(false);
    }
  };

  const handleClearFilters = () => {
    setStatusFilter('');
    setOwnerFilter('');
    setRepoFilter('');
    setPage(1);
  };

  const hasActiveFilters =
    statusFilter !== '' || ownerFilter.trim() !== '' || repoFilter.trim() !== '';

  return (
    <div className="page-history">
      <div className="page-header-row">
        <div>
          <h1 className="page-title">Review History</h1>
          <p className="page-subtitle">
            Inspect past pull request evaluations, results, and triaged findings
          </p>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleManualRefresh}
            disabled={isLoading}
            aria-label="Refresh history"
          >
            <RefreshCw size={15} className={isLoading ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>
          <Link to="/new" className="btn btn-primary">
            <PlusCircle size={16} />
            <span>New Review</span>
          </Link>
        </div>
      </div>

      {errorMessage && (
        <ErrorBanner
          message={errorMessage}
          onDismiss={() => setErrorMessage(null)}
          onRetry={handleManualRefresh}
        />
      )}

      {/* History Filters Card */}
      <div className="card history-filters-card mb-4">
        <div className="filter-grid">
          <div className="filter-col">
            <label className="form-label text-xs">Status</label>
            <select
              className="form-select"
              value={statusFilter}
              onChange={(e) => {
                setPage(1);
                setIsLoading(true);
                setStatusFilter(e.target.value as ReviewStatus | '');
              }}
              aria-label="Filter by status"
            >
              <option value="">All Statuses</option>
              <option value="completed">Completed</option>
              <option value="running">Running</option>
              <option value="queued">Queued</option>
              <option value="failed">Failed</option>
            </select>
          </div>

          <div className="filter-col">
            <label className="form-label text-xs">Repository Owner</label>
            <div className="input-with-icon">
              <Search size={14} className="input-leading-icon" aria-hidden="true" />
              <input
                type="text"
                className="form-input"
                placeholder="e.g. google"
                value={ownerFilter}
                onChange={(e) => {
                  setPage(1);
                  setIsLoading(true);
                  setOwnerFilter(e.target.value);
                }}
              />
            </div>
          </div>

          <div className="filter-col">
            <label className="form-label text-xs">Repository Name</label>
            <div className="input-with-icon">
              <Search size={14} className="input-leading-icon" aria-hidden="true" />
              <input
                type="text"
                className="form-input"
                placeholder="e.g. pr-sentinel"
                value={repoFilter}
                onChange={(e) => {
                  setPage(1);
                  setIsLoading(true);
                  setRepoFilter(e.target.value);
                }}
              />
            </div>
          </div>

          {hasActiveFilters && (
            <div className="filter-col filter-actions-col">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={handleClearFilters}
              >
                <X size={14} />
                <span>Clear Filters</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Reviews Table */}
      <div className="card">
        {isLoading ? (
          <div className="p-4">
            <TableSkeleton rows={8} cols={7} />
          </div>
        ) : reviews.length === 0 ? (
          <EmptyState
            title="No reviews found"
            description={
              hasActiveFilters
                ? 'No review records match your filter criteria.'
                : 'No reviews exist in the database yet.'
            }
            icon={<HistoryIcon size={36} />}
            action={
              hasActiveFilters ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleClearFilters}
                >
                  Clear Filters
                </button>
              ) : (
                <Link to="/new" className="btn btn-primary">
                  <PlusCircle size={15} />
                  <span>Launch Review</span>
                </Link>
              )
            }
          />
        ) : (
          <div className="table-responsive">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Pull Request</th>
                  <th scope="col">Repository</th>
                  <th scope="col">Author</th>
                  <th scope="col">Status</th>
                  <th scope="col">Findings</th>
                  <th scope="col">Changes</th>
                  <th scope="col">Date</th>
                  <th scope="col" className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {reviews.map((rev) => {
                  const summary = rev.findings_summary || {
                    critical: 0,
                    high: 0,
                    medium: 0,
                    low: 0,
                    total: 0,
                  };

                  return (
                    <tr key={rev.id}>
                      <td className="font-medium">
                        <Link
                          to={`/reviews/${rev.id}`}
                          className="table-pr-title"
                          title={rev.pr_title}
                        >
                          <span className="pr-number">#{rev.pr_number}</span>
                          <span className="pr-title-text">
                            {rev.pr_title || 'PR Review'}
                          </span>
                        </Link>
                      </td>
                      <td className="font-mono text-sm">
                        <a
                          href={rev.pr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="repo-link"
                          title="Open on GitHub"
                        >
                          <span>{rev.owner}/{rev.repo}</span>
                          <ExternalLink size={12} aria-hidden="true" />
                        </a>
                      </td>
                      <td>
                        <span className="author-tag">@{rev.pr_author || 'unknown'}</span>
                      </td>
                      <td>
                        <StatusPill status={rev.status} size="sm" />
                      </td>
                      <td>
                        <div className="findings-pill-group">
                          {summary.critical > 0 && (
                            <span className="badge-count badge-critical">
                              {summary.critical}C
                            </span>
                          )}
                          {summary.high > 0 && (
                            <span className="badge-count badge-high">
                              {summary.high}H
                            </span>
                          )}
                          {summary.medium > 0 && (
                            <span className="badge-count badge-medium">
                              {summary.medium}M
                            </span>
                          )}
                          {summary.low > 0 && (
                            <span className="badge-count badge-low">
                              {summary.low}L
                            </span>
                          )}
                          {summary.total === 0 && (
                            <span className="text-muted text-xs">0 issues</span>
                          )}
                        </div>
                      </td>
                      <td className="font-mono text-xs whitespace-nowrap">
                        <span className="text-success">+{rev.additions}</span>{' '}
                        <span className="text-danger">-{rev.deletions}</span>
                      </td>
                      <td className="text-muted text-sm whitespace-nowrap">
                        {new Date(rev.created_at).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </td>
                      <td className="text-right">
                        <div className="table-actions-cell">
                          <Link
                            to={`/reviews/${rev.id}`}
                            className="btn btn-secondary btn-sm"
                            aria-label={`View review for PR #${rev.pr_number}`}
                          >
                            <span>View</span>
                            <ArrowRight size={13} />
                          </Link>
                          <button
                            type="button"
                            className="btn-icon text-muted hover-danger"
                            onClick={() => setDeleteTarget(rev)}
                            aria-label={`Delete review for PR #${rev.pr_number}`}
                            title="Delete review"
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {totalCount > pageSize && (
          <div className="card-pagination-bar">
            <Pagination
              page={page}
              pageSize={pageSize}
              total={totalCount}
              onPageChange={(p) => {
                setIsLoading(true);
                setPage(p);
              }}
            />
          </div>
        )}
      </div>

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title="Delete Review Record"
        message={
          deleteTarget
            ? `Are you sure you want to delete the review for ${deleteTarget.owner}/${deleteTarget.repo}#${deleteTarget.pr_number}? This action cannot be undone.`
            : ''
        }
        confirmLabel="Delete"
        tone="danger"
        isLoading={isDeleting}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
};
