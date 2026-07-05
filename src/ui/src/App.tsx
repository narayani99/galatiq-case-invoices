import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Upload as UploadIcon, LayoutDashboard, Info } from 'lucide-react';
import { Upload } from './pages/Upload';
import { Processing } from './pages/Processing';
import { Dashboard } from './pages/Dashboard';
import './App.css';

const NavigationSidebar: React.FC = () => {
  const location = useLocation();

  const navItems = [
    { path: '/', label: 'Ingest Invoice', icon: <UploadIcon size={18} /> },
    { path: '/dashboard', label: 'Dashboard', icon: <LayoutDashboard size={18} /> },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <LandmarkIcon className="text-primary mr-2" size={24} />
        <h2>Galatiq Invoice</h2>
      </div>
      <nav className="sidebar-nav">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path;
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`nav-link ${isActive ? 'active' : ''}`}
            >
              {item.icon}
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <div className="footer-item">
          <Info size={14} />
          <span>Local Engine v1.0.0</span>
        </div>
      </div>
    </aside>
  );
};

function App() {
  return (
    <Router>
      <div className="app-layout">
        <NavigationSidebar />
        <main className="main-content">
          <header className="top-navbar">
            <div className="system-indicator">
              <span className="indicator-dot status-active" />
              <span>Pipeline Server Online</span>
            </div>
            <div className="user-profile">
              <span>Acme Accounts Team</span>
            </div>
          </header>
          <div className="page-content-wrapper">
            <Routes>
              <Route path="/" element={<Upload />} />
              <Route path="/processing/:id" element={<Processing />} />
              <Route path="/dashboard" element={<Dashboard />} />
            </Routes>
          </div>
        </main>
      </div>
    </Router>
  );
}

const LandmarkIcon: React.FC<{ className?: string; size?: number }> = ({ className = '', size = 24 }) => (
  <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10"></line>
    <line x1="12" y1="20" x2="12" y2="4"></line>
    <line x1="6" y1="20" x2="6" y2="14"></line>
    <line x1="2" y1="20" x2="22" y2="20"></line>
  </svg>
);

export default App;
