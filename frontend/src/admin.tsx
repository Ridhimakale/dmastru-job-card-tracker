import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { api, downloadFile } from './api';
import { Button, Card, Field, Status } from './components';
import type { User } from './types';

type CardRow = { id:number; number:string; msme_id:number; location:string; project_start_date:string; project_end_date:string; required_md:string; required_hours:string; status:string };
type Msme = { id:number; name:string; msme_code:string; zed_id?:string; certificate_level:string };
type Milestone = { id:number; code:string; name:string; planned_visits:number; actual_visits:number; progress:string; status:string };
type Document = { id:number; document_type:string; file_name:string; version:number; status:string; remarks?:string };
type Approval = { id:number; stage:string; decision:string; comment?:string };
type Notice = { id:number; message:string; read_at?:string; created_at:string };
type Audit = { id:number; action:string; entity_type:string; details?:string; created_at:string };

const message = (e:unknown) => e instanceof Error ? e.message : 'Unable to save.';

function useLoad<T>(path:string) {
  const [data,setData] = useState<T|null>(null);
  const [error,setError] = useState('');
  const load = () => api<T>(path).then(setData).catch((e:unknown) => setError(message(e)));
  useEffect(() => { load(); }, [path]);
  return { data, error, reload: load };
}

export function AdminDashboard() {
  const {data,error} = useLoad<{kpis:Record<string,number>}>('/api/dashboard/admin');
  return <Page title="Admin Dashboard">
    {error && <ErrorMessage text={error}/>}
    <div className="stat-grid">
      {Object.entries(data?.kpis ?? {}).map(([k,v]) => <Card key={k}><p className="muted">{k.replaceAll('_',' ')}</p><strong className="stat">{v}</strong></Card>)}
    </div>
  </Page>;
}

export function Users() {
  const {data,reload,error} = useLoad<User[]>('/api/users');
  const [form,setForm] = useState(false);
  async function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget));
    try { await api('/api/users',{method:'POST',body:JSON.stringify(d)}); setForm(false); reload(); }
    catch(e) { alert(message(e)); }
  }
  return <Page title="Users & Roles" action={<Button onClick={() => setForm(!form)}>+ Add user</Button>}>
    {form && <Card title="Create user"><form className="form-grid" onSubmit={create}><Field label="Full name" name="full_name" required/><Field label="Email" name="email" type="email" required/><Field label="Password" name="password" type="password" minLength={8} required/><label className="field"><span>Role</span><select name="role"><option value="END_USER">End User / Consultant</option><option value="MANAGEMENT">Management</option><option value="ADMIN">Admin</option></select></label><Button type="submit">Create user</Button></form></Card>}
    <ErrorMessage text={error}/>
    <DataTable headers={['Name','Email','Role','Status','Action']} rows={(data ?? []).map(u => [u.full_name,u.email,<Status>{u.role}</Status>,<Status>{u.status}</Status>,u.status === 'PENDING_ACTIVATION' ? <Button variant="secondary" onClick={async () => { await api(`/api/users/${u.id}/activate`,{method:'POST'}); reload(); }}>Activate</Button> : u.status === 'ACTIVE' ? <Button variant="quiet" onClick={async () => { await api(`/api/users/${u.id}/deactivate`,{method:'POST'}); reload(); }}>Deactivate</Button> : '-'])}/>
  </Page>;
}

export function Msmes() {
  const {data,reload,error} = useLoad<Msme[]>('/api/msmes');
  const [form,setForm] = useState(false);
  async function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try { await api('/api/msmes',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))}); setForm(false); reload(); }
    catch(e) { alert(message(e)); }
  }
  return <Page title="MSME Master" action={<Button onClick={() => setForm(!form)}>+ Add MSME</Button>}>
    {form && <Card title="Create MSME"><form className="form-grid" onSubmit={create}><Field label="MSME name" name="name" required/><Field label="MSME code" name="msme_code" required/><Field label="ZED ID" name="zed_id"/><label className="field"><span>Certificate level</span><select name="certificate_level"><option>Bronze</option><option>Silver</option><option>Gold</option></select></label><Button type="submit">Save MSME</Button></form></Card>}
    <ErrorMessage text={error}/>
    <DataTable headers={['MSME','Code','ZED ID','Certificate']} rows={(data ?? []).map(m => [m.name,m.msme_code,m.zed_id ?? '-',m.certificate_level])}/>
  </Page>;
}

export function JobCards() {
  const cards = useLoad<CardRow[]>('/api/job-cards');
  const msmes = useLoad<Msme[]>('/api/msmes');
  const users = useLoad<User[]>('/api/users');
  const [selected,setSelected] = useState<CardRow|null>(null);
  const [form,setForm] = useState(false);
  async function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d:Record<string,unknown> = Object.fromEntries(new FormData(e.currentTarget));
    for (const key of ['msme_id','lead_consultant_id','co_consultant_id','subject_expert_id','unit_representative_id','director_id']) if (d[key]) d[key] = Number(d[key]);
    if (!d.co_consultant_id) delete d.co_consultant_id;
    if (!d.subject_expert_id) delete d.subject_expert_id;
    try { await api('/api/job-cards',{method:'POST',body:JSON.stringify(d)}); setForm(false); cards.reload(); }
    catch(e) { alert(message(e)); }
  }
  return <Page title="Job Cards" action={<Button onClick={() => setForm(!form)}>+ Create Job Card</Button>}>
    {form && <Card title="Create Job Card"><form className="form-grid" onSubmit={create}><Field label="Job card number" name="number" placeholder="DMP/HMR/001" required/><label className="field"><span>MSME</span><select name="msme_id">{(msmes.data ?? []).map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</select></label><Field label="Location" name="location" required/><Field label="Required mandays" name="required_md" type="number" min=".25" step=".25" required/><Field label="Start date" name="project_start_date" type="date" required/><Field label="End date" name="project_end_date" type="date" required/><label className="field"><span>Lead Consultant</span><select name="lead_consultant_id">{(users.data ?? []).filter(u => u.role === 'END_USER' && u.status === 'ACTIVE').map(u => <option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><label className="field"><span>Unit Representative</span><select name="unit_representative_id">{(users.data ?? []).filter(u => u.role === 'UNIT_REPRESENTATIVE' && u.status === 'ACTIVE').map(u => <option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><label className="field"><span>Director</span><select name="director_id">{(users.data ?? []).filter(u => u.role === 'DIRECTOR' && u.status === 'ACTIVE').map(u => <option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><Button type="submit">Create Job Card</Button></form></Card>}
    <ErrorMessage text={cards.error}/>
    <DataTable headers={['Number','Location','Mandays','Status','Action']} rows={(cards.data ?? []).map(c => [c.number,c.location,`${c.required_md} / ${c.required_hours}h`,<Status>{c.status}</Status>,<Button variant="secondary" onClick={() => setSelected(c)}>Manage</Button>])}/>
    {selected && <JobCardManage card={selected} close={() => setSelected(null)} reload={cards.reload}/>}
  </Page>;
}

function JobCardManage({card,close,reload}:{card:CardRow;close:()=>void;reload:()=>void}) {
  const milestones = useLoad<Milestone[]>(`/api/job-cards/${card.id}/milestones`);
  const docs = useLoad<Document[]>(`/api/job-cards/${card.id}/documents`);
  const approvals = useLoad<Approval[]>(`/api/job-cards/${card.id}/approvals`);
  async function milestone(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try { await api(`/api/job-cards/${card.id}/milestones`,{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))}); milestones.reload(); }
    catch(e) { alert(message(e)); }
  }
  async function reviewDocument(document:Document, decision:'APPROVED'|'REJECTED') {
    const comment = decision === 'APPROVED' ? 'Document reviewed and approved.' : prompt('Rejection comment');
    if (!comment) return;
    try { await api(`/api/documents/${document.id}/review`,{method:'POST',body:JSON.stringify({decision,comment})}); docs.reload(); }
    catch(e) { alert(message(e)); }
  }
  return <section className="drawer" aria-label="Job Card management">
    <Card title={`Manage ${card.number}`}>
      <Button variant="quiet" onClick={close}>Close</Button>
      <div className="two-column">
        <div>
          <h3>Milestones</h3>
          <DataTable headers={['Code','Planned','Actual','Progress']} rows={(milestones.data ?? []).map(m => [m.code,m.planned_visits,m.actual_visits,`${m.progress}%`])}/>
          <form className="inline-form" onSubmit={milestone}><Field label="Code" name="code" required/><Field label="Name" name="name" required/><Field label="Planned visits" name="planned_visits" type="number" min="1" required/><Button type="submit">Add milestone</Button></form>
        </div>
        <div>
          <h3>Documents</h3>
          <DataTable headers={['Type','File','Version','Status','Remarks','Actions']} rows={(docs.data ?? []).map(d => [d.document_type,d.file_name,`V${d.version}`,<Status>{d.status}</Status>,d.remarks ?? '-',<div className="actions"><Button variant="secondary" onClick={() => downloadFile(`/api/documents/${d.id}/download`,d.file_name)}>Download</Button>{d.status !== 'APPROVED' && <Button onClick={() => reviewDocument(d,'APPROVED')}>Approve</Button>}<Button variant="secondary" onClick={() => reviewDocument(d,'REJECTED')}>Reject</Button></div>])}/>
          <h3>Approvals</h3>
          <DataTable headers={['Stage','Decision','Comment']} rows={(approvals.data ?? []).map(a => [a.stage,a.decision,a.comment ?? '-'])}/>
        </div>
      </div>
      <Button variant="secondary" onClick={async () => { await api(`/api/job-cards/${card.id}`,{method:'DELETE'}); close(); reload(); }}>Archive Job Card</Button>
    </Card>
  </section>;
}

export function Settings() {
  const [value,setValue] = useState('8');
  const [saved,setSaved] = useState('');
  async function save(e:FormEvent) {
    e.preventDefault();
    try { const r = await api<{value:string}>('/api/settings/manday_hours',{method:'PUT',body:JSON.stringify({value})}); setSaved(`Saved: ${r.value} hours per manday`); }
    catch(e) { setSaved(message(e)); }
  }
  return <Page title="Settings"><Card title="Manday calculation"><form className="inline-form" onSubmit={save}><Field label="Hours per manday" type="number" min="1" value={value} onChange={e => setValue(e.target.value)}/><Button type="submit">Save setting</Button></form>{saved && <p role="status">{saved}</p>}</Card></Page>;
}

export function Workflow() {
  const cards = useLoad<CardRow[]>('/api/job-cards');
  const [card,setCard] = useState<CardRow|null>(null);
  return <Page title="Documents & Approvals"><DataTable headers={['Job Card','Status','Open']} rows={(cards.data ?? []).map(c => [c.number,<Status>{c.status}</Status>,<Button variant="secondary" onClick={() => setCard(c)}>Review</Button>])}/>{card && <JobCardManage card={card} close={() => setCard(null)} reload={cards.reload}/>}</Page>;
}

export function Notifications() {
  const {data,reload,error} = useLoad<Notice[]>('/api/notifications');
  return <Page title="Notifications"><ErrorMessage text={error}/>{(data ?? []).map(n => <Card key={n.id}><p><strong>{n.message}</strong><br/><small className="muted">{n.created_at}</small></p>{!n.read_at && <Button variant="secondary" onClick={async () => { await api(`/api/notifications/${n.id}/read`,{method:'POST'}); reload(); }}>Mark read</Button>}</Card>)}</Page>;
}

export function AuditLog() {
  const {data,error} = useLoad<Audit[]>('/api/audit-logs');
  return <Page title="Audit Log"><ErrorMessage text={error}/><DataTable headers={['When','Action','Entity','Details']} rows={(data ?? []).map(a => [a.created_at,a.action,a.entity_type,a.details ?? '-'])}/></Page>;
}

function Page({title,children,action}:{title:string;children:ReactNode;action?:ReactNode}) {
  return <><header className="page-header"><div><p className="eyebrow">ADMIN WORKSPACE</p><h1>{title}</h1></div>{action}</header>{children}</>;
}

function ErrorMessage({text}:{text:string}) {
  return text ? <p className="form-error" role="alert">{text}</p> : null;
}

function DataTable({headers,rows}:{headers:string[];rows:ReactNode[][]}) {
  return <div className="table-wrap"><table><thead><tr>{headers.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.length ? rows.map((r,i) => <tr key={i}>{r.map((cell,j) => <td key={j}>{cell}</td>)}</tr>) : <tr><td colSpan={headers.length} className="muted">No records found.</td></tr>}</tbody></table></div>;
}
