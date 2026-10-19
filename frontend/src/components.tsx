import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react';
import { Link } from 'react-router-dom';
type DashboardActionIcon='list'|'folder'|'calendar'|'report'|'payment'|'check'|'task';
export function DashboardLink({to,children,icon='list'}:{to:string;children:ReactNode;icon?:DashboardActionIcon}){
  return <Link className="dashboard-link-button" to={to}><svg viewBox="0 0 24 24" aria-hidden="true">{icon==='list'?<><path d="M9 5h11M9 12h11M9 19h11"/><path d="m3 5 1 1 2-2M3 12l1 1 2-2M3 19l1 1 2-2"/></>:icon==='folder'?<><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M3.5 9h17"/></>:icon==='calendar'?<><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4m8-4v4M3 10h18m-13 4h2m3 0h2m-7 3h2"/></>:icon==='report'?<><path d="M4 20V11m5 9V5m5 15v-7m5 7V8"/><path d="M2 20h20"/></>:icon==='payment'?<><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18m-13 5h4"/></>:icon==='check'?<><path d="M5 4h14v17H5zM9 4V2h6v2"/><path d="m8 12 2 2 5-5"/></>:<><path d="M5 4h14v17H5zM8 8h8M8 12h8M8 16h5"/></>}</svg><span>{children}</span></Link>;
}
export function Button({children,variant='primary',...props}:ButtonHTMLAttributes<HTMLButtonElement>&{variant?:'primary'|'secondary'|'quiet'}){return <button className={`button ${variant}`} {...props}>{children}</button>}
export function Field({label,error,...props}:{label:string;error?:string}&InputHTMLAttributes<HTMLInputElement>){const id=props.id??props.name;return <label className="field" htmlFor={id}><span>{label}</span><input id={id} aria-invalid={Boolean(error)} aria-describedby={error?`${id}-error`:undefined} {...props}/>{error&&<small id={`${id}-error`} role="alert">{error}</small>}</label>}
export function Card({children,title}:{children:ReactNode;title?:string}){return <section className="card">{title&&<h2>{title}</h2>}{children}</section>}
export function Status({children}:{children:ReactNode}){const statusClass=typeof children==='string'?children.toLowerCase().replace(/[^a-z0-9]/g,'-'):'';return <span className={`status ${statusClass}`}>{children}</span>}
export function Logo({ className = '', size = 40 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size * 1.3}
      viewBox="0 0 120 160"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ display: 'inline-block', verticalAlign: 'middle' }}
    >
      {/* Green right-hand upright and diagonal of the supplied D'Mastru mark. */}
      <path d="M116 4 V156 H14 L42 128 H88 V72 L50 110 L31 91 Z" fill="#7AAB55" />
      <path d="M116 4 V36 L61 91 L42 72 Z" fill="#7AAB55" />
      {/* Broad blue left-hand D form, kept above the green overlap. */}
      <path d="M4 4 H34 L65 34 C78 47 77 58 63 70 L4 119 V81 L44 48 L4 10 Z" fill="#4A70AE" />
      <path d="M4 157 V125 L48 87 L66 105 Z" fill="#4A70AE" />
    </svg>
  );
}

