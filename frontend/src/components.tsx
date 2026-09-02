import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react';
export function Button({children,variant='primary',...props}:ButtonHTMLAttributes<HTMLButtonElement>&{variant?:'primary'|'secondary'|'quiet'}){return <button className={`button ${variant}`} {...props}>{children}</button>}
export function Field({label,error,...props}:{label:string;error?:string}&InputHTMLAttributes<HTMLInputElement>){const id=props.id??props.name;return <label className="field" htmlFor={id}><span>{label}</span><input id={id} aria-invalid={Boolean(error)} aria-describedby={error?`${id}-error`:undefined} {...props}/>{error&&<small id={`${id}-error`} role="alert">{error}</small>}</label>}
export function Card({children,title}:{children:ReactNode;title?:string}){return <section className="card">{title&&<h2>{title}</h2>}{children}</section>}
export function Status({children}:{children:ReactNode}){const statusClass=typeof children==='string'?children.toLowerCase().replace(/[^a-z0-9]/g,'-'):'';return <span className={`status ${statusClass}`}>{children}</span>}
export function Logo({ className = '', size = 40 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size * 1.3}
      viewBox="0 0 100 130"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ display: 'inline-block', verticalAlign: 'middle' }}
    >
      {/* Blue shape forming D */}
      <path
        d="M10 10 H35 L75 55 V75 L35 120 H10 V98 L48 65 L10 32 Z"
        fill="#3E63A7"
      />
      {/* Green shape forming M */}
      <path
        d="M90 32 V120 H65 L25 75 V55 L65 10 H90 V32 L52 65 L90 98 Z"
        fill="#7AAB55"
      />
    </svg>
  );
}

