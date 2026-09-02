import { useState } from 'react';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router-dom';
import { Button, Logo } from './components';
import { useAuth } from './auth';
import type { Role } from './types';
const nav:Record<Role,{to:string;label:string}[]>={
 ADMIN:[{to:'/admin/dashboard',label:'Dashboard'},{to:'/admin/job-cards',label:'Job Cards'},{to:'/admin/users',label:'Users & Roles'},{to:'/admin/msmes',label:'MSME Master'},{to:'/admin/workflow',label:'Documents & Approvals'},{to:'/admin/audit',label:'Audit Log'},{to:'/admin/notifications',label:'Notifications'},{to:'/admin/settings',label:'Settings'}],
 MANAGEMENT:[{to:'/management/dashboard',label:'Dashboard'},{to:'/management/projects',label:'Projects'},{to:'/management/reports',label:'Reports'},{to:'/management/audit',label:'Audit Logs'},{to:'/management/notifications',label:'Notifications'}],
 END_USER:[{to:'/user/dashboard',label:'My Dashboard'},{to:'/user/job-cards',label:'My Job Cards'},{to:'/user/visits',label:'Visits'},{to:'/user/documents',label:'Documents'},{to:'/user/activity',label:'Activity'},{to:'/user/notifications',label:'Notifications'},{to:'/user/profile',label:'Profile'}],
 UNIT_REPRESENTATIVE:[{to:'/approvals',label:'Approval Queue'},{to:'/approval-notifications',label:'Notifications'}],
 DIRECTOR:[{to:'/approvals',label:'Approval Queue'},{to:'/approval-notifications',label:'Notifications'}]
};
export function AppLayout(){
  const {user,logout}=useAuth();
  const [menuOpen,setMenuOpen]=useState(false);
  const location=useLocation();
  if(!user)return <Navigate to="/login" replace/>;
  const closeMenu=()=>setMenuOpen(false);
  return <div className={`app-shell${menuOpen?' mobile-nav-open':''}`}>
    <aside className="sidebar">
      <div className="sidebar-top">
        <a className="brand" href="#/" onClick={closeMenu}><Logo size={26} /><div className="brand-title">D'Mastru <small>Job Card Tracker</small></div></a>
        <button className="mobile-menu-button" type="button" aria-label="Toggle navigation" aria-expanded={menuOpen} aria-controls="primary-navigation" onClick={()=>setMenuOpen(open=>!open)}>
          <span></span><span></span><span></span>
        </button>
      </div>
      <nav id="primary-navigation" aria-label="Primary navigation">{nav[user.role].map(i=><NavLink key={i.to} to={i.to} onClick={closeMenu}>{i.label}</NavLink>)}</nav>
      <div className="account"><strong>{user.full_name}</strong><small>{user.role.replace('_',' ')}</small><Button variant="quiet" onClick={()=>void logout()}>Sign out</Button></div>
    </aside>
    <button className="mobile-nav-backdrop" type="button" aria-label="Close navigation" tabIndex={menuOpen ? 0 : -1} onClick={closeMenu}></button>
    <main className="main" key={location.pathname}><Outlet/></main>
  </div>
}
