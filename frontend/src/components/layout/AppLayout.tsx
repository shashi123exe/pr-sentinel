import React, { useState } from 'react';
import { Menu, Shield } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { AccessTokenModal } from '../common/AccessTokenModal';
import { useTheme } from '../../hooks/useTheme';

interface AppLayoutProps {
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const { theme, toggleTheme } = useTheme();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  return (
    <div className="app-root">
      <a href="#main-content" className="sr-only">
        Skip to main content
      </a>

      {/* Mobile Header Bar */}
      <header className="mobile-header">
        <button
          type="button"
          className="btn-icon"
          onClick={() => setIsMobileMenuOpen(true)}
          aria-label="Open navigation menu"
        >
          <Menu size={22} />
        </button>
        <div className="mobile-header-brand">
          <Shield size={20} className="text-primary" aria-hidden="true" />
          <span className="font-bold">PR Sentinel</span>
        </div>
      </header>

      {/* Sidebar Navigation */}
      <Sidebar
        theme={theme}
        onToggleTheme={toggleTheme}
        isOpenMobile={isMobileMenuOpen}
        onCloseMobile={() => setIsMobileMenuOpen(false)}
      />

      {/* Main Content Area */}
      <main id="main-content" className="app-main">
        <div className="content-container">{children}</div>
      </main>

      {/* Global Access Token Modal (listens for 401 unauthorized events) */}
      <AccessTokenModal />
    </div>
  );
};
