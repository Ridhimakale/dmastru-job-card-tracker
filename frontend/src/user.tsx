import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from './api';
import { Button, Card, Field, Status } from './components';
import { useAuth } from './auth';
import { GuestLayout } from './pages';
type JobCard={id:number;number:string;location:string;project_start_date:string;project_end_date:string;required_md:string;required_hours:string;status:string};type Milestone={id:number;code:string;name:string;planned_visits:number;actual_visits:number;progress:string;status:string};type Visit={id:number;job_card_id:number;visit_number:number;visit_date:string;working_minutes:number;work_details:string;rating_code:string;status:string;rejection_reason?:string};type Document={id:number;job_card_id:number;document_type:string;file_name:string;version:number;status:string;remarks?:string};type Approval={id:number;stage:string;decision:string;comment?:string};type Notice={id:number;message:string;read_at?:string;created_at:string};type Activity={id:number;action:string;entity_type:string;details?:string;created_at:string};
const err=(e:unknown)=>e instanceof Error?e.message:'Unable to complete request.';
function useLoad<T>(path:string){const [data,setData]=useState<T|null>(null);const [error,setError]=useState('');const reload=()=>api<T>(path).then(setData).catch((e:unknown)=>setError(err(e)));useEffect(()=>{reload()},[path]);return {data,error,reload}}

export function Registration(){
  const [message,setMessage]=useState('');
  async function submit(e:FormEvent<HTMLFormElement>){
    e.preventDefault();
    try{
      await api('/api/auth/register',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))});
      setMessage('Registration submitted. An administrator must activate your account before you can sign in.')
    }catch(e){
      setMessage(err(e))
    }
  }
  return (
    <GuestLayout>
      <div className="login-card-inner">
        <span className="form-eyebrow">CONSULTANT REGISTRATION</span>
        <h2 className="form-title">Create account</h2>
        <p className="form-description">Onboarding requires administrator approval and activation.</p>
        
        <form onSubmit={submit} className="form-grid">
          <Field label="Full name" name="full_name" required placeholder="e.g. Rahul Sharma"/>
          <Field label="Email" name="email" type="email" required placeholder="name@dmastru.com"/>
          <Field label="Mobile number" name="mobile" placeholder="e.g. +91 99999 99999"/>
          <Field label="Consultant ID" name="consultant_id" placeholder="e.g. CON-2026-001"/>
          <Field label="Password" name="password" type="password" minLength={8} required placeholder="At least 8 characters"/>
          <Field label="Confirm password" name="confirm_password" type="password" minLength={8} required placeholder="Repeat password"/>
          
          <Button type="submit">Register Consultant</Button>
        </form>
        
        {message && (
          <p role="status" className={message.includes('submitted') ? 'form-success-info' : 'form-error'}>
            {message}
          </p>
        )}
        
        <p className="form-footer-link">
          Already have an account? <Link to="/login">Back to sign in</Link>
        </p>
      </div>
    </GuestLayout>
  );
}

export function UserDashboard(){const dash=useLoad<{kpis:Record<string,number>;projects:JobCard[]}>('/api/dashboard/user');return <Page title="My Dashboard"><UserError text={dash.error}/><div className="stat-grid">{Object.entries(dash.data?.kpis??{}).map(([k,v])=><Card key={k}><p className="muted">{k.replaceAll('_',' ')}</p><strong className="stat">{v}</strong></Card>)}</div><Card title="Assigned Job Cards"><Rows cards={dash.data?.projects??[]}/></Card></Page>}
export function MyJobCards(){const cards=useLoad<JobCard[]>('/api/job-cards');const [selected,setSelected]=useState<JobCard|null>(null);return <Page title="My Job Cards"><UserError text={cards.error}/><Rows cards={cards.data??[]} onOpen={setSelected}/>{selected&&<CardDetail card={selected} close={()=>setSelected(null)} reload={cards.reload}/>}</Page>}
function Rows({cards,onOpen}:{cards:JobCard[];onOpen?:(c:JobCard)=>void}){
  return <div className="table-wrap mobile-cards"><table>
    <thead><tr><th>Job Card</th><th>Location</th><th>Dates</th><th>Status</th><th>Action</th></tr></thead>
    <tbody>{cards.length?cards.map(c=><tr key={c.id}>
      <td data-label="Job Card"><strong>{c.number}</strong></td>
      <td data-label="Location">{c.location}</td>
      <td data-label="Dates">{c.project_start_date} — {c.project_end_date}</td>
      <td data-label="Status"><Status>{c.status}</Status></td>
      <td data-label="Action">{onOpen&&<Button variant="secondary" onClick={()=>onOpen(c)}>Open</Button>}</td>
    </tr>):<tr><td colSpan={5} className="muted">No Job Cards have been assigned to you yet.</td></tr>}</tbody>
  </table></div>
}
function CardDetail({card,close,reload}:{card:JobCard;close:()=>void;reload:()=>void}){const milestones=useLoad<Milestone[]>(`/api/job-cards/${card.id}/milestones`);const visits=useLoad<Visit[]>(`/api/job-cards/${card.id}/visits`);const docs=useLoad<Document[]>(`/api/job-cards/${card.id}/documents`);const approvals=useLoad<Approval[]>(`/api/job-cards/${card.id}/approvals`);const [showVisit,setShowVisit]=useState(false);const [showDoc,setShowDoc]=useState(false);return <section className="drawer"><Card title={card.number}><Button variant="quiet" onClick={close}>Close details</Button><div className="two-column"><div><h3>Milestones</h3><Table headers={['Milestone','Visits','Progress']} rows={(milestones.data??[]).map(m=>[`${m.code} · ${m.name}`,`${m.actual_visits}/${m.planned_visits}`,`${m.progress}%`])}/><h3>Visits</h3><Table headers={['#','Date','Duration','Rating','Status']} rows={(visits.data??[]).map(v=>[v.visit_number,v.visit_date,`${Math.floor(v.working_minutes/60)}h ${v.working_minutes%60}m`,v.rating_code,<Status>{v.status}</Status>])}/><Button onClick={()=>setShowVisit(!showVisit)}>+ Add visit</Button>{showVisit&&<VisitForm card={card} milestones={milestones.data??[]} done={()=>{visits.reload();milestones.reload();reload();setShowVisit(false)}}/>}</div><div><h3>Documents</h3><Table headers={['Type','Version','Status']} rows={(docs.data??[]).map(d=>[d.document_type,`V${d.version}`,<Status>{d.status}</Status>])}/><Button variant="secondary" onClick={()=>setShowDoc(!showDoc)}>Upload document</Button>{showDoc&&<UploadForm card={card} done={()=>{docs.reload();setShowDoc(false)}}/>}<h3>Approvals</h3><Table headers={['Stage','Decision','Comment']} rows={(approvals.data??[]).map(a=>[a.stage,a.decision,a.comment??'—'])}/></div></div></Card></section>}
function VisitForm({card,milestones,done}:{card:JobCard;milestones:Milestone[];done:()=>void}){const [mode,setMode]=useState<'draft'|'submit'>('draft');async function save(e:FormEvent<HTMLFormElement>){e.preventDefault();const d:Record<string,unknown>=Object.fromEntries(new FormData(e.currentTarget));d.milestone_id=Number(d.milestone_id);d.hours=Number(d.hours);d.minutes=Number(d.minutes);try{const visit=await api<Visit>(`/api/job-cards/${card.id}/visits/draft`,{method:'POST',body:JSON.stringify(d)});if(mode==='submit')await api(`/api/visits/${visit.id}/submit`,{method:'POST'});done()}catch(e){alert(err(e))}}return <Card title="Add visit"><form className="form-grid" onSubmit={save}><label className="field"><span>Milestone</span><select name="milestone_id">{milestones.map(m=><option value={m.id} key={m.id}>{m.code} · {m.name}</option>)}</select></label><Field label="Visit date" name="visit_date" type="date" required/><Field label="Hours" name="hours" type="number" min="0" required/><Field label="Minutes" name="minutes" type="number" min="0" max="59" required/><label className="field"><span>Rating</span><select name="rating_code"><option>A</option><option>B</option><option>C</option><option>D</option><option>E</option></select></label><label className="field wide"><span>Work details</span><textarea name="work_details" minLength={3} required/></label><label className="field wide"><span>Remarks</span><textarea name="remarks"/></label><div className="actions"><Button type="submit" variant="secondary" onClick={()=>setMode('draft')}>Save draft</Button><Button type="submit" onClick={()=>setMode('submit')}>Submit visit</Button></div></form></Card>}
function UploadForm({card,done}:{card:JobCard;done:()=>void}){async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const f=new FormData(e.currentTarget);const file=f.get('file') as File;if(!file)return;const content_base64=await read(file);try{await api(`/api/job-cards/${card.id}/documents`,{method:'POST',body:JSON.stringify({document_type:f.get('document_type'),file_name:file.name,mime_type:file.type,content_base64,remarks:f.get('remarks')})});done()}catch(e){alert(err(e))}}return <Card title="Upload document"><form onSubmit={submit}><label className="field"><span>Document type</span><select name="document_type"><option>Case Study</option><option>Technology Recommendation</option><option>Audit</option><option>Other Supporting Document</option></select></label><label className="field"><span>File (PDF, image, DOCX, XLSX; max 10 MB)</span><input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx" required/></label><label className="field"><span>Remarks</span><textarea name="remarks"/></label><Button type="submit">Upload for review</Button></form></Card>}
const read=(file:File)=>new Promise<string>((resolve,reject)=>{if(file.size>10*1024*1024){reject(new Error('File must not exceed 10 MB.'));return}const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read the selected file.'));reader.readAsDataURL(file)});
function useAssignedRecords<T extends {job_card_id:number}>(path:(id:number)=>string){const cards=useLoad<JobCard[]>('/api/job-cards');const [records,setRecords]=useState<T[]>([]);const [failure,setFailure]=useState('');useEffect(()=>{const items=cards.data??[];Promise.all(items.map(card=>api<T[]>(path(card.id)))).then(groups=>setRecords(groups.flat())).catch(e=>setFailure(err(e)))},[cards.data]);return {cards,records,failure}}
export function Visits(){const {cards,records,failure}=useAssignedRecords<Visit>(id=>`/api/job-cards/${id}/visits`);const names=Object.fromEntries((cards.data??[]).map(c=>[c.id,c.number]));return <Page title="My Visits"><UserError text={cards.error||failure}/><Table headers={['Job Card','Visit','Date','Duration','Rating','Status','Rejection']} rows={records.map(v=>[names[v.job_card_id]??v.job_card_id,`#${v.visit_number}`,v.visit_date,`${Math.floor(v.working_minutes/60)}h ${v.working_minutes%60}m`,v.rating_code,<Status>{v.status}</Status>,v.rejection_reason??'—'])}/></Page>}
export function Documents(){const {cards,records,failure}=useAssignedRecords<Document>(id=>`/api/job-cards/${id}/documents`);const names=Object.fromEntries((cards.data??[]).map(c=>[c.id,c.number]));return <Page title="My Documents"><UserError text={cards.error||failure}/><Table headers={['Job Card','Type','File','Version','Status','Remarks']} rows={records.map(d=>[names[d.job_card_id]??d.job_card_id,d.document_type,d.file_name,`V${d.version}`,<Status>{d.status}</Status>,d.remarks??'—'])}/></Page>}
export function Profile(){const {user}=useAuth();const [saved,setSaved]=useState('');async function update(e:FormEvent<HTMLFormElement>){e.preventDefault();try{await api('/api/auth/me',{method:'PATCH',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))});setSaved('Profile saved.')}catch(e){setSaved(err(e))}}return <Page title="My Profile"><Card title="Personal details"><form className="form-grid" onSubmit={update}><Field label="Full name" name="full_name" defaultValue={user?.full_name} required/><Field label="Email" defaultValue={user?.email} disabled/><Field label="Mobile" name="mobile" defaultValue={user?.mobile??''}/><Field label="Consultant ID" name="consultant_id"/><Button type="submit">Save profile</Button></form>{saved&&<p role="status">{saved}</p>}</Card></Page>}
export function Activity(){const activity=useLoad<Activity[]>('/api/activity');return <Page title="Activity History"><UserError text={activity.error}/><Table headers={['When','Action','Entity','Details']} rows={(activity.data??[]).map(a=>[a.created_at,a.action,a.entity_type,a.details??'—'])}/></Page>}
export function Notifications(){const notes=useLoad<Notice[]>('/api/notifications');return <Page title="Notifications">{(notes.data??[]).map(n=><Card key={n.id}><strong>{n.message}</strong><p className="muted">{n.created_at}</p>{!n.read_at&&<Button variant="secondary" onClick={async()=>{await api(`/api/notifications/${n.id}/read`,{method:'POST'});notes.reload()}}>Mark read</Button>}</Card>)}</Page>}
function Page({title,children}:{title:string;children:ReactNode}){return <><header className="page-header"><div><p className="eyebrow">END USER WORKSPACE</p><h1>{title}</h1></div></header>{children}</>};function UserError({text}:{text:string}){return text?<p className="form-error" role="alert">{text}</p>:null};function Table({headers,rows}:{headers:string[];rows:ReactNode[][]}){return <div className="table-wrap mobile-cards"><table><thead><tr>{headers.map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{rows.length?rows.map((r,i)=><tr key={i}>{r.map((x,j)=><td key={j} data-label={headers[j]}>{x}</td>)}</tr>):<tr><td colSpan={headers.length} className="muted">No records yet.</td></tr>}</tbody></table></div>}
