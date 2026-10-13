import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, downloadFile } from './api';
import { Button, Card, Field, Status } from './components';
import type { User } from './types';

type CardRow = { id:number; number:string; msme_id:number; location:string; project_start_date:string; project_end_date:string; required_md:string; hours_per_manday:string; required_hours:string; completed_md:string; remaining_md:string; progress:string; overrun:boolean; unit_representative_id:number|null; director_id:number|null; remarks?:string|null; status:string };
type EnquiryRow={id:string;number:string;company_name:string;status:string};
type Msme = { id:number; name:string; msme_code:string; zed_id?:string; certificate_level:string; address?:string; city?:string; state?:string; contact_person?:string;email?:string;phone?:string;industry?:string;unit_name?:string;status:string };
type AssignmentRow = { id:number; user_id:number; full_name:string; assignment_type:string;assigned_at:string;allocation_start_date:string|null;allocation_end_date:string|null;ended_at:string|null;active:boolean };
type ServicePackage={id:number;code:string;name:string;planned_mandays:string;commercial_amount:string;active:boolean};
type Milestone = { id:number; code:string; name:string; description?:string; planned_visits:number;planned_mandays?:string|null;amount?:string|null;payment_percent?:string|null;activities?:string[];responsible_consultant_id?:number|null; actual_visits:number; progress:string; status:string; start_date?:string|null; closing_date?:string|null; payment_date?:string|null; portal_report_submission_date?:string|null };
type Visit = { id:number; visit_number:number; visit_date:string; working_minutes:number; work_details:string; rating_code:string; status:string; attachment_name?:string };
type MandaySummary = { completed_hours:string; completed_md:string; remaining_hours:string; remaining_md:string; progress:string; overrun:boolean };
type Document = { id:number; job_card_id:number; document_type:string; file_name:string; version:number; status:string; remarks?:string;reviewed_by:number|null;reviewed_at:string|null };
type RecordDocument={id:number;milestone_id:number|null;document_type:string;file_name:string;version:number;status:string;remarks:string|null;reviewed_by:number|null;reviewed_at:string|null};
type Approval = { id:number; stage:string; decision:string; comment?:string };
type Notice = { id:number; message:string; link?:string; read_at?:string; created_at:string };
type Audit = { id:number; action:string; entity_type:string; entity_id?:number; entity_ref?:string; details?:string; created_at:string };

const message = (e:unknown) => e instanceof Error ? e.message : 'Unable to save.';

function useLoad<T>(path:string) {
  const [data,setData] = useState<T|null>(null);
  const [error,setError] = useState('');
  const [loading,setLoading]=useState(true);
  const load = () => {setLoading(true);return api<T>(path).then(value=>{setData(value);setError('')}).catch((e:unknown) => setError(message(e))).finally(()=>setLoading(false))};
  useEffect(() => { load(); }, [path]);
  return { data, error, loading, reload: load };
}

export function AdminDashboard() {
  const {data,error,loading} = useLoad<{kpis:Record<string,number>;consultant_workload:{user_id:number;full_name:string;active_job_cards:number;planned_mandays:string}[];consultant_availability:{user_id:number;full_name:string;available:boolean}[];upcoming_visits:{visit_id:number;visit_number:number;visit_date:string;job_card_id:number;job_card_number:string;consultant:string|null}[]}>('/api/dashboard/admin');
  return <Page title="Admin Dashboard">
    {error && <ErrorMessage text={error}/>}
    {loading&&<p role="status">Loading dashboard...</p>}
    <div className="stat-grid">
      {Object.entries(data?.kpis ?? {}).map(([k,v]) => <Card key={k}><p className="muted">{k.replaceAll('_',' ')}</p><strong className="stat">{v}</strong></Card>)}
    </div>
    <div className="two-column"><Card title="Consultant workload and availability"><DataTable headers={['Consultant','Availability','Active Job Cards','Planned mandays']} rows={(data?.consultant_availability??[]).map(c=>[c.full_name,<Status>{c.available?'AVAILABLE':'ALLOCATED'}</Status>,data?.consultant_workload.find(w=>w.user_id===c.user_id)?.active_job_cards??0,data?.consultant_workload.find(w=>w.user_id===c.user_id)?.planned_mandays??'0'])}/></Card><Card title="Upcoming visits"><DataTable headers={['Date','Job Card','Consultant']} rows={(data?.upcoming_visits??[]).map(v=>[v.visit_date,<Link to={`/job-cards/${v.job_card_id}`}>{v.job_card_number}</Link>,v.consultant??'-'])}/></Card></div>
    <div className="actions"><Link className="button primary" to="/admin/job-cards">Create or manage Job Cards</Link><Link className="button secondary" to="/admin/users">Manage users</Link><Link className="button secondary" to="/admin/workflow">Review documents</Link></div>
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
  const [search,setSearch]=useState('');const [term,setTerm]=useState('');const [statusFilter,setStatusFilter]=useState('');const [certificate,setCertificate]=useState('');
  useEffect(()=>{const timer=window.setTimeout(()=>setTerm(search),250);return()=>window.clearTimeout(timer)},[search]);
  const params=new URLSearchParams();if(term)params.set('search',term);if(statusFilter)params.set('status',statusFilter);if(certificate)params.set('certificate_level',certificate);
  const {data,reload,error} = useLoad<Msme[]>(`/api/msmes?${params.toString()}`);
  const [form,setForm] = useState(false);
  const [selected,setSelected] = useState<Msme|null>(null);
  async function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try { await api(selected?`/api/msmes/${selected.id}`:'/api/msmes',{method:selected?'PATCH':'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))}); setForm(false); setSelected(null); reload(); }
    catch(e) { alert(message(e)); }
  }
  const openForm=(msme?:Msme)=>{setSelected(msme??null);setForm(true)};
  return <Page title="MSME Master" action={<Button onClick={() => openForm()}>+ Add MSME</Button>}>
    {form && <Card title={selected?`Edit ${selected.name}`:'Create MSME'}><form className="form-grid" onSubmit={create}><Field label="MSME name" name="name" defaultValue={selected?.name} required/><Field label="MSME code" name="msme_code" defaultValue={selected?.msme_code} required/><Field label="ZED ID" name="zed_id" defaultValue={selected?.zed_id??''}/><label className="field"><span>Certificate level</span><select name="certificate_level" defaultValue={selected?.certificate_level??'Bronze'}><option>Bronze</option><option>Silver</option><option>Gold</option></select></label><Field label="Address" name="address" defaultValue={selected?.address??''}/><Field label="City" name="city" defaultValue={selected?.city??''}/><Field label="State" name="state" defaultValue={selected?.state??''}/><Field label="Contact person" name="contact_person" defaultValue={selected?.contact_person??''}/><Field label="Email" name="email" type="email" defaultValue={selected?.email??''}/><Field label="Phone" name="phone" defaultValue={selected?.phone??''}/><Field label="Industry" name="industry" defaultValue={selected?.industry??''}/><Field label="Unit name" name="unit_name" defaultValue={selected?.unit_name??''}/>{selected&&<label className="field"><span>Status</span><select name="status" defaultValue={selected.status}><option>ACTIVE</option><option>INACTIVE</option></select></label>}<Button type="submit">Save MSME</Button><Button type="button" variant="secondary" onClick={()=>{setForm(false);setSelected(null)}}>Cancel</Button></form></Card>}
    <div className="actions"><Field label="Search name, code, ZED ID, city or state" value={search} onChange={e=>setSearch(e.target.value)}/><label className="field"><span>Certificate level</span><select value={certificate} onChange={e=>setCertificate(e.target.value)}><option value="">All levels</option><option>Bronze</option><option>Silver</option><option>Gold</option></select></label><label className="field"><span>Status</span><select value={statusFilter} onChange={e=>setStatusFilter(e.target.value)}><option value="">All statuses</option><option>ACTIVE</option><option>INACTIVE</option></select></label></div>
    <ErrorMessage text={error}/>
    <DataTable headers={['MSME','Code','Contact','Industry / Unit','Address','Location','Certificate','Status','Actions']} rows={(data ?? []).map(m => [m.name,m.msme_code,<>{m.contact_person??'-'}<br/>{m.email??''}<br/>{m.phone??''}</>,`${m.industry??'-'} / ${m.unit_name??'-'}`,m.address??'-',`${m.city??'-'}, ${m.state??'-'}`,m.certificate_level,<Status>{m.status}</Status>,<Button variant="secondary" onClick={()=>openForm(m)}>Edit</Button>])}/>
  </Page>;
}

export function JobCards() {
  const [searchParams,setSearchParams]=useSearchParams();const requestedCard=searchParams.get('card'),requestedFocus=searchParams.get('focus');
  const [search,setSearch]=useState(''); const [term,setTerm]=useState(''); const [status,setStatus]=useState(''); const [consultant,setConsultant]=useState('');const [startDate,setStartDate]=useState('');const [endDate,setEndDate]=useState(''); const [page,setPage]=useState(1); const pageSize=20;
  useEffect(()=>{const timer=window.setTimeout(()=>{setTerm(search);setPage(1)},250);return()=>window.clearTimeout(timer)},[search]);
  const query=new URLSearchParams({page:String(page),page_size:String(pageSize)}); if(term)query.set('search',term);if(status)query.set('status',status);if(consultant)query.set('consultant_id',consultant);if(startDate)query.set('start_date',startDate);if(endDate)query.set('end_date',endDate);
  const cards = useLoad<CardRow[]>(`/api/job-cards?${query.toString()}`);
  const msmes = useLoad<Msme[]>('/api/msmes');
  const users = useLoad<User[]>('/api/users');
  const enquiries=useLoad<{items:EnquiryRow[]}>('/api/enquiries?status=QUALIFIED&page_size=100');
  const sourceTickets=useLoad<{items:{id:string;number:string;title:string;enquiry_id:string|null;job_card_id:number|null}[]}>('/api/tickets?page_size=100');
  const packages=useLoad<ServicePackage[]>('/api/packages');
  const [selected,setSelected] = useState<CardRow|null>(null);
  const [form,setForm] = useState(false);
  const [hoursPerManday,setHoursPerManday]=useState('8');
  const [packageCode,setPackageCode]=useState('');
  useEffect(()=>{api<{value:string}>('/api/settings/manday_hours').then(x=>setHoursPerManday(x.value)).catch(()=>setHoursPerManday('8'))},[]);
  useEffect(()=>{if(!requestedCard)return;api<CardRow>(`/api/job-cards/${requestedCard}`).then(setSelected).catch(e=>alert(message(e)))},[requestedCard]);
  function closeManage(){setSelected(null);setSearchParams(current=>{current.delete('card');return current},{replace:true})}
  async function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d:Record<string,unknown> = Object.fromEntries(new FormData(e.currentTarget));
    for (const key of ['msme_id','lead_consultant_id','co_consultant_id','subject_expert_id','unit_representative_id','director_id']) { if (d[key]) d[key] = Number(d[key]); else delete d[key]; }
    for(const key of ['source_ticket_id','source_enquiry_id'])if(!d[key])delete d[key];
    if(d.package_code)delete d.required_md;
    d.hours_per_manday=Number(d.hours_per_manday);
    try { await api('/api/job-cards',{method:'POST',body:JSON.stringify(d)}); setForm(false); cards.reload(); }
    catch(e) { alert(message(e)); }
  }
  return <Page title="Job Cards" action={<Button onClick={() => setForm(!form)}>+ Create Job Card</Button>}>
    {requestedFocus==='milestones'&&<p className="notice" role="status">Select Manage on a Job Card to view and configure its milestones.</p>}{requestedFocus==='allocation'&&<p className="notice" role="status">Select Manage on a Job Card to view or update consultant allocation.</p>}
    {form && <Card title="Create Job Card"><form className="form-grid" onSubmit={create}><p className="muted">Job Card number is generated automatically after creation.</p><label className="field"><span>Source ticket (optional)</span><select name="source_ticket_id" defaultValue=""><option value="">No source ticket</option>{(sourceTickets.data?.items??[]).filter(t=>!t.job_card_id).map(t=><option key={t.id} value={t.id}>{t.number} - {t.title}</option>)}</select></label><label className="field"><span>Convert qualified enquiry (optional)</span><select name="source_enquiry_id" defaultValue=""><option value="">Create without an enquiry</option>{(enquiries.data?.items??[]).map(e=><option key={e.id} value={e.id}>{e.number} ... {e.company_name}</option>)}</select></label><Field label="Project name" name="project_name" maxLength={200}/><label className="field"><span>Service package</span><select name="package_code" value={packageCode} onChange={e=>setPackageCode(e.target.value)}><option value="">No package template</option>{(packages.data??[]).map(p=><option key={p.code} value={p.code}>{p.code} ? {p.name} ? {p.planned_mandays} MD / INR {Number(p.commercial_amount).toLocaleString('en-IN')}</option>)}</select></label><label className="field"><span>Project scope</span><textarea name="scope" maxLength={10000} disabled={Boolean(packageCode)} placeholder={packageCode?'Scope is set by the selected package':''}/></label><label className="field"><span>MSME</span><select name="msme_id" required defaultValue="">{(msmes.data ?? []).filter(m=>m.status==='ACTIVE').map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</select></label><Field label="Location" name="location" required/><Field label="Required mandays" name="required_md" type="number" min=".01" step=".01" required={!packageCode} disabled={Boolean(packageCode)} placeholder={packageCode?`${packages.data?.find(p=>p.code===packageCode)?.planned_mandays} from package`:undefined}/><Field label="Hours per manday" name="hours_per_manday" type="number" min=".01" max="24" step=".01" defaultValue={hoursPerManday} required/><Field label="Start date" name="project_start_date" type="date" required/><Field label="End date" name="project_end_date" type="date" required/><label className="field"><span>Admin remarks</span><textarea name="remarks" maxLength={10000}/></label>{[['Lead Consultant','lead_consultant_id',true],['Co-Consultant','co_consultant_id',false],['Subject Expert','subject_expert_id',false]].map(([label,key,required])=><label className="field" key={String(key)}><span>{String(label)}</span><select name={String(key)} required={Boolean(required)} defaultValue=""><option value="">{required?'Select consultant':'Unassigned'}</option>{(users.data??[]).filter(u=>u.role==='END_USER'&&u.status==='ACTIVE').map(u=><option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label>)}<label className="field"><span>Unit Representative</span><select name="unit_representative_id" required defaultValue=""><option value="">Select unit representative</option>{(users.data ?? []).filter(u => u.role === 'UNIT_REPRESENTATIVE' && u.status === 'ACTIVE').map(u => <option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><label className="field"><span>Director</span><select name="director_id" required defaultValue=""><option value="">Select director</option>{(users.data ?? []).filter(u => u.role === 'DIRECTOR' && u.status === 'ACTIVE').map(u => <option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><Button type="submit">Create Job Card</Button><Button type="button" variant="secondary" onClick={()=>setForm(false)}>Cancel</Button></form></Card>}
    <div className="actions"><Field label="Search number, MSME, ZED ID or location" value={search} onChange={e=>setSearch(e.target.value)}/><label className="field"><span>Status</span><select value={status} onChange={e=>{setStatus(e.target.value);setPage(1)}}><option value="">All statuses</option><option>AWAITING_PAYMENT</option><option>ASSIGNED</option><option>IN_PROGRESS</option><option>ON_HOLD</option><option>PENDING_APPROVAL</option><option>COMPLETED</option><option>CANCELLED</option></select></label><label className="field"><span>Consultant</span><select value={consultant} onChange={e=>{setConsultant(e.target.value);setPage(1)}}><option value="">All consultants</option>{(users.data??[]).filter(u=>u.role==='END_USER').map(u=><option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><Field label="Project starts from" type="date" value={startDate} onChange={e=>{setStartDate(e.target.value);setPage(1)}}/><Field label="Project ends by" type="date" value={endDate} onChange={e=>{setEndDate(e.target.value);setPage(1)}}/></div>
    <ErrorMessage text={cards.error}/>
    <DataTable headers={['Job Card','MSME ID','Location','Dates','Required MD','Completed MD','Remaining MD','Status','Action']} rows={(cards.data ?? []).map(c => [c.number,c.msme_id,c.location,`${c.project_start_date} â€“ ${c.project_end_date}`,`${c.required_md} MD Â· ${c.hours_per_manday} h/MD`,c.completed_md,c.remaining_md,<Status>{c.status}</Status>,<Button variant="secondary" onClick={() => {setSelected(c);setSearchParams(current=>{current.set('card',String(c.id));return current},{replace:true})}}>Manage</Button>])}/>
    <div className="actions"><Button variant="secondary" disabled={page<=1} onClick={()=>setPage(page-1)}>Previous</Button><span>Page {page}</span><Button variant="secondary" disabled={(cards.data?.length??0)<pageSize} onClick={()=>setPage(page+1)}>Next</Button></div>
    {selected && <JobCardManage card={selected} users={users.data??[]} msmes={msmes.data??[]} close={closeManage} reload={cards.reload}/>}
  </Page>;
}

function JobCardManage({card,users,msmes,close,reload}:{card:CardRow;users:User[];msmes:Msme[];close:()=>void;reload:()=>void}) {
  const milestones = useLoad<Milestone[]>(`/api/job-cards/${card.id}/milestones`);
  const visits=useLoad<Visit[]>(`/api/job-cards/${card.id}/visits`);
  const mandays=useLoad<MandaySummary>(`/api/job-cards/${card.id}/mandays`);
  const docs = useLoad<Document[]>(`/api/job-cards/${card.id}/documents`);
  const recordDocs=useLoad<RecordDocument[]>(`/api/job-cards/${card.id}/record-documents`);
  const approvals = useLoad<Approval[]>(`/api/job-cards/${card.id}/approvals`);
  const assignments=useLoad<AssignmentRow[]>(`/api/job-cards/${card.id}/assignments`);
  const [documentError,setDocumentError] = useState('');
  const [notice,setNotice]=useState('');
  const [error,setError]=useState('');
  const [assignmentType,setAssignmentType]=useState('LEAD_CONSULTANT');
  const [reassignUser,setReassignUser]=useState('');
  const [editingMilestone,setEditingMilestone]=useState<Milestone|null>(null);
  async function milestone(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();const form=e.currentTarget;
    const data:Record<string,unknown>=Object.fromEntries(new FormData(e.currentTarget));data.planned_visits=Number(data.planned_visits);data.planned_mandays=Number(data.planned_mandays);data.amount=Number(data.amount);data.payment_percent=Number(data.payment_percent);data.activities=String(data.activities??'').split(',').map(x=>x.trim()).filter(Boolean);if(data.responsible_consultant_id)data.responsible_consultant_id=Number(data.responsible_consultant_id);else delete data.responsible_consultant_id;for(const key of ['start_date','closing_date','portal_report_submission_date'])if(data[key]==='')data[key]=null;
    try { await api(`/api/job-cards/${card.id}/milestones`,{method:'POST',body:JSON.stringify(data)}); milestones.reload();form.reset(); }
    catch(e) { alert(message(e)); }
  }
  async function saveMilestone(e:FormEvent<HTMLFormElement>){e.preventDefault();if(!editingMilestone)return;const data:Record<string,unknown>=Object.fromEntries(new FormData(e.currentTarget));data.planned_visits=Number(data.planned_visits);data.planned_mandays=Number(data.planned_mandays);data.amount=Number(data.amount);data.payment_percent=Number(data.payment_percent);data.activities=String(data.activities??'').split(',').map(x=>x.trim()).filter(Boolean);if(data.responsible_consultant_id)data.responsible_consultant_id=Number(data.responsible_consultant_id);else data.responsible_consultant_id=null;for(const key of ['start_date','closing_date','portal_report_submission_date'])if(data[key]==='')data[key]=null;try{await api(`/api/milestones/${editingMilestone.id}`,{method:'PATCH',body:JSON.stringify(data)});setEditingMilestone(null);milestones.reload()}catch(e){setError(message(e))}}
  async function reviewDocument(document:Document, decision:'APPROVED'|'RETURNED'|'REJECTED') {
    const comment = decision === 'APPROVED' ? 'Document reviewed and approved.' : prompt(decision==='RETURNED'?'Reason for returning this document for correction':'Reason for rejecting this document');
    if (!comment) return;
    try { await api(`/api/documents/${document.id}/review`,{method:'POST',body:JSON.stringify({decision,comment})}); docs.reload(); }
    catch(e) { alert(message(e)); }
  }
  async function downloadDocument(document:Document) {
    setDocumentError('');
    try { await downloadFile(`/api/documents/${document.id}/download`,document.file_name); }
    catch(e) { setDocumentError(message(e)); }
  }
  async function saveCard(e:FormEvent<HTMLFormElement>){e.preventDefault();const raw=Object.fromEntries(new FormData(e.currentTarget));const data:Record<string,unknown>={...raw,msme_id:Number(raw.msme_id),required_md:Number(raw.required_md),hours_per_manday:Number(raw.hours_per_manday),unit_representative_id:Number(raw.unit_representative_id),director_id:Number(raw.director_id),remarks:raw.remarks||null};if(raw.status===card.status)delete data.status;try{await api(`/api/job-cards/${card.id}`,{method:'PATCH',body:JSON.stringify(data)});setNotice('Job Card updated.');setError('');close();reload()}catch(e){setError(message(e))}}
  async function reassign(e:FormEvent<HTMLFormElement>){e.preventDefault();try{await api(`/api/job-cards/${card.id}/reassign`,{method:'POST',body:JSON.stringify({assignment_type:assignmentType,user_id:Number(reassignUser),reason:new FormData(e.currentTarget).get('reason'),allocation_start_date:new FormData(e.currentTarget).get('allocation_start_date')||null,allocation_end_date:new FormData(e.currentTarget).get('allocation_end_date')||null})});setNotice('Consultant assignment updated.');setError('');assignments.reload();reload()}catch(e){setError(message(e))}}
  async function reopen(){const reason=prompt('Reason for reopening this completed Job Card:');if(!reason?.trim())return;try{await api(`/api/job-cards/${card.id}/reopen`,{method:'POST',body:JSON.stringify({reason})});setError('');close();reload()}catch(e){setError(message(e))}}
  return <section className="drawer" aria-label="Job Card management">
    <Card title={`Manage ${card.number}`}>
      <Button variant="quiet" onClick={close}>Close</Button>
      {error&&<ErrorMessage text={error}/>} {notice&&<p role="status">{notice}</p>}
      <h3>Manday summary</h3><div className="stat-grid"><Card><span>Required</span><strong>{card.required_md} MD Â· {card.required_hours} h</strong></Card><Card><span>Completed</span><strong>{mandays.data?.completed_md??'â€”'} MD Â· {mandays.data?.completed_hours??'â€”'} h</strong></Card><Card><span>Remaining</span><strong>{mandays.data?.remaining_md??'â€”'} MD Â· {mandays.data?.remaining_hours??'â€”'} h</strong></Card><Card><span>Progress</span><strong>{mandays.data?.progress??'â€”'}%</strong></Card></div>
      <h3>Visit activity</h3><DataTable headers={['Visit','Date','Duration','Rating','Status','Work']} rows={(visits.data??[]).map(v=>[v.visit_number,v.visit_date,`${Math.floor(v.working_minutes/60)}h ${v.working_minutes%60}m`,v.rating_code,<Status>{v.status}</Status>,v.work_details])}/>
      {card.status==='COMPLETED'?<Button onClick={()=>void reopen()}>Reopen Job Card</Button>:<>
      <h3>Job Card details</h3><form className="form-grid" onSubmit={saveCard}><p className="muted">Job Card number: <strong>{card.number}</strong> (system generated)</p><label className="field"><span>MSME</span><select name="msme_id" defaultValue={card.msme_id}>{msmes.filter(m=>m.status==='ACTIVE'||m.id===card.msme_id).map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select></label><Field label="Location" name="location" defaultValue={card.location} required/><Field label="Project start" name="project_start_date" type="date" defaultValue={card.project_start_date} required/><Field label="Project end" name="project_end_date" type="date" defaultValue={card.project_end_date} required/><Field label="Required mandays" name="required_md" type="number" min=".01" step=".01" defaultValue={card.required_md} required/><Field label="Hours per manday" name="hours_per_manday" type="number" min=".01" max="24" step=".01" defaultValue={card.hours_per_manday} required/><label className="field"><span>Admin remarks</span><textarea name="remarks" defaultValue={card.remarks??''} maxLength={10000}/></label><label className="field"><span>Unit Representative</span><select name="unit_representative_id" defaultValue={card.unit_representative_id??''} required>{users.filter(u=>u.role==='UNIT_REPRESENTATIVE').map(u=><option value={u.id} key={u.id}>{u.full_name}{u.status==='ACTIVE'?'':' (inactive)'}</option>)}</select></label><label className="field"><span>Director</span><select name="director_id" defaultValue={card.director_id??''} required>{users.filter(u=>u.role==='DIRECTOR').map(u=><option value={u.id} key={u.id}>{u.full_name}{u.status==='ACTIVE'?'':' (inactive)'}</option>)}</select></label><label className="field"><span>Status</span><select name="status" defaultValue={card.status}>{!['ASSIGNED','IN_PROGRESS','ON_HOLD','CANCELLED'].includes(card.status)&&<option value={card.status}>{card.status.replaceAll('_',' ')}</option>}<option>AWAITING_PAYMENT</option><option>ASSIGNED</option><option>IN_PROGRESS</option><option>ON_HOLD</option><option>CANCELLED</option></select></label><Button type="submit">Save changes</Button></form>
      <h3>Consultant assignments</h3><DataTable headers={['Role','Consultant','Allocation period','Assigned','Ended','Current']} rows={(assignments.data??[]).map(a=>[a.assignment_type.replaceAll('_',' '),a.full_name,`${a.allocation_start_date??'-'} to ${a.allocation_end_date??'-'}`,new Date(a.assigned_at).toLocaleString(),a.ended_at?new Date(a.ended_at).toLocaleString():'-',a.active?'Yes':'No'])}/><form className="inline-form" onSubmit={reassign}><label className="field"><span>Role to change</span><select value={assignmentType} onChange={e=>setAssignmentType(e.target.value)}><option>LEAD_CONSULTANT</option><option>CO_CONSULTANT</option><option>SUBJECT_EXPERT</option></select></label><label className="field"><span>Assign to</span><select value={reassignUser} onChange={e=>setReassignUser(e.target.value)} required><option value="">Select consultant</option>{users.filter(u=>u.role==='END_USER'&&u.status==='ACTIVE'&&!assignments.data?.some(a=>a.user_id===u.id&&a.assignment_type!==assignmentType)).map(u=><option value={u.id} key={u.id}>{u.full_name}</option>)}</select></label><Field label="Allocation starts" name="allocation_start_date" type="date"/><Field label="Allocation ends" name="allocation_end_date" type="date"/><Field label="Reason for reassignment" name="reason" minLength={3} maxLength={5000} required/><Button type="submit">Update assignment</Button></form>
      </>}
      <div className="two-column">
        <div>
          <h3>Milestones</h3>
          <DataTable headers={['Code','Planned','Actual','Progress','Start','Closing','Portal Report','Action']} rows={(milestones.data ?? []).map(m => [m.code,m.planned_visits,m.actual_visits,`${m.progress}%`,m.start_date??'â€”',m.closing_date??'â€”',m.portal_report_submission_date??'â€”',<Button variant="quiet" onClick={()=>setEditingMilestone(m)}>Edit</Button>])}/>
          {editingMilestone&&<form className="form-grid" onSubmit={saveMilestone}><h4>Edit {editingMilestone.code}</h4><label className="field"><span>Responsible consultant</span><select name="responsible_consultant_id" defaultValue={editingMilestone.responsible_consultant_id??''}><option value="">Use Job Card lead</option>{users.filter(u=>u.role==='END_USER'&&u.status==='ACTIVE'&&(assignments.data??[]).some(a=>a.user_id===u.id&&a.active)).map(u=><option key={u.id} value={u.id}>{u.full_name}</option>)}</select></label><Field label="Planned mandays" name="planned_mandays" type="number" min=".01" step=".01" defaultValue={editingMilestone.planned_mandays??''} required/><Field label="Payment amount" name="amount" type="number" min="0" step=".01" defaultValue={editingMilestone.amount??''}/><Field label="Payment percent" name="payment_percent" type="number" min="0" max="100" step=".01" defaultValue={editingMilestone.payment_percent??''}/><Field label="Activities and deliverables (comma separated)" name="activities" defaultValue={(editingMilestone.activities??[]).join(', ')}/><label className="field wide"><span>Scope / description</span><textarea name="description" defaultValue={editingMilestone.description??''}/></label><Field label="Name" name="name" defaultValue={editingMilestone.name} required/><Field label="Planned visits" name="planned_visits" type="number" min="1" defaultValue={editingMilestone.planned_visits} required/><Field label="Start date" name="start_date" type="date" defaultValue={editingMilestone.start_date??''}/><Field label="Closing date" name="closing_date" type="date" defaultValue={editingMilestone.closing_date??''}/><Field label="Portal report submission" name="portal_report_submission_date" type="date" defaultValue={editingMilestone.portal_report_submission_date??''}/><Button type="submit">Save milestone</Button><Button type="button" variant="quiet" onClick={()=>setEditingMilestone(null)}>Cancel</Button></form>}
          <form className="form-grid" onSubmit={milestone}><label className="field"><span>Milestone</span><select name="code"><option>M1</option><option>M2</option><option>M3</option></select></label><Field label="Name" name="name" required/><Field label="Planned visits" name="planned_visits" type="number" min="1" required/><Field label="Start date" name="start_date" type="date"/><Field label="Closing date" name="closing_date" type="date"/><Field label="Portal report submission" name="portal_report_submission_date" type="date"/><Field label="Planned mandays" name="planned_mandays" type="number" min=".01" step=".01" required/><Field label="Payment amount" name="amount" type="number" min="0" step=".01"/><Field label="Payment percent" name="payment_percent" type="number" min="0" max="100" step=".01"/><Field label="Activities and deliverables (comma separated)" name="activities"/><label className="field"><span>Responsible consultant</span><select name="responsible_consultant_id" defaultValue=""><option value="">Use Job Card lead</option>{users.filter(u=>u.role==='END_USER'&&u.status==='ACTIVE'&&(assignments.data??[]).some(a=>a.user_id===u.id&&a.active)).map(u=><option key={u.id} value={u.id}>{u.full_name}</option>)}</select></label><label className="field wide"><span>Scope / description</span><textarea name="description"/></label><Button type="submit">Add milestone</Button></form>
        </div>
        <div>
          <h3>Documents</h3>
          {documentError && <ErrorMessage text={documentError}/>}
          <DataTable headers={['Type','File','Version','Status','Remarks','Reviewer / date','Actions']} rows={(docs.data ?? []).map(d => [d.document_type,d.file_name,`V${d.version}`,<Status>{d.status}</Status>,d.remarks ?? '-',d.reviewed_by?`${users.find(u=>u.id===d.reviewed_by)?.full_name??`User #${d.reviewed_by}`} - ${d.reviewed_at??''}`:'-',<div className="actions"><Button variant="secondary" onClick={() => void downloadDocument(d)}>Download</Button>{card.status!=='COMPLETED'&&['SUBMITTED','RESUBMITTED'].includes(d.status)&&<><Button onClick={() => reviewDocument(d,'APPROVED')}>Approve</Button><Button variant="secondary" onClick={() => reviewDocument(d,'RETURNED')}>Return for correction</Button><Button variant="quiet" onClick={() => reviewDocument(d,'REJECTED')}>Reject</Button></>}</div>])}/><h4>Related record documents</h4><DataTable headers={['Type','Milestone','File','Version','Status','Remarks','Reviewer / date','Actions']} rows={(recordDocs.data??[]).map(d=>[d.document_type,d.milestone_id?milestones.data?.find(m=>m.id===d.milestone_id)?.code??`#${d.milestone_id}`:'Job Card',d.file_name,`V${d.version}`,<Status>{d.status}</Status>,d.remarks??'-',d.reviewed_by?`${users.find(u=>u.id===d.reviewed_by)?.full_name??`User #${d.reviewed_by}`} - ${d.reviewed_at??''}`:'-',<div className="actions"><Button variant="secondary" onClick={()=>void downloadFile(`/api/record-documents/${d.id}/download`,d.file_name)}>Open / download</Button>{card.status!=='COMPLETED'&&['SUBMITTED','RESUBMITTED'].includes(d.status)&&<><Button onClick={async()=>{const comment='Document reviewed and approved.';try{await api(`/api/record-documents/${d.id}/review`,{method:'POST',body:JSON.stringify({decision:'APPROVED',comment})});recordDocs.reload()}catch(e){setError(message(e))}}}>Approve</Button><Button variant="secondary" onClick={async()=>{const comment=prompt('Reason for correction:');if(!comment?.trim())return;try{await api(`/api/record-documents/${d.id}/review`,{method:'POST',body:JSON.stringify({decision:'RETURNED',comment})});recordDocs.reload()}catch(e){setError(message(e))}}}>Return</Button></>}</div>])}/>
          <h3>Approvals</h3>
          <DataTable headers={['Stage','Decision','Comment']} rows={(approvals.data ?? []).map(a => [a.stage,a.decision,a.comment ?? '-'])}/>
        </div>
      </div>
      {card.status!=='COMPLETED'&&<Button variant="secondary" onClick={async () => { try{await api(`/api/job-cards/${card.id}`,{method:'DELETE'});close();reload()}catch(e){setError(message(e))} }}>Archive Job Card</Button>}
    </Card>
  </section>;
}

export function Settings() {
  const [value,setValue] = useState('8');
  const [saved,setSaved] = useState('');
  const [activityTypes,setActivityTypes] = useState<{id:number;code:string;name:string;active:boolean}[]>([]);
  const [activityCode,setActivityCode] = useState('');
  const [activityName,setActivityName] = useState('');
  async function loadActivityTypes(){try{setActivityTypes(await api('/api/activity-types?include_inactive=true'))}catch(e){setSaved(message(e))}}
  useEffect(()=>{api<{value:string}>('/api/settings/manday_hours').then(setting=>setValue(setting.value)).catch(()=>setValue('8'));void loadActivityTypes()},[]);
  async function save(e:FormEvent) {
    e.preventDefault();
    try { const r = await api<{value:string}>('/api/settings/manday_hours',{method:'PUT',body:JSON.stringify({value})}); setSaved(`Saved: ${r.value} hours per manday`); }
    catch(e) { setSaved(message(e)); }
  }
  async function saveActivity(e:FormEvent){e.preventDefault();try{await api(`/api/activity-types/${encodeURIComponent(activityCode)}`,{method:'PUT',body:JSON.stringify({code:activityCode,name:activityName,active:true})});setActivityCode('');setActivityName('');setSaved('Activity type saved.');await loadActivityTypes()}catch(e){setSaved(message(e))}}
  async function toggleType(row:{id:number;code:string;name:string;active:boolean}){try{await api(`/api/activity-types/${encodeURIComponent(row.code)}`,{method:'PUT',body:JSON.stringify({...row,active:!row.active})});await loadActivityTypes()}catch(e){setSaved(message(e))}}
  return <Page title="Settings"><Card title="Default manday calculation"><p className="muted">This default applies to new Job Cards. Existing cards keep their own configured hours per manday.</p><form className="inline-form" onSubmit={save}><Field label="Hours per manday" type="number" min=".01" max="24" step=".01" value={value} onChange={e => setValue(e.target.value)}/><Button type="submit">Save setting</Button></form></Card><Card title="Configurable activity types"><p className="muted">Consultants can select active types when recording milestone work.</p><form className="inline-form" onSubmit={saveActivity}><Field label="Type code" value={activityCode} onChange={e=>setActivityCode(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g,''))} minLength={2} maxLength={48} required/><Field label="Display name" value={activityName} onChange={e=>setActivityName(e.target.value)} minLength={2} maxLength={120} required/><Button type="submit">Add activity type</Button></form><DataTable headers={['Code','Name','Status','Action']} rows={activityTypes.map(row=>[row.code,row.name,<Status>{row.active?'ACTIVE':'INACTIVE'}</Status>,<Button variant="secondary" onClick={()=>void toggleType(row)}>{row.active?'Deactivate':'Activate'}</Button>])}/>{saved&&<p role="status">{saved}</p>}</Card></Page>;
}

export function Workflow() {
  const cards = useLoad<CardRow[]>('/api/job-cards');
  const users = useLoad<User[]>('/api/users');
  const msmes = useLoad<Msme[]>('/api/msmes');
  const [card,setCard] = useState<CardRow|null>(null);
  return <Page title="Documents & Approvals"><DataTable headers={['Job Card','Status','Open']} rows={(cards.data ?? []).map(c => [c.number,<Status>{c.status}</Status>,<Button variant="secondary" onClick={() => setCard(c)}>Review</Button>])}/>{card && <JobCardManage card={card} users={users.data??[]} msmes={msmes.data??[]} close={() => setCard(null)} reload={cards.reload}/>}</Page>;
}

export function Notifications() {
  const {data,reload,error} = useLoad<Notice[]>('/api/notifications');
  function target(link?:string){const id=link?.match(/job-cards\/(\d+)/)?.[1];if(id)return `/admin/job-cards?card=${id}`;return link?.includes('/visits/')?'/admin/workflow':''}
  return <Page title="Notifications"><ErrorMessage text={error}/>{(data ?? []).map(n => <Card key={n.id}><p><strong>{n.message}</strong><br/><small className="muted">{n.created_at}</small></p>{target(n.link)&&<p><Link to={target(n.link)}>Open related record</Link></p>}{!n.read_at && <Button variant="secondary" onClick={async () => { await api(`/api/notifications/${n.id}/read`,{method:'POST'}); reload(); }}>Mark read</Button>}</Card>)}</Page>;
}

export function AuditLog() {
  const [search,setSearch]=useState(''),[term,setTerm]=useState(''),[page,setPage]=useState(1);useEffect(()=>{const timer=window.setTimeout(()=>{setTerm(search);setPage(1)},250);return()=>window.clearTimeout(timer)},[search]);const data=useLoad<Audit[]>(`/api/audit-logs?search=${encodeURIComponent(term)}&page=${page}&page_size=100`);
  return <Page title="Audit Log"><Field label="Search actions, entities or details" value={search} onChange={e=>setSearch(e.target.value)}/><ErrorMessage text={data.error}/><DataTable headers={['When','Action','Entity','Details']} rows={(data.data ?? []).map(a => [a.created_at,a.action,[a.entity_type,a.entity_ref??a.entity_id].filter(Boolean).join(" ... "),a.details ?? '-'])}/><div className="actions"><Button variant="secondary" disabled={page<=1} onClick={()=>setPage(page-1)}>Previous</Button><span>Page {page}</span><Button variant="secondary" disabled={(data.data?.length??0)<100} onClick={()=>setPage(page+1)}>Next</Button></div></Page>;
}

function Page({title,children,action}:{title:string;children:ReactNode;action?:ReactNode}) {
  return <><header className="page-header"><div><p className="eyebrow">ADMIN WORKSPACE</p><h1>{title}</h1></div>{action}</header>{children}</>;
}

function ErrorMessage({text}:{text:string}) {
  return text ? <p className="form-error" role="alert">{text}</p> : null;
}

function DataTable({headers,rows}:{headers:string[];rows:ReactNode[][]}) {
  return <div className="table-wrap mobile-cards"><table><thead><tr>{headers.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.length ? rows.map((r,i) => <tr key={i}>{r.map((cell,j) => <td key={j} data-label={headers[j]}>{cell}</td>)}</tr>) : <tr><td colSpan={headers.length} className="muted">No records found.</td></tr>}</tbody></table></div>;
}
