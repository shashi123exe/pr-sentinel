import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle,
  FileCode,
  Filter,
  RefreshCw,
  Search,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { FindingOut, ReviewFileOut, ReviewOut } from '../types/api';
import { DiffViewer } from '../components/diff/DiffViewer';
import { FindingDetailDrawer } from '../components/findings/FindingDetailDrawer';
import { EmptyState } from '../components/common/EmptyState';
import { ErrorBanner } from '../components/common/ErrorBanner';
import { Skeleton } from '../components/common/Skeleton';

export const ReviewFiles: React.FC = () => {
  const { id } = useParams<{ id: string }>();

  const [review, setReview] = useState<ReviewOut | null>(null);
  const [files, setFiles] = useState<ReviewFileOut[]>([]);
  const [findings, setFindings] = useState<FindingOut[]>([]);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [includeSkipped, setIncludeSkipped] = useState(true);

  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  // Finding drawer state
  const [selectedFinding, setSelectedFinding] = useState<FindingOut | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  useEffect(() => {
    if (!id) return;
    const active = { current: true };

    Promise.all([
      apiClient.getReview(id),
      apiClient.listReviewFiles(id, { include_skipped: includeSkipped, page_size: 100 }),
      apiClient.listReviewFindings(id, { page_size: 100 }),
    ])
      .then(([reviewData, filesData, findingsData]) => {
        if (active.current) {
          setReview(reviewData);
          setFiles(filesData.items);
          setFindings(findingsData.items);
          setSelectedFilePath((prev) => prev ?? (filesData.items[0]?.path || null));
          setErrorMessage(null);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (active.current) {
          setErrorMessage(
            err instanceof Error ? err.message : 'Failed to load files and diff data.'
          );
          setIsLoading(false);
        }
      });

    return () => {
      active.current = false;
    };
  }, [id, includeSkipped, refreshTrigger]);

  const handleManualRefresh = () => {
    setIsLoading(true);
    setRefreshTrigger((prev) => prev + 1);
  };

  // Filter files by search term
  const filteredFiles = files.filter((f) =>
    f.path.toLowerCase().includes(searchQuery.toLowerCase().trim())
  );

  // Current active file
  const activeFile =
    files.find((f) => f.path === selectedFilePath) ||
    filteredFiles[0] ||
    null;

  // Active file's findings
  const activeFindings = findings.filter(
    (f) => activeFile && f.file_path === activeFile.path
  );

  const handleFindingUpdated = (updated: FindingOut) => {
    setFindings((prev) =>
      prev.map((f) => (f.id === updated.id ? updated : f))
    );
    setSelectedFinding(updated);
  };

  if (isLoading && !review) {
    return (
      <div className="page-review-files">
        <div className="flex-align-center gap-2 mb-4">
          <Skeleton width="160px" height="24px" />
        </div>
        <Skeleton width="50%" height="32px" className="mb-4" />
        <div className="files-layout-grid">
          <Skeleton width="280px" height="500px" />
          <Skeleton width="100%" height="500px" />
        </div>
      </div>
    );
  }

  if (!review) {
    return (
      <EmptyState
        title="Review Not Found"
        description="The requested review could not be found."
        action={
          <Link to="/" className="btn btn-primary">
            <ArrowLeft size={16} />
            <span>Return to Dashboard</span>
          </Link>
        }
      />
    );
  }

  return (
    <div className="page-review-files">
      {/* Top Header */}
      <div className="files-page-header">
        <div className="header-breadcrumbs">
          <Link to={`/reviews/${review.id}`} className="back-link">
            <ArrowLeft size={16} />
            <span>Back to Review Overview</span>
          </Link>
          <span className="breadcrumb-separator">/</span>
          <span className="font-semibold text-sm">Changed Files & Diff</span>
        </div>

        <div className="files-header-meta">
          <h1 className="files-pr-title">
            <span>{review.pr_title || `PR #${review.pr_number}`}</span>
            <span className="text-muted font-normal text-sm ml-2">
              ({review.owner}/{review.repo})
            </span>
          </h1>
          <div className="flex-align-center gap-3">
            <span className="text-sm font-mono text-success font-semibold">
              +{review.additions}
            </span>
            <span className="text-sm font-mono text-danger font-semibold">
              -{review.deletions}
            </span>
            <span className="badge badge-secondary">
              {review.files_changed} files
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleManualRefresh}
              aria-label="Refresh files"
            >
              <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
              <span>Refresh</span>
            </button>
          </div>
        </div>
      </div>

      {errorMessage && (
        <ErrorBanner
          message={errorMessage}
          onDismiss={() => setErrorMessage(null)}
        />
      )}

      {/* Main Files & Diff Split Workspace */}
      <div className="files-workspace">
        {/* Left File Tree Sidebar */}
        <aside className="files-sidebar-pane" aria-label="Changed files list">
          <div className="files-sidebar-header">
            <div className="files-search-box">
              <Search size={14} className="search-icon" aria-hidden="true" />
              <input
                type="text"
                className="form-input search-file-input"
                placeholder="Filter files..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label="Filter file list"
              />
            </div>
            <div className="skipped-toggle-row">
              <label className="checkbox-label text-xs text-muted">
                <input
                  type="checkbox"
                  checked={includeSkipped}
                  onChange={(e) => setIncludeSkipped(e.target.checked)}
                />
                <span>Include skipped/binary files</span>
              </label>
            </div>
          </div>

          <div className="files-list-container" role="listbox" aria-label="PR touched files">
            {filteredFiles.length === 0 ? (
              <div className="empty-files-hint">
                <Filter size={18} className="text-muted" aria-hidden="true" />
                <span className="text-xs text-muted">No matching files found</span>
              </div>
            ) : (
              filteredFiles.map((f) => {
                const isSelected = activeFile?.path === f.path;
                const pathParts = f.path.split('/');
                const fileName = pathParts.pop() || f.path;
                const dirPath = pathParts.join('/');

                return (
                  <button
                    key={f.id}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={`file-item-btn ${isSelected ? 'selected' : ''}`}
                    onClick={() => setSelectedFilePath(f.path)}
                  >
                    <div className="file-item-icon" aria-hidden="true">
                      <FileCode size={16} />
                    </div>
                    <div className="file-item-names">
                      <span className="file-item-filename font-medium">{fileName}</span>
                      {dirPath && (
                        <span className="file-item-dir text-muted text-xs font-mono">
                          {dirPath}
                        </span>
                      )}
                    </div>
                    <div className="file-item-badges">
                      {f.findings_count > 0 && (
                        <span
                          className="file-finding-pill badge-warning"
                          title={`${f.findings_count} findings detected in this file`}
                        >
                          {f.findings_count}
                        </span>
                      )}
                      {f.skipped_reason ? (
                        <span
                          className="badge-skipped"
                          title={`Skipped: ${f.skipped_reason}`}
                        >
                          Skipped
                        </span>
                      ) : (
                        <span className="file-diff-summary text-xs font-mono">
                          <span className="text-success">+{f.additions}</span>{' '}
                          <span className="text-danger">-{f.deletions}</span>
                        </span>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        {/* Right Main Pane: Diff Viewer */}
        <main className="diff-main-pane">
          {activeFile ? (
            <DiffViewer
              file={activeFile}
              findings={activeFindings}
              onSelectFinding={(f) => {
                setSelectedFinding(f);
                setIsDrawerOpen(true);
              }}
            />
          ) : (
            <div className="diff-empty-placeholder">
              <CheckCircle size={32} className="text-muted" aria-hidden="true" />
              <p className="text-muted font-medium">Select a file to inspect its diff and findings.</p>
            </div>
          )}
        </main>
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
    </div>
  );
};
