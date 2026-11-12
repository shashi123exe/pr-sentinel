import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  GitPullRequest,
  Loader2,
  ShieldCheck,
  Sparkles,
  Terminal,
} from 'lucide-react';
import { apiClient, ApiClientError } from '../api/client';
import { ErrorBanner } from '../components/common/ErrorBanner';

// Standard GitHub pull request URL pattern
const GITHUB_PR_REGEX = /^https?:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+(?:[/?#].*)?$/i;

export const NewReview: React.FC = () => {
  const navigate = useNavigate();
  const [prUrl, setPrUrl] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const handleTokenUpdated = () => {
      // Clear previous authentication error when new access token is saved in modal
      setApiError(null);
    };

    window.addEventListener('pr-sentinel:token-updated', handleTokenUpdated);
    return () => {
      window.removeEventListener('pr-sentinel:token-updated', handleTokenUpdated);
    };
  }, []);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (isSubmitting) return;

    setValidationError(null);
    setApiError(null);

    const trimmedUrl = prUrl.trim();
    if (!trimmedUrl) {
      setValidationError('Please enter a GitHub Pull Request URL.');
      return;
    }

    if (!GITHUB_PR_REGEX.test(trimmedUrl)) {
      setValidationError(
        'Invalid PR URL format. Expected: https://github.com/owner/repository/pull/123'
      );
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await apiClient.createReview({ pr_url: trimmedUrl });
      if (!response || !response.id) {
        throw new Error('Unexpected response from server: Review ID was not returned.');
      }
      // Redirect to review detail / execution view
      navigate(`/reviews/${response.id}`);
    } catch (err) {
      let message = 'Failed to queue pull request review. Please check the URL and try again.';
      if (err instanceof ApiClientError) {
        if (err.status === 429) {
          message = err.retryAfter
            ? `${err.message} (Retry after ${err.retryAfter} seconds)`
            : err.message;
        } else {
          message = err.message;
        }
      } else if (err instanceof Error) {
        message = err.message;
      }
      setApiError(message);
      setIsSubmitting(false);
    }
  };

  return (
    <div className="page-new-review">
      <div className="new-review-container">
        <div className="new-review-header">
          <div className="badge badge-primary inline-flex mb-2">
            <Sparkles size={13} className="badge-icon" aria-hidden="true" />
            <span>Automated Analysis</span>
          </div>
          <h1 className="page-title">Submit Pull Request for Review</h1>
          <p className="page-subtitle">
            Provide a public or token-accessible GitHub pull request URL. Sentinel will
            parse the diff, execute static security analysis, query Gemini AI, and validate findings.
          </p>
        </div>

        {apiError && (
          <div className="mb-4">
            <ErrorBanner
              message={apiError}
              onRetry={() => void handleSubmit()}
              onDismiss={() => setApiError(null)}
            />
          </div>
        )}

        <div className="card new-review-card">
          <form onSubmit={handleSubmit} noValidate>
            <div className="form-group">
              <label htmlFor="pr-url-input" className="form-label font-semibold">
                GitHub Pull Request URL
              </label>
              <div className="input-with-icon">
                <GitPullRequest
                  size={18}
                  className="input-leading-icon"
                  aria-hidden="true"
                />
                <input
                  id="pr-url-input"
                  type="url"
                  className={`form-input ${validationError ? 'input-error' : ''}`}
                  placeholder="https://github.com/owner/repo/pull/42"
                  value={prUrl}
                  onChange={(e) => {
                    setPrUrl(e.target.value);
                    if (validationError) setValidationError(null);
                  }}
                  disabled={isSubmitting}
                  autoComplete="off"
                  spellCheck="false"
                  required
                />
              </div>

              {validationError && (
                <div className="form-error-msg" role="alert">
                  <AlertCircle size={14} aria-hidden="true" />
                  <span>{validationError}</span>
                </div>
              )}

              <p className="form-help-text">
                Example:{' '}
                <span className="font-mono text-xs">
                  https://github.com/fastapi/fastapi/pull/1000
                </span>
              </p>
            </div>

            <div className="form-actions">
              <button
                type="submit"
                className="btn btn-primary btn-lg w-full"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    <span>Queuing Review...</span>
                  </>
                ) : (
                  <>
                    <ShieldCheck size={18} />
                    <span>Launch Sentinel Review</span>
                    <ArrowRight size={18} />
                  </>
                )}
              </button>
            </div>
          </form>
        </div>

        {/* Feature Highlights Grid */}
        <div className="review-pipeline-info-grid">
          <div className="info-card">
            <div className="info-card-icon text-primary">
              <Terminal size={20} />
            </div>
            <h3 className="info-card-title">Static Security Scanning</h3>
            <p className="info-card-desc">
              Executes Bandit AST analysis to detect SQL injections, command execution,
              unsafe deserialization, and hardcoded credentials.
            </p>
          </div>

          <div className="info-card">
            <div className="info-card-icon text-info">
              <Sparkles size={20} />
            </div>
            <h3 className="info-card-title">Gemini AI Code Review</h3>
            <p className="info-card-desc">
              Context-aware deep reasoning identifies subtle logic bugs, boundary edge cases,
              and security anti-patterns across altered methods.
            </p>
          </div>

          <div className="info-card">
            <div className="info-card-icon text-success">
              <CheckCircle2 size={20} />
            </div>
            <h3 className="info-card-title">Rigorous Evidence Validation</h3>
            <p className="info-card-desc">
              Every finding must cite verbatim evidence present in the PR diff or context snippets.
              Hallucinated findings are automatically eliminated.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
