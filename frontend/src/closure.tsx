import { FormEvent, useEffect, useState } from 'react';
import { api, downloadFile } from './api';
import { Button, Card, Field, Status } from './components';

type Project={id:number;number:string;location:string;status:string};
type Closure={id?:number;job_card_id:number;client_confirmation?:string;problem?:string;requirement?:string;solution?:string;implementation?:string;benefits?:string;results?:string;feedback_rating?:number;feedback?:string;testimonial?:string;publication_permission?:boolean;certificate_number?:string;closed_at?:string|null};
const errorText=(e:unknown)=>e instanceof Error?e.message:'The request could not be completed.';

export function ClosureWorkspace(){
  const [projects,setProjects]=useState<Project[]>([]),[cardId,setCardId]=useState(''),[closure,setClosure]=useState<Closure|null>(null),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  useEffect(()=>{api<Project[]>('/api/job-cards').then(rows=>{setProjects(rows);if(!cardId&&rows.length)setCardId(String(rows[0].id))}).catch(e=>setNotice(errorText(e)))},[reload]);
  useEffect(()=>{if(!cardId){setClosure(null);return}api<Closure>(`/api/job-cards/${cardId}/closure`).then(setClosure).catch(()=>setClosure({job_card_id:Number(cardId)}))},[cardId,reload]);
  async function save(e:FormEvent<HTMLFormElement>){e.preventDefault();setBusy(true);setNotice('');const f=new FormData(e.currentTarget);const values=Object.fromEntries(f);const data={...values,feedback_rating:Number(values.feedback_rating),publication_permission:f.has('publication_permission')};try{const saved=await api<Closure>(`/api/job-cards/${cardId}/closure`,{method:'PUT',body:JSON.stringify(data)});setClosure(saved);setNotice('Closure records saved. Management final approval is still required before final closure.')}catch(e){setNotice(errorText(e))}finally{setBusy(false)}}
  async function complete(){setBusy(true);setNotice('');try{const saved=await api<Closure>(`/api/job-cards/${cardId}/closure/complete`,{method:'POST'});setClosure(saved);setNotice(`Project closed. Certificate ${saved.certificate_number} is ready.`);setReload(v=>v+1)}catch(e){setNotice(errorText(e))}finally{setBusy(false)}}
  const current=projects.find(p=>String(p.id)===cardId);
  return <main className="page"><header className="page-header"><div><p className="eyebrow">CLIENT DELIVERY</p><h1>Project closure</h1><p className="muted">Record client confirmation, case study and feedback before Management final approval.</p></div></header>
    {notice&&<p role="status" className="notice">{notice}</p>}
    <Card title="Select Job Card"><label className="field"><span>Project</span><select value={cardId} onChange={e=>setCardId(e.target.value)}><option value="">Select a Job Card</option>{projects.map(p=><option key={p.id} value={p.id}>{p.number} · {p.location} · {p.status}</option>)}</select></label>{current&&<p><Status>{current.status}</Status></p>}</Card>
    {cardId&&closure&&<><Card title="Client confirmation, case study and feedback"><form key={`${cardId}-${closure.id??'new'}-${reload}`} className="marketing-form" onSubmit={save}>
      <label className="field"><span>Client confirmation and evidence summary</span><textarea name="client_confirmation" defaultValue={closure.client_confirmation??''} minLength={5} required/></label>
      {(['problem','requirement','solution','implementation','benefits','results'] as const).map(k=><label className="field" key={k}><span>{k[0].toUpperCase()+k.slice(1)}</span><textarea name={k} defaultValue={closure[k]??''} minLength={3} required/></label>)}
      <Field label="Client rating (1 to 5)" name="feedback_rating" type="number" min="1" max="5" defaultValue={closure.feedback_rating??''} required/>
      <label className="field"><span>Client feedback</span><textarea name="feedback" defaultValue={closure.feedback??''} minLength={3} required/></label>
      <label className="field"><span>Testimonial (optional)</span><textarea name="testimonial" defaultValue={closure.testimonial??''}/></label>
      <label className="field"><span><input type="checkbox" name="publication_permission" defaultChecked={closure.publication_permission??false}/> Permission to publish client information</span></label>
      <Button disabled={busy}>{busy?'Saving…':'Save closure records'}</Button>
    </form></Card>
    {closure.certificate_number?<Card title="Completion certificate"><p>Certificate {closure.certificate_number}</p><Button variant="secondary" onClick={()=>void downloadFile(`/api/job-cards/${cardId}/closure/certificate`,`${closure.certificate_number}.pdf`)}>Download PDF certificate</Button></Card>:current?.status==='CLOSURE_PENDING'&&<Card title="Finalize project"><p>Management approval is recorded. Completing closure generates and stores the project certificate.</p><Button disabled={busy} onClick={()=>void complete()}>{busy?'Closing…':'Complete project and generate certificate'}</Button></Card>}</>}
  </main>;
}
