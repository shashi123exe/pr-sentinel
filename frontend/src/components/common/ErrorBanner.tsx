import React from 'react';
import { AlertCircle, RotateCcw, X } from 'lucide-react';

interface ErrorBannerProps {
  message: string;
  code?: string;
  onRetry?: () => void;
  onDismiss?: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({
  message,
  code,
  onRetry,
  onDismiss,
}) => {
  return (
    <div className="error-banner" role="alert">
      <div className="error-banner-content">
        <AlertCircle size={18} className="error-banner-icon" aria-hidden="true" />
        <div className="error-banner-text">
          {code && <span className="error-banner-code">[{code}] </span>}
          <span>{message}</span>
        </div>
      </div>
      <div className="error-banner-actions">
        {onRetry && (
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            onClick={onRetry}
          >
            <RotateCcw size={13} />
            <span>Retry</span>
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            className="btn-icon"
            onClick={onDismiss}
            aria-label="Dismiss error"
          >
            <X size={16} />
          </button>
        )}
      </div>
    </div>
  );
};
