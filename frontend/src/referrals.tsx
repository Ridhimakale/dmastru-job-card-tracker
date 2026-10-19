import { FormEvent, useEffect, useState } from 'react';
import { api } from './api';
import { Button, Card, Field, IndustrySelect, Status } from './components';
import { IndiaLocationField } from './india_location';

type Referral={id:string;number:string;company_name:string;contact_person:string|null;enquiry_date:string;status:string;created_at:string};
const services=['ZED Silver','ZED Bronze','ZED Gold','Lean','ISO','Training'];
const today=()=>new Date().toISOString().slice(0,10);

export function Referrals(){
  const [items,setItems]=useState<Referral[]>([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [selectedServices,setSelectedServices]=useState<string[]>([]);
  async function refresh(){setLoading(true);try{setItems(await api<Referral[]>('/api/my-referrals'));setError('')}catch(e){setError(e instanceof Error?e.message:'Unable to load your referrals.')}finally{setLoading(false)}}
  useEffect(()=>{void refresh()},[]);
  async function submit(event:FormEvent<HTMLFormElement>){
    event.preventDefault();const form=event.currentTarget;const data=new FormData(form);const enquiryFor=data.getAll('enquiry_for').map(String);
    if(!enquiryFor.length){setError('Select at least one Enquiry for option.');return}
    setBusy(true);setError('');setNotice('');
    try{
      await api('/api/referrals',{method:'POST',body:JSON.stringify({company_name:data.get('company_name'),contact_person:data.get('contact_person')||null,email:data.get('email')||null,phone:data.get('phone')||null,enquiry_for:enquiryFor,zed_id:data.get('zed_id')||null,zed_pass:data.get('zed_pass')||null,industry:data.get('industry')||null,location:data.get('location')||null,requirement:data.get('requirement'),enquiry_date:today(),deadline_date:data.get('deadline_date'),source:'USER_REFERRAL',referral_consultant_id:null})});
      form.reset();setSelectedServices([]);setNotice('Referral sent to the Marketing team for follow-up. You can track its enquiry status here.');await refresh();
    }catch(e){setError(e instanceof Error?e.message:'Unable to submit this referral.')}finally{setBusy(false)}
  }
  return <main className="marketing-page referral-page"><header className="page-header"><div><p className="eyebrow">WORKSPACE</p><h1>Refer a Client</h1><p className="muted">Enter the enquiry details for Marketing to process and follow up. A referral is marked Successful once onboarding is complete.</p></div></header>
    <div className="referral-workspace"><Card title="Client referral"><form className="marketing-form referral-form" onSubmit={submit}>
      <Field name="company_name" label="Company name" required minLength={2}/><Field name="contact_person" label="Contact person"/><Field name="email" label="Email" type="email"/>
      <Field name="phone" label="Phone"/><IndustrySelect name="industry"/><IndiaLocationField name="location" label="Client location"/>
      <fieldset className="field enquiry-for-field"><legend>Enquiry for (select all that apply) *</legend><div className="enquiry-for-options">{services.map(service=><label className="checkbox-label" key={service}><input type="checkbox" name="enquiry_for" value={service} checked={selectedServices.includes(service)} onChange={event=>setSelectedServices(current=>event.target.checked?[...current,service]:current.filter(value=>value!==service))}/>{service}</label>)}</div></fieldset>
      {selectedServices.some(service=>service.startsWith('ZED '))&&<><Field name="zed_id" label="ZED ID"/><Field name="zed_pass" label="ZED pass (optional)" type="password" autoComplete="new-password"/><small className="muted referral-zed-note">Stored encrypted; it is never displayed after saving.</small></>}
      <Field name="deadline_date" label="Response deadline date (required)" type="date" required/>
      <label className="field referral-textarea referral-requirement"><span>Client requirement / scope *</span><textarea name="requirement" required minLength={3} maxLength={10000}/></label>
      <p className="referral-process">Lead source, initial status, and Marketing assignment are automatic. Marketing will manage follow-up.</p><Button disabled={busy}>{busy?'Sending referral...':'Send referral to Marketing'}</Button>
    </form>{error&&<p className="form-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}</Card>
    <Card title="My referred clients">{loading?<p className="muted">Loading referrals...</p>:items.length===0?<p className="muted">You have not referred any clients yet.</p>:<div className="table-wrap"><table><thead><tr><th>Enquiry</th><th>Client</th><th>Submitted</th><th>Status</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td>{item.number}</td><td>{item.company_name}{item.contact_person&&<small className="table-subtext">{item.contact_person}</small>}</td><td>{item.enquiry_date}</td><td><Status>{item.status}</Status></td></tr>)}</tbody></table></div>}</Card></div>
  </main>
}
