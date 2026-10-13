import { useEffect, useState } from 'react';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router-dom';
import { Button, Logo } from './components';
import { useAuth } from './auth';
import { api } from './api';
import type { Role } from './types';
type NavItem={to:string;label:string};
const nav:Record<Role,NavItem[]>={
 ADMIN:[{to:'/admin/dashboard',label:'Dashboard'},{to:'/admin/msmes',label:'MSME Master'},{to:'/admin/users',label:'Users & Roles'},{to:'/admin/job-cards',label:'Job Cards'},{to:'/admin/job-cards?focus=milestones',label:'Milestones'},{to:'/admin/job-cards?focus=allocation',label:'Consultant Allocation'},{to:'/admin/planner',label:'Planner Overview'},{to:'/admin/workflow',label:'Document Review'},{to:'/admin/reports',label:'Reports'},
  {to:'/admin/packages',label:'Service Packages'},{to:'/admin/workflows',label:'Workflow Templates'},{to:'/actions',label:'Actions'},{to:'/marketing/closure',label:'Project Closure'},{to:'/admin/audit',label:'Audit Log'},{to:'/admin/notifications',label:'Notifications'},{to:'/admin/settings',label:'Settings'}],
 MANAGEMENT:[{to:'/management/dashboard',label:'Dashboard'},{to:'/management/projects',label:'Projects'},{to:'/management/reports?kind=visits',label:'Mandays'},{to:'/management/reports?kind=consultants',label:'Consultant Workload'},{to:'/management/planner',label:'Visit Calendar'},{to:'/marketing/payments',label:'Payments Overview'},{to:'/management/reports',label:'Reports'},
  {to:'/actions',label:'Department Actions'},{to:'/management/audit',label:'Audit Logs'},{to:'/management/notifications',label:'Notifications'}],
 MARKETING:[{to:'/marketing',label:'Dashboard'},{to:'/marketing?view=enquiries',label:'Enquiries'},{to:'/marketing?view=enquiries&focus=followups',label:'Follow-ups'},{to:'/marketing/payments',label:'Job Cards'},{to:'/marketing/payments',label:'Payments'},{to:'/marketing/payments',label:'Invoices'},{to:'/marketing/reports',label:'Reports'},
  {to:'/actions',label:'My Actions'},{to:'/marketing/closure',label:'Project Closure'}],
 END_USER:[{to:'/user/dashboard',label:'Dashboard'},{to:'/user/job-cards',label:'My Job Cards'},{to:'/actions',label:'My Tasks'},{to:'/user/visits',label:'My Planner'},{to:'/user/activities',label:'Activities'},{to:'/user/mandays',label:'Mandays'},{to:'/user/documents',label:'Documents'},{to:'/user/activity',label:'History'},
  {to:'/user/notifications',label:'Notifications'},{to:'/user/profile',label:'Profile'}],
 UNIT_REPRESENTATIVE:[{to:'/approvals',label:'Management · Approval Queue'},{to:'/actions',label:'My Actions'},{to:'/approval-notifications',label:'Notifications'}],
 DIRECTOR:[{to:'/approvals',label:'Management · Approval Queue'},{to:'/actions',label:'My Actions'},{to:'/approval-notifications',label:'Notifications'}]
};
const roleLabels:Record<Role,string>={ADMIN:'Admin Team',MARKETING:'Marketing / Finance Team',MANAGEMENT:'Management',END_USER:'Consultant',UNIT_REPRESENTATIVE:'Management · Unit Representative',DIRECTOR:'Management · Director'};
export function AppLayout(){
  const {user,loading,logout}=useAuth();
  const [menuOpen,setMenuOpen]=useState(false);
  const location=useLocation();
  if(loading)return <main className="center" role="status">Checking your session…</main>;
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
      <nav id="primary-navigation" aria-label={`${roleLabels[user.role]} navigation`}>{nav[user.role].map((i,index)=><NavLink key={`${i.to}-${i.label}-${index}`} to={i.to} onClick={closeMenu} className={({isActive})=>{const target=new URL(i.to,window.location.origin);return isActive&&location.pathname===target.pathname&&location.search===target.search?'active':''}}>{i.label}</NavLink>)}</nav>
      <div className="account"><strong>{user.full_name}</strong><small>{roleLabels[user.role]}</small><Button variant="quiet" onClick={()=>void logout()}>Sign out</Button></div>
    </aside>
    <button className="mobile-nav-backdrop" type="button" aria-label="Close navigation" tabIndex={menuOpen ? 0 : -1} onClick={closeMenu}></button>
    <main className="main" key={location.pathname}><GlobalSearch/><Outlet/></main>
  </div>
}

type SearchResult={kind:string;id:string;title:string;subtitle:string;status:string;href:string};
function GlobalSearch(){const [term,setTerm]=useState(''),[rows,setRows]=useState<SearchResult[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');useEffect(()=>{const value=term.trim();if(value.length<2){setRows([]);setError('');setBusy(false);return}let active=true;setBusy(true);const timer=window.setTimeout(()=>{void api<SearchResult[]>(`/api/search?q=${encodeURIComponent(value)}`).then(result=>{if(active)setRows(result)}).catch(e=>{if(active)setError(e instanceof Error?e.message:'Search failed')}).finally(()=>{if(active)setBusy(false)})},250);return()=>{active=false;window.clearTimeout(timer)}},[term]);return <section className="global-search" aria-label="Global search"><label htmlFor="global-search-input">Search records</label><input id="global-search-input" type="search" value={term} onChange={e=>{setTerm(e.target.value);setError('')}} placeholder="Tickets, enquiries, clients, Job Cards…" autoComplete="off" aria-controls="global-search-results" aria-expanded={term.trim().length>=2}/>{term.trim().length>=2&&<div id="global-search-results" className="global-search-results" role="listbox">{busy&&<p className="muted">Searching…</p>}{error&&<p role="alert">{error}</p>}{!busy&&!error&&rows.length===0&&<p className="muted">No matching records.</p>}{rows.map(row=><NavLink key={`${row.kind}-${row.id}`} to={row.href} onClick={()=>{setTerm('');setRows([])}} className="global-search-result"><strong>{row.title}</strong><span>{row.kind} · {row.subtitle} · {row.status.replaceAll('_',' ')}</span></NavLink>)}</div>}</section>}
