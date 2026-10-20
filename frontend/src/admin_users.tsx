import { FormEvent, useEffect, useState } from 'react';
import { api } from './api';
import { Button, Card, Field, Status } from './components';
import type { User } from './types';

const roles = ['END_USER', 'MARKETING', 'FINANCE', 'MANAGEMENT', 'UNIT_REPRESENTATIVE', 'ADMIN'] as const;
type Department={id:number;code:string;name:string;active:boolean};
const message = (e: unknown) => e instanceof Error ? e.message : 'Unable to save changes.';

export function Users({onBack}:{onBack?:()=>void}={}) {
  const [users, setUsers] = useState<User[]>([]);
  const [departments,setDepartments]=useState<Department[]>([]);
  const [selected, setSelected] = useState<User | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [view,setView]=useState<'home'|'records'|'form'>(()=>onBack?'records':'home');
  const [resetOpen, setResetOpen] = useState(false);
  const [resetLink,setResetLink]=useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function reload() {
    try { setUsers(await api<User[]>('/api/users')); setError(''); }
    catch (e) { setError(message(e)); }
  }
  useEffect(() => { void reload(); }, []);
  useEffect(()=>{api<Department[]>('/api/departments').then(setDepartments).catch(e=>setError(message(e)))},[]);
  async function addDepartment(e:FormEvent<HTMLFormElement>){e.preventDefault();const target=e.currentTarget,form=new FormData(target);try{await api('/api/departments',{method:'POST',body:JSON.stringify({code:String(form.get('code')).trim().toUpperCase().replaceAll(' ','_'),name:form.get('name')})});target.reset();setNotice('Department created.');setDepartments(await api<Department[]>('/api/departments'))}catch(e){setError(message(e))}}

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try {
      const values=Object.fromEntries(new FormData(e.currentTarget));const result=await api<{initial_password_email_sent:boolean;message:string}>('/api/users', { method: 'POST', body: JSON.stringify({...values,department_id:values.department_id?Number(values.department_id):null}) });
      setCreateOpen(false);setView('records'); setNotice(`${result.message}${values.role==='END_USER'?' End User accounts also require Admin activation before sign-in.':''}`); setError(''); void reload();
    } catch (e) { setError(message(e)); }
  }
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (!selected) return;
    try {
      const values=Object.fromEntries(new FormData(e.currentTarget));await api(`/api/users/${selected.id}`, { method: 'PATCH', body: JSON.stringify({...values,department_id:values.department_id?Number(values.department_id):null}) });
      setNotice('User details and role updated.'); setError(''); setSelected(null); void reload();
    } catch (e) { setError(message(e)); }
  }
  async function resetPassword() {
    if (!selected) return;
    try {
      const result=await api<{email_sent:boolean;message:string;development_reset_token?:string}>(`/api/users/${selected.id}/reset-password`, { method: 'POST', body: JSON.stringify({}) });
      setNotice(`${result.message} ${selected.email}`);setResetLink(result.development_reset_token?`/reset-password?token=${encodeURIComponent(result.development_reset_token)}`:'');setError(''); setResetOpen(false);
    } catch (e) { setError(message(e)); }
  }
  async function resetAuthenticator() {
    if (!selected) return;
    try {
      await api(`/api/users/${selected.id}/reset-mfa`, { method: 'POST' });
      setNotice(`Authenticator reset for ${selected.email}; they will enroll again at next sign-in.`);
      setError(''); setSelected(null); void reload();
    } catch (e) { setError(message(e)); }
  }
  async function action(path: string) {
    try { await api(path, { method: 'POST' }); setNotice('User account status updated.'); setError(''); void reload(); }
    catch (e) { setError(message(e)); }
  }

  return <>
    <header className="page-header"><div><p className="eyebrow">ADMIN WORKSPACE</p><h1>Users & Roles</h1><p className="muted">Manage user accounts and permissions.</p></div><div className="actions">{view==='records'&&<Button onClick={()=>{setCreateOpen(true);setSelected(null);setView('form')}}>Add New User</Button>}{view!=='home'&&<Button variant="secondary" onClick={()=>{setCreateOpen(false);setSelected(null);if(onBack)onBack();else setView('home')}}>{onBack?'Back to Master Data':'Home'}</Button>}</div></header>
    {view==='home'&&<div className="marketing-actions-home module-actions-home"><button type="button" className="marketing-action-card" onClick={()=>setView('records')}><span className="marketing-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.4-3 2.2-4.5 5.5-4.5s5.1 1.5 5.5 4.5M16 5.5a3 3 0 0 1 0 5.8M17 14.5c2.2.4 3.4 1.9 3.6 4.5"/></svg></span><span className="marketing-action-copy"><strong>Users & Roles</strong><small>Check user accounts, roles, departments, and access status.</small></span></button><button type="button" className="marketing-action-card marketing-action-create" onClick={()=>{setCreateOpen(true);setSelected(null);setView('form')}}><span className="marketing-action-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></span><span className="marketing-action-copy"><strong>Add New User</strong><small>Create an account and assign its role and department.</small></span></button></div>}
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{resetLink&&<p>Local password setup: <a href={resetLink}>Open reset link</a></p>}
    {view==='records'&&<Card title="Departments"><form className="form-grid" onSubmit={addDepartment}><Field label="Department code" name="code" pattern="[A-Za-z][A-Za-z0-9_ ]*" required/><Field label="Department name" name="name" required/><Button type="submit">Add department</Button></form><p className="muted">{departments.map(d=>d.name).join(' · ')||'No departments yet.'}</p></Card>}
    {createOpen && view==='form' && <Card title="Create user"><form className="form-grid" onSubmit={create}><Field label="Full name" name="full_name" required/><Field label="Email" name="email" type="email" required/><Field label="Mobile" name="mobile"/><Field label="Consultant ID" name="consultant_id"/><Field label="Initial password (emailed to user)" name="password" type="password" minLength={8} required/><p className="muted">The email includes this initial password and a one-time link to set a confirmed permanent password. The user then enrolls a new authenticator.</p><label className="field"><span>Role</span><select name="role" defaultValue="END_USER">{roles.map(role => <option key={role} value={role}>{role.replaceAll('_', ' ')}</option>)}</select></label><label className="field"><span>Department</span><select name="department_id" defaultValue=""><option value="">No department</option>{departments.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label><Button type="submit">Create user</Button><Button type="button" variant="secondary" onClick={() => {setCreateOpen(false);if(onBack)onBack();else setView('home')}}>Cancel</Button></form></Card>}
    {selected && view==='records' && <Card title={`Edit ${selected.full_name}`}><form className="form-grid" onSubmit={save}><Field label="Full name" name="full_name" defaultValue={selected.full_name} required/><Field label="Mobile" name="mobile" defaultValue={selected.mobile ?? ''}/><label className="field"><span>Role</span><select name="role" defaultValue={selected.role}>{roles.map(role => <option key={role} value={role}>{role.replaceAll('_', ' ')}</option>)}</select></label><label className="field"><span>Department</span><select name="department_id" defaultValue={selected.department_id??''}><option value="">No department</option>{departments.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label><Button type="submit">Save user</Button><Button type="button" variant="secondary" onClick={() => setSelected(null)}>Cancel</Button></form><p className="muted">Authenticator MFA: {selected.mfa_enabled?'Enabled':'Not enrolled'}</p>{selected.mfa_enabled&&<Button type="button" variant="secondary" onClick={()=>void resetAuthenticator()}>Reset authenticator</Button>}{resetOpen?<div className="inline-form"><p className="muted">Send this user a secure link to choose and confirm a new permanent password. It expires in 30 minutes.</p><Button type="button" onClick={()=>void resetPassword()}>Send password setup email</Button><Button type="button" variant="quiet" onClick={()=>setResetOpen(false)}>Cancel</Button></div>:<Button type="button" variant="secondary" onClick={()=>{setResetLink('');setResetOpen(true)}}>Send password reset email</Button>}</Card>}
    {view==='records'&&<div className="table-wrap mobile-cards"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>{users.map(user => <tr key={user.id}><td data-label="Name">{user.full_name}</td><td data-label="Email">{user.email}</td><td data-label="Role"><Status>{user.role}</Status></td><td data-label="Status"><Status>{user.status}</Status></td><td data-label="Actions"><div className="actions"><Button variant="secondary" onClick={() => { setSelected(user); setCreateOpen(false); setResetOpen(false); setError(''); setNotice(''); }}>Edit</Button>{(user.status === 'PENDING_ACTIVATION' || user.status === 'INACTIVE') && <Button variant="secondary" onClick={() => void action(`/api/users/${user.id}/activate`)}>Activate</Button>}{user.status === 'LOCKED' && <Button variant="secondary" onClick={() => void action(`/api/users/${user.id}/unlock`)}>Unlock</Button>}{user.status === 'ACTIVE' && <Button variant="secondary" onClick={() => void action(`/api/users/${user.id}/deactivate`)}>Deactivate</Button>}</div></td></tr>)}</tbody></table></div>}
  </>;
}
