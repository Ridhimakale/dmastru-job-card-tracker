import { useState } from 'react';
import { Msmes } from './admin';
import { Users } from './admin_users';

type Section='home'|'msmes'|'users';

export function MasterData(){
  const [section,setSection]=useState<Section>('home');
  if(section==='msmes')return <Msmes onBack={()=>setSection('home')}/>;
  if(section==='users')return <Users onBack={()=>setSection('home')}/>;
  return <main className="page">
    <header className="page-header"><div><p className="eyebrow">ADMIN WORKSPACE</p><h1>Master Data</h1><p className="muted">Manage the core organization and account records in one place.</p></div></header>
    <div className="marketing-actions-home module-actions-home">
      <button type="button" className="marketing-action-card" onClick={()=>setSection('msmes')}>
        <span className="marketing-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M8 4v16M8 9h12M8 14h12"/></svg></span>
        <span className="marketing-action-copy"><strong>MSME Master</strong><small>Check and manage MSME records, contacts, and certificate details.</small></span>
      </button>
      <button type="button" className="marketing-action-card marketing-action-create" onClick={()=>setSection('users')}>
        <span className="marketing-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.4-3 2.2-4.5 5.5-4.5s5.1 1.5 5.5 4.5M16 5.5a3 3 0 0 1 0 5.8M17 14.5c2.2.4 3.4 1.9 3.6 4.5"/></svg></span>
        <span className="marketing-action-copy"><strong>Users & Roles</strong><small>Manage team accounts, roles, departments, and access.</small></span>
      </button>
    </div>
  </main>;
}
