import { useState } from 'react';
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Button, Logo } from './components';
import { useAuth } from './auth';
import type { Role } from './types';
type NavItem={to:string;label:string};
const nav:Record<Role,NavItem[]>={
 ADMIN:[{to:'/admin/dashboard',label:'Dashboard'},{to:'/referrals',label:'Refer a Client'},{to:'/admin/master-data',label:'Master Data'},{to:'/admin/job-cards',label:'Job Cards'},{to:'/admin/planner',label:'Planner Overview'},{to:'/admin/documents',label:'Document Review'},{to:'/admin/reports',label:'Reports'},
  {to:'/admin/packages',label:'Service Packages'},{to:'/marketing/closure',label:'Project Closure'},{to:'/admin/audit',label:'Audit Log'},{to:'/admin/notifications',label:'Notifications'},{to:'/admin/settings',label:'Settings'}],
 MANAGEMENT:[{to:'/management/dashboard',label:'Dashboard'},{to:'/referrals',label:'Refer a Client'},{to:'/management/projects',label:'Projects'},{to:'/management/reviews',label:'Milestone Reviews'},{to:'/management/reports?kind=visits',label:'Mandays'},{to:'/management/reports?kind=consultants',label:'Consultant Workload'},{to:'/management/planner',label:'Visit Calendar'},{to:'/marketing/payments',label:'Payments Overview'},{to:'/management/reports',label:'Reports'},
  {to:'/management/audit',label:'Audit Logs'},{to:'/management/notifications',label:'Notifications'}],
 MARKETING:[{to:'/marketing',label:'Dashboard'},{to:'/referrals',label:'Refer a Client'}],
 FINANCE:[{to:'/finance/dashboard',label:'Dashboard'},{to:'/referrals',label:'Refer a Client'},{to:'/finance/payments',label:'Milestone Payments'}],
 END_USER:[{to:'/user/dashboard',label:'Dashboard'},{to:'/referrals',label:'Refer a Client'},{to:'/user/job-cards',label:'My Job Cards'},{to:'/user/visits',label:'My Planner'},{to:'/user/activities',label:'Activities'},{to:'/user/mandays',label:'Mandays'},{to:'/user/documents',label:'Documents'},{to:'/user/activity',label:'History'},
  {to:'/user/notifications',label:'Notifications'},{to:'/user/profile',label:'Profile'}],
 UNIT_REPRESENTATIVE:[{to:'/referrals',label:'Refer a Client'}],
 DIRECTOR:[{to:'/approvals/dashboard',label:'Dashboard'},{to:'/referrals',label:'Refer a Client'},{to:'/approvals',label:'Approval Queue'},{to:'/approval-notifications',label:'Notifications'}]
};
const roleLabels:Record<Role,string>={ADMIN:'Admin Team',MARKETING:'Marketing Team',FINANCE:'Finance Team',MANAGEMENT:'Management',END_USER:'Consultant',UNIT_REPRESENTATIVE:'Unit Representative',DIRECTOR:'Director'};
export function AppLayout(){
  const {user,loading,logout}=useAuth();
  const [menuOpen,setMenuOpen]=useState(false);
  const location=useLocation();
  const navigate=useNavigate();
  if(loading)return <main className="center" role="status">Checking your session...</main>;
  if(!user)return <Navigate to="/login" replace/>;
  const closeMenu=()=>setMenuOpen(false);
  const signOut=()=>{void logout();closeMenu();navigate('/login',{replace:true})};
  return <div className={`app-shell${menuOpen?' mobile-nav-open':''}`}>
    <header className="mobile-topbar">
      <a className="brand" href="#/" onClick={closeMenu}><Logo size={26} /><div className="brand-title">D'Mastru <small>Job Card Tracker</small></div></a>
      <button className="mobile-menu-button" type="button" aria-label={menuOpen?'Close navigation':'Open navigation'} aria-expanded={menuOpen} aria-controls="primary-navigation" onClick={()=>setMenuOpen(open=>!open)}>
        <span></span><span></span><span></span>
      </button>
    </header>
    <aside className="sidebar">
      <div className="sidebar-top">
        <a className="brand" href="#/" onClick={closeMenu}><Logo size={26} /><div className="brand-title">D'Mastru <small>Job Card Tracker</small></div></a>
      </div>
      <div className="sidebar-workspace"><span><small>WORKSPACE</small><strong>{roleLabels[user.role]}</strong></span></div>
      <nav id="primary-navigation" aria-label={`${roleLabels[user.role]} navigation`}>{nav[user.role].map((i,index)=><NavLink key={`${i.to}-${i.label}-${index}`} title={i.label} to={i.to} onClick={closeMenu} className={({isActive})=>{const target=new URL(i.to,window.location.origin);return isActive&&location.pathname===target.pathname&&location.search===target.search?'active':''}}>{i.label}</NavLink>)}</nav>
      <div className="account"><span className="account-details"><strong>{user.full_name}</strong><small>{roleLabels[user.role]}</small></span><Button type="button" variant="quiet" onClick={signOut}>Sign out</Button></div>
    </aside>
    <button className="mobile-nav-backdrop" type="button" aria-label="Close navigation" tabIndex={menuOpen ? 0 : -1} onClick={closeMenu}></button>
    <main className="main" key={location.pathname}><div className="main-topbar"><div className="topbar-user"><span><strong>{user.full_name}</strong><small>{roleLabels[user.role]}</small></span></div></div><Outlet/></main>
  </div>
}
