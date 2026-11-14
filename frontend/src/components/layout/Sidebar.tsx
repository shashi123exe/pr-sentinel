import React, { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import {
  History,
  LogOut,
  Moon,
  PlusCircle,
  Radio,
  Settings,
  Shield,
  Sun,
  LayoutDashboard,
} from 'lucide-react';
import { apiClient } from '../../api/client';
import type { Theme } from '../../hooks/useTheme';

interface SidebarProps {
  theme: Theme;
  onToggleTheme: () => void;
  isOpenMobile?: boolean;
  onCloseMobile?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  theme,
  onToggleTheme,
  isOpenMobile = false,
  onCloseMobile,
}) => {
  const [healthStatus, setHealthStatus] = useState<'checking' | 'healthy' | 'unhealthy'>('checking');
  const [hasToken, setHasToken] = useState<boolean>(() => Boolean(apiClient.getAccessToken()));

  useEffect(() => {
    let isMounted = true;
    const checkHealth = () => {
      apiClient
        .getHealth()
        .then((res) => {
          if (isMounted) {
            setHealthStatus(res.status === 'ok' && res.database === 'ok' ? 'healthy' : 'unhealthy');
          }
        })
        .catch(() => {
          if (isMounted) {
            setHealthStatus('unhealthy');
          }
        });
    };

    const handleAuthChange = () => {
      if (isMounted) {
        setHasToken(Boolean(apiClient.getAccessToken()));
        checkHealth();
      }
    };

    checkHealth();

    window.addEventListener('pr-sentinel:token-updated', handleAuthChange);
    window.addEventListener('pr-sentinel:unauthorized', handleAuthChange);

    return () => {
      isMounted = false;
      window.removeEventListener('pr-sentinel:token-updated', handleAuthChange);
      window.removeEventListener('pr-sentinel:unauthorized', handleAuthChange);
    };
  }, []);

  const handleLogout = () => {
    apiClient.clearAccessToken();
    setHasToken(false);
    window.dispatchEvent(new CustomEvent('pr-sentinel:unauthorized'));
    if (onCloseMobile) onCloseMobile();
  };

  const navItems = [
    { to: '/', label: 'Dashboard', icon: <LayoutDashboard size={18} /> },
    { to: '/new', label: 'New Review', icon: <PlusCircle size={18} /> },
    { to: '/history', label: 'Review History', icon: <History size={18} /> },
    { to: '/settings', label: 'Settings', icon: <Settings size={18} /> },
  ];

  return (
    <>
      {isOpenMobile && (
        <div
          className="sidebar-backdrop"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}
      <aside className={`app-sidebar ${isOpenMobile ? 'open' : ''}`}>
        <div className="sidebar-brand">
          <div className="brand-logo" aria-hidden="true">
            <Shield size={24} className="text-primary" />
          </div>
          <div className="brand-info">
            <span className="brand-title">PR Sentinel</span>
            <span className="brand-badge">v1.0</span>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="Main Navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `sidebar-link ${isActive ? 'active' : ''}`
              }
              onClick={onCloseMobile}
            >
              <span className="sidebar-icon" aria-hidden="true">
                {item.icon}
              </span>
              <span className="sidebar-label">{item.label}</span>
            </NavLink>
          ))}
          {hasToken && (
            <button
              type="button"
              className="sidebar-link"
              style={{
                width: '100%',
                background: 'transparent',
                border: 'none',
                textAlign: 'left',
                cursor: 'pointer',
                font: 'inherit',
              }}
              onClick={handleLogout}
              data-testid="sidebar-logout-btn"
              aria-label="Log Out"
            >
              <span className="sidebar-icon" aria-hidden="true">
                <LogOut size={18} />
              </span>
              <span className="sidebar-label">Log Out</span>
            </button>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="system-health-badge" title={`System status: ${healthStatus}`}>
            <Radio
              size={14}
              className={
                healthStatus === 'healthy'
                  ? 'text-success'
                  : healthStatus === 'unhealthy'
                    ? 'text-danger'
                    : 'text-warning'
              }
              aria-hidden="true"
            />
            <span className="health-label">
              {healthStatus === 'healthy'
                ? 'Backend Online'
                : healthStatus === 'unhealthy'
                  ? 'API Disconnected'
                  : 'Checking...'}
            </span>
          </div>

          <button
            type="button"
            className="theme-toggle-btn"
            onClick={onToggleTheme}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            <span>{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>
          </button>
        </div>
      </aside>
    </>
  );
};
