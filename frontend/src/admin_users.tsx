import { FormEvent, useEffect, useState } from 'react';
import { api } from './api';
import { Button, Card, Field, Status } from './components';
import type { User } from './types';

const roles = ['END_USER', 'MARKETING', 'MANAGEMENT', 'UNIT_REPRESENTATIVE', 'DIRECTOR', 'ADMIN'] as const;
type Department={id:number;code:string;name:string;active:boolean};
const message = (e: unknown) => e instanceof Error ? e.message : 'Unable to save changes.';

export function Users() {
  const [users, setUsers] = useState<User[]>([]);
  const [departments,setDepartments]=useState<Department[]>([]);
  const [selected, setSelected] = useState<User | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
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
      const values=Object.fromEntries(new FormData(e.currentTarget));await api('/api/users', { method: 'POST', body: JSON.stringify({...values,department_id:values.department_id?Number(values.department_id):null}) });
      setCreateOpen(false); setNotice('User created. End User accounts require activation before sign in.'); setError(''); void reload();
    } catch (e) { setError(message(e)); }
  }
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (!selected) return;
    try {
      const values=Object.fromEntries(new FormData(e.currentTarget));await api(`/api/users/${selected.id}`, { method: 'PATCH', body: JSON.stringify({...values,department_id:values.department_id?Number(values.department_id):null}) });
      setNotice('User details and role updated.'); setError(''); setSelected(null); void reload();
    } catch (e) { setError(message(e)); }
  }
  async function resetPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (!selected) return;
    const form = new FormData(e.currentTarget);
    try {
      await api(`/api/users/${selected.id}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: form.get('new_password') }) });
      setNotice(`Password reset for ${selected.email}; existing sessions were revoked.`); setError(''); setResetOpen(false);
    } catch (e) { setError(message(e)); }
  }
  async function action(path: string) {
    try { await api(path, { method: 'POST' }); setNotice('User account status updated.'); setError(''); void reload(); }
    catch (e) { setError(message(e)); }
  }

  return <>
    <header className="page-header"><div><p className="eyebrow">ADMIN WORKSPACE</p><h1>Users & Roles</h1><p className="muted">Create accounts, assign roles, and manage account access.</p></div><Button onClick={() => { setCreateOpen(v => !v); setSelected(null); }}>+ Add user</Button></header>
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <Card title="Departments"><form className="form-grid" onSubmit={addDepartment}><Field label="Department code" name="code" pattern="[A-Za-z][A-Za-z0-9_ ]*" required/><Field label="Department name" name="name" required/><Button type="submit">Add department</Button></form><p className="muted">{departments.map(d=>d.name).join(' · ')||'No departments yet.'}</p></Card>
    {createOpen && <Card title="Create user"><form className="form-grid" onSubmit={create}><Field label="Full name" name="full_name" required/><Field label="Email" name="email" type="email" required/><Field label="Mobile" name="mobile"/><Field label="Consultant ID" name="consultant_id"/><Field label="Temporary password" name="password" type="password" minLength={8} required/><label className="field"><span>Role</span><select name="role" defaultValue="END_USER">{roles.map(role => <option key={role} value={role}>{role.replaceAll('_', ' ')}</option>)}</select></label><label className="field"><span>Department</span><select name="department_id" defaultValue=""><option value="">No department</option>{departments.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label><Button type="submit">Create user</Button><Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button></form></Card>}
    {selected && <Card title={`Edit ${selected.full_name}`}><form className="form-grid" onSubmit={save}><Field label="Full name" name="full_name" defaultValue={selected.full_name} required/><Field label="Mobile" name="mobile" defaultValue={selected.mobile ?? ''}/><label className="field"><span>Role</span><select name="role" defaultValue={selected.role}>{roles.map(role => <option key={role} value={role}>{role.replaceAll('_', ' ')}</option>)}</select></label><label className="field"><span>Department</span><select name="department_id" defaultValue={selected.department_id??''}><option value="">No department</option>{departments.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label><Button type="submit">Save user</Button><Button type="button" variant="secondary" onClick={() => setSelected(null)}>Cancel</Button></form>{resetOpen ? <form className="inline-form" onSubmit={resetPassword}><Field label="New password" name="new_password" type="password" minLength={8} required/><Button type="submit">Reset password</Button><Button type="button" variant="quiet" onClick={() => setResetOpen(false)}>Cancel</Button></form> : <Button type="button" variant="secondary" onClick={() => setResetOpen(true)}>Set a new password</Button>}</Card>}
    <div className="table-wrap mobile-cards"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>{users.map(user => <tr key={user.id}><td data-label="Name">{user.full_name}</td><td data-label="Email">{user.email}</td><td data-label="Role"><Status>{user.role}</Status></td><td data-label="Status"><Status>{user.status}</Status></td><td data-label="Actions"><div className="actions"><Button variant="secondary" onClick={() => { setSelected(user); setCreateOpen(false); setResetOpen(false); setError(''); setNotice(''); }}>Edit</Button>{(user.status === 'PENDING_ACTIVATION' || user.status === 'INACTIVE') && <Button variant="secondary" onClick={() => void action(`/api/users/${user.id}/activate`)}>Activate</Button>}{user.status === 'LOCKED' && <Button variant="secondary" onClick={() => void action(`/api/users/${user.id}/unlock`)}>Unlock</Button>}{user.status === 'ACTIVE' && <Button variant="secondary" onClick={() => void action(`/api/users/${user.id}/deactivate`)}>Deactivate</Button>}</div></td></tr>)}</tbody></table></div>
  </>;
}
