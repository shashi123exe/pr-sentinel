import React, { useCallback, useEffect, useRef, useState } from 'react';
import { KeyRound, ShieldAlert, X } from 'lucide-react';
import { apiClient } from '../../api/client';

interface AccessTokenModalProps {
  /** Optional manual trigger control */
  isOpen?: boolean;
  onClose?: () => void;
  onSuccess?: () => void;
}

export const AccessTokenModal: React.FC<AccessTokenModalProps> = ({
  isOpen: controlledIsOpen,
  onClose: controlledOnClose,
  onSuccess,
}) => {
  const [internalIsOpen, setInternalIsOpen] = useState<boolean>(false);
  const [tokenInput, setTokenInput] = useState<string>(() => apiClient.getAccessToken() || '');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isVisible = controlledIsOpen !== undefined ? controlledIsOpen : internalIsOpen;

  const handleClose = useCallback(() => {
    setErrorMsg(null);
    if (controlledOnClose) {
      controlledOnClose();
    } else {
      setInternalIsOpen(false);
    }
  }, [controlledOnClose]);

  useEffect(() => {
    const handleUnauthorizedEvent = () => {
      setErrorMsg('This instance requires an access token, or the existing token expired.');
      setTokenInput(apiClient.getAccessToken() || '');
      setInternalIsOpen(true);
    };

    window.addEventListener('pr-sentinel:unauthorized', handleUnauthorizedEvent);
    return () => {
      window.removeEventListener('pr-sentinel:unauthorized', handleUnauthorizedEvent);
    };
  }, []);

  useEffect(() => {
    if (!isVisible) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    const timer = setTimeout(() => {
      inputRef.current?.focus();
    }, 50);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      clearTimeout(timer);
    };
  }, [isVisible, handleClose]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = tokenInput.trim();
    if (!trimmed) {
      setErrorMsg('Please enter an access token.');
      return;
    }

    // Save strictly to sessionStorage per security design
    apiClient.setAccessToken(trimmed);
    setErrorMsg(null);

    // Notify listeners across the app that token was updated (without destroying user drafts via reload)
    window.dispatchEvent(
      new CustomEvent('pr-sentinel:token-updated', {
        detail: { token: trimmed },
      })
    );

    handleClose();
    if (onSuccess) {
      onSuccess();
    }
  };

  const handleClear = () => {
    apiClient.clearAccessToken();
    setTokenInput('');
    setErrorMsg('Access token removed from this session.');
  };

  if (!isVisible) return null;

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className="modal-container"
        role="dialog"
        aria-modal="true"
        aria-labelledby="access-token-title"
        aria-describedby="access-token-desc"
        data-testid="access-token-modal"
      >
        <div className="modal-header">
          <div className="modal-title-row">
            <KeyRound className="text-primary" size={20} aria-hidden="true" />
            <h2 id="access-token-title" className="modal-title">
              API Access Token Required
            </h2>
          </div>
          <button
            type="button"
            className="btn-icon"
            onClick={handleClose}
            aria-label="Close dialog"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p id="access-token-desc" className="modal-message mb-3 text-sm text-muted">
              This PR Sentinel server enforces access control via <code className="font-mono">APP_ACCESS_TOKEN</code>.
              Your token will be saved strictly in <span className="font-semibold">sessionStorage</span> for this browser tab only.
            </p>

            {errorMsg && (
              <div className="form-error-msg mb-3" role="alert" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <ShieldAlert size={15} />
                <span>{errorMsg}</span>
              </div>
            )}

            <div className="form-group">
              <label htmlFor="modal-token-input" className="form-label text-sm font-medium">
                Access Token (X-Access-Token)
              </label>
              <input
                id="modal-token-input"
                ref={inputRef}
                type="password"
                className="input-text font-mono w-full"
                placeholder="Paste shared access token..."
                value={tokenInput}
                onChange={(e) => {
                  setTokenInput(e.target.value);
                  setErrorMsg(null);
                }}
                autoComplete="off"
              />
            </div>
          </div>

          <div className="modal-footer" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              {tokenInput && (
                <button
                  type="button"
                  className="btn btn-secondary text-xs"
                  onClick={handleClear}
                >
                  Clear Token
                </button>
              )}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleClose}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
              >
                Save &amp; Continue
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
