import React from 'react';
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { LayoutDashboard, ListOrdered, Receipt } from 'lucide-react';
import { Dashboard } from './pages/Dashboard';
import { Positions } from './pages/Positions';
import { Orders } from './pages/Orders';

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <div className="app-layout">
        {/* Sidebar */}
        <aside className="sidebar">
          <div className="sidebar-brand">
            <span className="sidebar-logo">🤖</span>
            <span className="sidebar-title">PolyBot</span>
          </div>
          <nav className="sidebar-nav">
            <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <LayoutDashboard size={18} />
              <span>Dashboard</span>
            </NavLink>
            <NavLink to="/positions" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <Receipt size={18} />
              <span>Positions</span>
            </NavLink>
            <NavLink to="/orders" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <ListOrdered size={18} />
              <span>Orders</span>
            </NavLink>
          </nav>
          <div className="sidebar-footer">
            <span className="text-muted" style={{ fontSize: 11 }}>v1.0.0</span>
          </div>
        </aside>

        {/* Main Content */}
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/orders" element={<Orders />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
};

export default App;
