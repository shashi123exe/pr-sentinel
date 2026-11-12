import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertOctagon,
  ArrowRight,
  ExternalLink,
  GitPullRequest,
  PlusCircle,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { ReviewListItem, ReviewStatus } from '../types/api';
import { StatusPill } from '../components/badges/StatusPill';
import { ErrorBanner } from '../components/common/ErrorBanner';
import { StatCard } from '../components/common/StatCard';
import { EmptyState } from '../components/common/EmptyState';
import { TableSkeleton } from '../components/common/Skeleton';

export const Dashboard: React.FC = () => {
  const [reviews, setReviews] = useState<ReviewListItem[]>([]);
  const [tableTotal, setTableTotal] = useState<number>(0);
  const [globalTotalCount, setGlobalTotalCount] = useState<number>(0);
  const [recentSample, setRecentSample] = useState<ReviewListItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | ''>('');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  useEffect(() => {
    const active = { current: true };

    // Fetch both global recent sample for accurate KPI metrics and filtered list for table
    Promise.all([
      apiClient.listReviews({ page: 1, page_size: 20 }),
      apiClient.listReviews({
        status: statusFilter ? statusFilter : undefined,
        page: 1,
        page_size: 10,
      }),
    ])
      .then(([globalData, tableData]) => {
        if (active.current) {
          setGlobalTotalCount(globalData.total);
          setRecentSample(globalData.items);
          setReviews(tableData.items);
          setTableTotal(tableData.total);
          setErrorMessage(null);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (active.current) {
          setErrorMessage(
            err instanceof Error ? err.message : 'Failed to load dashboard data.'
          );
          setIsLoading(false);
        }
      });

    return () => {
      active.current = false;
    };
  }, [statusFilter, refreshTrigger]);

  const handleManualRefresh = () => {
    setIsLoading(true);
    setRefreshTrigger((prev) => prev + 1);
  };

  // Accurate metric aggregation across the loaded recent reviews sample
  const sampleSize = recentSample.length;
  const recentFindingsTotal = recentSample.reduce(
    (acc, r) => acc + (r.findings_summary?.total || 0),
    0
  );
  const recentCriticalTotal = recentSample.reduce(
    (acc, r) => acc + (r.findings_summary?.critical || 0),
    0
  );
  const avgFindingsPerPR =
    sampleSize > 0 ? (recentFindingsTotal / sampleSize).toFixed(1) : '0';

  return (
    <div className="page-dashboard">
      {/* Top Header */}
      <div className="page-header-row">
        <div>
          <h1 className="page-title">Dashboard Overview</h1>
          <p className="page-subtitle">
            Autonomous GitHub pull request security & quality sentinel
          </p>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleManualRefresh}
            disabled={isLoading}
            aria-label="Refresh reviews"
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
          onRetry={handleManualRefresh}
          onDismiss={() => setErrorMessage(null)}
        />
      )}

      {/* Metrics Stat Cards with honest, accurate scoping */}
      <div className="stats-grid">
        <StatCard
          title="Total Reviews"
          value={globalTotalCount}
          subtitle="All recorded PR reviews"
          icon={<GitPullRequest size={20} className="text-primary" />}
        />
        <StatCard
          title="Detected Issues"
          value={recentFindingsTotal}
          subtitle={
            sampleSize > 0
              ? `Across ${sampleSize} recent reviews`
              : 'No review records'
          }
          icon={<ShieldAlert size={20} className="text-warning" />}
          tone="warning"
        />
        <StatCard
          title="Critical Issues"
          value={recentCriticalTotal}
          subtitle={
            sampleSize > 0
              ? `Across ${sampleSize} recent reviews`
              : 'No review records'
          }
          icon={<AlertOctagon size={20} className="text-danger" />}
          tone={recentCriticalTotal > 0 ? 'critical' : 'default'}
        />
        <StatCard
          title="Avg Findings / PR"
          value={avgFindingsPerPR}
          subtitle={
            sampleSize > 0
              ? `Per recent PR (${sampleSize} evaluated)`
              : 'No review records'
          }
          icon={<ArrowRight size={20} className="text-info" />}
        />
      </div>

      {/* Recent Reviews Section */}
      <div className="dashboard-section card">
        <div className="card-header-bar">
          <div className="flex-align-center gap-3">
            <h2 className="card-title">Recent PR Reviews</h2>
            <span className="badge badge-secondary">{tableTotal}</span>
          </div>

          {/* Quick status tabs */}
          <div className="filter-tabs" role="tablist" aria-label="Filter reviews by status">
            {(
              [
                { label: 'All', value: '' },
                { label: 'Completed', value: 'completed' },
                { label: 'Running', value: 'running' },
                { label: 'Queued', value: 'queued' },
                { label: 'Failed', value: 'failed' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.value}
                type="button"
                role="tab"
                aria-selected={statusFilter === tab.value}
                className={`tab-btn ${statusFilter === tab.value ? 'active' : ''}`}
                onClick={() => {
                  setIsLoading(true);
                  setStatusFilter(tab.value as ReviewStatus | '');
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {isLoading ? (
          <div className="p-4">
            <TableSkeleton rows={5} cols={6} />
          </div>
        ) : reviews.length === 0 ? (
          <EmptyState
            title="No reviews found"
            description={
              statusFilter
                ? `No reviews match the status "${statusFilter}".`
                : 'No pull requests have been reviewed yet. Start your first automated PR review.'
            }
            action={
              <Link to="/new" className="btn btn-primary">
                <PlusCircle size={15} />
                <span>Submit PR for Review</span>
              </Link>
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
                  <th scope="col">Submitted</th>
                  <th scope="col" className="text-right">Action</th>
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
                          <span className="pr-title-text">{rev.pr_title || 'PR Review'}</span>
                        </Link>
                      </td>
                      <td className="font-mono text-sm">
                        <a
                          href={rev.pr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="repo-link"
                          title="Open PR on GitHub"
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
                            <span className="badge-count badge-critical" title="Critical issues">
                              {summary.critical}C
                            </span>
                          )}
                          {summary.high > 0 && (
                            <span className="badge-count badge-high" title="High issues">
                              {summary.high}H
                            </span>
                          )}
                          {summary.medium > 0 && (
                            <span className="badge-count badge-medium" title="Medium issues">
                              {summary.medium}M
                            </span>
                          )}
                          {summary.low > 0 && (
                            <span className="badge-count badge-low" title="Low issues">
                              {summary.low}L
                            </span>
                          )}
                          {summary.total === 0 && (
                            <span className="text-muted text-xs">0 findings</span>
                          )}
                        </div>
                      </td>
                      <td className="text-muted text-sm whitespace-nowrap">
                        {new Date(rev.created_at).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </td>
                      <td className="text-right">
                        <Link
                          to={`/reviews/${rev.id}`}
                          className="btn btn-secondary btn-sm"
                          aria-label={`View review details for PR #${rev.pr_number}`}
                        >
                          <span>View</span>
                          <ArrowRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="card-footer-bar">
          <Link to="/history" className="view-all-link">
            <span>View all past reviews in history</span>
            <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </div>
  );
};
