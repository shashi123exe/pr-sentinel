import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  RotateCcw,
  Save,
  Sliders,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { Category, SettingsOut, SettingsUpdate } from '../types/api';
import { ALL_CATEGORIES } from '../types/api';
import { ErrorBanner } from '../components/common/ErrorBanner';
import { Skeleton } from '../components/common/Skeleton';

export const Settings: React.FC = () => {
  const [initialSettings, setInitialSettings] = useState<SettingsOut | null>(null);

  // Form fields
  const [maxFiles, setMaxFiles] = useState<number>(50);
  const [maxDiffBytes, setMaxDiffBytes] = useState<number>(200000);
  const [minConfidence, setMinConfidence] = useState<number>(0.7);
  const [geminiModel, setGeminiModel] = useState<string>('gemini-2.5-flash');
  const [enabledCategories, setEnabledCategories] = useState<Category[]>([...ALL_CATEGORIES]);

  // UI state
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [reloadTrigger, setReloadTrigger] = useState<number>(0);

  // Access Token state (sessionStorage or localStorage)
  const [sessionToken, setSessionToken] = useState<string>(() => apiClient.getAccessToken() || '');
  const [rememberDevice, setRememberDevice] = useState<boolean>(() => apiClient.isDeviceRemembered());
  const [tokenMessage, setTokenMessage] = useState<string | null>(null);

  useEffect(() => {
    const handleTokenUpdated = () => {
      setSessionToken(apiClient.getAccessToken() || '');
      setRememberDevice(apiClient.isDeviceRemembered());
    };

    window.addEventListener('pr-sentinel:token-updated', handleTokenUpdated);
    return () => {
      window.removeEventListener('pr-sentinel:token-updated', handleTokenUpdated);
    };
  }, []);

  useEffect(() => {
    const active = { current: true };

    apiClient
      .getSettings()
      .then((data) => {
        if (active.current) {
          setInitialSettings(data);
          setMaxFiles(data.max_files);
          setMaxDiffBytes(data.max_diff_bytes);
          setMinConfidence(data.min_confidence);
          setGeminiModel(data.gemini_model || '');
          setEnabledCategories(data.enabled_categories || [...ALL_CATEGORIES]);
          setErrorMessage(null);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (active.current) {
          setErrorMessage(
            err instanceof Error ? err.message : 'Failed to retrieve application settings.'
          );
          setIsLoading(false);
        }
      });

    return () => {
      active.current = false;
    };
  }, [reloadTrigger]);

  const handleReload = () => {
    setIsLoading(true);
    setSuccessMessage(null);
    setErrorMessage(null);
    setReloadTrigger((prev) => prev + 1);
  };

  const handleCategoryToggle = (category: Category) => {
    setEnabledCategories((prev) =>
      prev.includes(category)
        ? prev.filter((c) => c !== category)
        : [...prev, category]
    );
  };

  const handleReset = () => {
    if (!initialSettings) return;
    setMaxFiles(initialSettings.max_files);
    setMaxDiffBytes(initialSettings.max_diff_bytes);
    setMinConfidence(initialSettings.min_confidence);
    setGeminiModel(initialSettings.gemini_model || '');
    setEnabledCategories(initialSettings.enabled_categories || [...ALL_CATEGORIES]);
    setValidationErrors({});
    setSuccessMessage(null);
    setErrorMessage(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSuccessMessage(null);
    setErrorMessage(null);

    // Validation
    const errors: Record<string, string> = {};
    if (isNaN(maxFiles) || maxFiles < 1 || maxFiles > 200) {
      errors.maxFiles = 'Max files must be between 1 and 200.';
    }
    if (isNaN(maxDiffBytes) || maxDiffBytes < 10000 || maxDiffBytes > 5000000) {
      errors.maxDiffBytes = 'Max diff bytes must be between 10,000 and 5,000,000 bytes.';
    }
    if (isNaN(minConfidence) || minConfidence < 0 || minConfidence > 1) {
      errors.minConfidence = 'Confidence threshold must be between 0.0 and 1.0.';
    }
    if (enabledCategories.length === 0) {
      errors.categories = 'At least one category must be enabled.';
    }

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }
    setValidationErrors({});

    setIsSaving(true);
    try {
      const payload: SettingsUpdate = {
        max_files: maxFiles,
        max_diff_bytes: maxDiffBytes,
        min_confidence: minConfidence,
        enabled_categories: enabledCategories,
        gemini_model: geminiModel.trim(),
      };

      const updated = await apiClient.updateSettings(payload);
      setInitialSettings(updated);
      setSuccessMessage('Application analysis settings successfully updated.');
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to update application settings.'
      );
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading && !initialSettings) {
    return (
      <div className="page-settings">
        <Skeleton width="220px" height="32px" className="mb-2" />
        <Skeleton width="400px" height="20px" className="mb-6" />
        <div className="card p-6">
          <Skeleton count={4} height="50px" className="mb-4" />
        </div>
      </div>
    );
  }

  return (
    <div className="page-settings">
      <div className="page-header-row">
        <div>
          <h1 className="page-title">Global Analysis Settings</h1>
          <p className="page-subtitle">
            Configure safety processing thresholds, confidence gates, and AI review models
          </p>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleReload}
            disabled={isLoading || isSaving}
            aria-label="Reload settings"
          >
            <RefreshCw size={15} className={isLoading ? 'animate-spin' : ''} />
            <span>Reload</span>
          </button>
        </div>
      </div>

      {successMessage && (
        <div className="alert alert-success mb-4" role="status">
          <CheckCircle2 size={18} className="text-success" aria-hidden="true" />
          <span>{successMessage}</span>
        </div>
      )}

      {errorMessage && (
        <ErrorBanner
          message={errorMessage}
          onDismiss={() => setErrorMessage(null)}
        />
      )}

      <form onSubmit={handleSubmit} noValidate>
        <div className="card settings-card">
          <div className="card-header-bar">
            <div className="flex-align-center gap-2">
              <Sliders size={18} className="text-primary" aria-hidden="true" />
              <h2 className="card-title">Diff & Processing Limits</h2>
            </div>
          </div>

          <div className="settings-fields-grid">
            {/* Max Files */}
            <div className="form-group">
              <label htmlFor="max-files-input" className="form-label font-semibold">
                Maximum Files Per PR
              </label>
              <input
                id="max-files-input"
                type="number"
                min={1}
                max={200}
                className={`form-input ${validationErrors.maxFiles ? 'input-error' : ''}`}
                value={maxFiles}
                onChange={(e) => setMaxFiles(parseInt(e.target.value, 10))}
                disabled={isSaving}
                required
              />
              {validationErrors.maxFiles && (
                <div className="form-error-msg" role="alert">
                  <AlertCircle size={14} aria-hidden="true" />
                  <span>{validationErrors.maxFiles}</span>
                </div>
              )}
              <p className="form-help-text">
                Cap on touched files allowed in review before truncating (1 to 200 files).
              </p>
            </div>

            {/* Max Diff Bytes */}
            <div className="form-group">
              <label htmlFor="max-diff-bytes-input" className="form-label font-semibold">
                Maximum Diff Bytes
              </label>
              <input
                id="max-diff-bytes-input"
                type="number"
                min={10000}
                max={5000000}
                step={50000}
                className={`form-input ${validationErrors.maxDiffBytes ? 'input-error' : ''}`}
                value={maxDiffBytes}
                onChange={(e) => setMaxDiffBytes(parseInt(e.target.value, 10))}
                disabled={isSaving}
                required
              />
              {validationErrors.maxDiffBytes && (
                <div className="form-error-msg" role="alert">
                  <AlertCircle size={14} aria-hidden="true" />
                  <span>{validationErrors.maxDiffBytes}</span>
                </div>
              )}
              <p className="form-help-text">
                Safety cap on total patch size in bytes (10,000 to 5,000,000 bytes).
              </p>
            </div>

            {/* Min Confidence */}
            <div className="form-group">
              <div className="flex-between">
                <label htmlFor="min-confidence-input" className="form-label font-semibold">
                  Minimum Confidence Threshold
                </label>
                <span className="font-mono font-semibold text-primary">
                  {Math.round(minConfidence * 100)}%
                </span>
              </div>
              <input
                id="min-confidence-input"
                type="range"
                min={0}
                max={1}
                step={0.05}
                className="range-slider w-full mt-2"
                value={minConfidence}
                onChange={(e) => setMinConfidence(parseFloat(e.target.value))}
                disabled={isSaving}
              />
              {validationErrors.minConfidence && (
                <div className="form-error-msg" role="alert">
                  <AlertCircle size={14} aria-hidden="true" />
                  <span>{validationErrors.minConfidence}</span>
                </div>
              )}
              <p className="form-help-text">
                Findings with confidence below this threshold are automatically filtered out.
              </p>
            </div>

            {/* Gemini Model Override */}
            <div className="form-group">
              <label htmlFor="gemini-model-input" className="form-label font-semibold">
                Gemini Model Identifier
              </label>
              <input
                id="gemini-model-input"
                type="text"
                className="form-input font-mono"
                placeholder="gemini-2.5-flash"
                value={geminiModel}
                onChange={(e) => setGeminiModel(e.target.value)}
                disabled={isSaving}
              />
              <p className="form-help-text">
                Default: <span className="font-mono">gemini-2.5-flash</span>. Leave empty to use system default.
              </p>
            </div>
          </div>

          {/* Enabled Categories */}
          <div className="categories-settings-section">
            <h3 className="section-heading mb-1">Enabled Review Categories</h3>
            <p className="text-sm text-muted mb-4">
              Sentinel will only report and validate findings matching enabled categories.
            </p>

            {validationErrors.categories && (
              <div className="form-error-msg mb-3" role="alert">
                <AlertCircle size={14} aria-hidden="true" />
                <span>{validationErrors.categories}</span>
              </div>
            )}

            <div className="categories-checkbox-grid">
              {ALL_CATEGORIES.map((cat) => {
                const isChecked = enabledCategories.includes(cat);
                return (
                  <label
                    key={cat}
                    className={`category-checkbox-card ${isChecked ? 'checked' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => handleCategoryToggle(cat)}
                      disabled={isSaving}
                    />
                    <div className="checkbox-info">
                      <span className="checkbox-title capitalize">
                        {cat.replace('_', ' ')}
                      </span>
                    </div>
                  </label>
                );
              })}
            </div>
          </div>

          {/* Form Actions Footer */}
          <div className="card-footer-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleReset}
              disabled={isSaving}
            >
              <RotateCcw size={15} />
              <span>Reset Changes</span>
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isSaving}
            >
              <Save size={15} />
              <span>{isSaving ? 'Saving Settings...' : 'Save Settings'}</span>
            </button>
          </div>
        </div>
      </form>

      {/* Access Token Management Card */}
      <div className="card mt-6" data-testid="access-token-card">
        <div className="card-header">
          <h2 className="card-title text-base">API Access Token &amp; Authentication</h2>
          <p className="card-subtitle text-xs text-muted">
            If your PR Sentinel instance enforces APP_ACCESS_TOKEN, manage your authentication credentials here.
            You can keep the token isolated to this session or remember it across browser restarts.
          </p>
        </div>
        <div className="card-body">
          <div className="form-group mb-2">
            <label htmlFor="session-token-input" className="form-label text-sm font-medium">
              Bearer Access Token
            </label>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <input
                id="session-token-input"
                type="password"
                className="input-text font-mono"
                style={{ flex: 1 }}
                placeholder="Enter shared access token..."
                value={sessionToken}
                onChange={(e) => {
                  setSessionToken(e.target.value);
                  setTokenMessage(null);
                }}
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  apiClient.setAccessToken(sessionToken, rememberDevice);
                  setTokenMessage(
                    sessionToken
                      ? rememberDevice
                        ? 'Access token saved and remembered on this device.'
                        : 'Access token saved for this browser session.'
                      : 'Token cleared.'
                  );
                  window.dispatchEvent(new CustomEvent('pr-sentinel:token-updated'));
                  setReloadTrigger((prev) => prev + 1);
                }}
              >
                Save Token
              </button>
              {sessionToken && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    apiClient.clearAccessToken();
                    setSessionToken('');
                    setRememberDevice(false);
                    setTokenMessage('Logged out. Access token cleared from all browser storage.');
                    window.dispatchEvent(new CustomEvent('pr-sentinel:unauthorized'));
                    setReloadTrigger((prev) => prev + 1);
                  }}
                  data-testid="logout-btn"
                >
                  Log Out
                </button>
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.75rem' }}>
              <input
                id="settings-remember-device"
                type="checkbox"
                checked={rememberDevice}
                onChange={(e) => setRememberDevice(e.target.checked)}
                data-testid="settings-remember-device-checkbox"
              />
              <label htmlFor="settings-remember-device" className="text-xs text-muted" style={{ cursor: 'pointer' }}>
                Remember this device (stores token in localStorage across browser restarts)
              </label>
            </div>

            <p className="text-xs text-muted mt-2" style={{ fontStyle: 'italic' }}>
              Security Note: Persistent storage survives browser reboots for convenience, but increases XSS exposure compared to tab-scoped sessionStorage.
            </p>

            {tokenMessage && (
              <p className="text-xs text-muted mt-2" style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <CheckCircle2 size={13} aria-hidden="true" />
                <span>{tokenMessage}</span>
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
