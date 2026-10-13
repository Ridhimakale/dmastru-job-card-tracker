import { FormEvent, ReactElement, ReactNode, useState } from 'react';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { useAuth } from './auth';
import { api } from './api';
import { Button, Card, Field, Status, Logo } from './components';
import type { Role } from './types';
type PrimaryRole='MARKETING_FINANCE'|'ADMIN'|'CONSULTANT'|'MANAGEMENT';
const portals:{id:PrimaryRole;label:string;roles:Role[]}[]=[
  {id:'MARKETING_FINANCE',label:'Marketing / Finance Team',roles:['MARKETING']},
  {id:'ADMIN',label:'Admin Team',roles:['ADMIN']},
  {id:'CONSULTANT',label:'Consultant',roles:['END_USER']},
  {id:'MANAGEMENT',label:'Management',roles:['MANAGEMENT','UNIT_REPRESENTATIVE','DIRECTOR']},
];

export function GuestLayout({ children }: { children: ReactNode }) {
  return (
    <main className="login-split-container">
      <div className="login-left-panel">
        <div className="login-left-content">
          <div className="guest-brand">
            <Logo size={46} />
            <div className="guest-brand-text">
              <span className="brand-name">D'MASTRU</span>
              <span className="brand-tagline">SUPPORT SYSTEM FOR SUSTAINABLE GROWTH</span>
            </div>
          </div>
          
          <h1 className="guest-slogan">
            Every job card, visit and mandate — <span className="highlight">tracked in one place.</span>
          </h1>
          
          <p className="guest-subtext">
            Sign in to log site visits, update job card status, and stay ahead of mandate renewals across your team.
          </p>
          
        </div>
      </div>
      
      <div className="login-right-panel">
        <div className="login-right-header">
          <div className="login-toggle-pills">
            <span className="pill active">Login</span>
            <span className="pill disabled">Dashboard</span>
          </div>
        </div>
        <div className="login-right-content">
          {children}
        </div>
      </div>
    </main>
  );
}

export function Loading(){return <main className="center"><p>Loading your workspace...</p></main>}
export function Login(){
  const {user,login}=useAuth();
  const [selected,setSelected] = useState<PrimaryRole|null>(null);
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [showPassword,setShowPassword]=useState(false);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  
  if(user)return <HomeRedirect/>;
  
  async function submit(e:FormEvent){
    e.preventDefault();
    if(!selected)return;
    setError('');
    setBusy(true);
    try{
      const portal=portals.find(item=>item.id===selected);
      await login(email,password,portal?.roles)
    }catch(e){
      setError(e instanceof Error?e.message:'Unable to sign in.')
    }finally{
      setBusy(false)
    }
  }
  
  if(!selected) {
    return (
      <GuestLayout>
        <div className="login-card-inner">
          <span className="form-eyebrow">TEAM LOGIN</span>
          <h2 className="form-title">Welcome to D'Mastru</h2>
          <p className="form-description">Select the portal you want to access.</p>
          
          <div className="role-choice-grid">
            {portals.map(portal => (
              <Button key={portal.id} type="button" onClick={() => { setSelected(portal.id); setEmail(''); }}>
                {portal.label} Login
              </Button>
            ))}
          </div>
          
          <p className="form-trouble-help">
            Trouble signing in? Contact your site administrator.
          </p>
        </div>
      </GuestLayout>
    );
  }
  
  return (
    <GuestLayout>
      <div className="login-card-inner">
        <span className="form-eyebrow">{portals.find(portal=>portal.id===selected)?.label.toUpperCase()} LOGIN</span>
        <h2 className="form-title">Welcome back</h2>
        <p className="form-description">Enter your credentials to reach the {portals.find(portal=>portal.id===selected)?.label} workspace.</p>
        
        <form onSubmit={submit} noValidate>
          <Field 
          label="Work email"
            name="email" 
            type="email" 
            autoComplete="email" 
            required 
            placeholder="name@company.com"
            value={email} 
            onChange={e => setEmail(e.target.value)} 
          />          <label className="field">
            <span>Password</span>
            <span className="password-input">
              <input name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" required placeholder="••••••••••••" value={password} onChange={e => setPassword(e.target.value)} />
              <button className="password-toggle" type="button" aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} title={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword(visible => !visible)}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />{!showPassword&&<path d="m4 4 16 16" />}</svg>
              </button>
            </span>
          </label>
          
          <div className="form-options">
            <Link to="/forgot-password" className="forgot-link">Forgot password?</Link>
          </div>

          {error && <p className="form-error" role="alert">{error}</p>}
          
          <Button type="submit" disabled={busy}>
            {busy ? 'Signing in...' : 'Sign In →'}
          </Button>
          
          <Button type="button" variant="secondary" onClick={() => { setSelected(null); setEmail(''); setPassword(''); setError(''); }}>
            Choose another portal
          </Button>
        </form>
        
        <p className="form-trouble-help">
          Trouble signing in? Contact your site administrator.
        </p>
        {selected==='CONSULTANT'&&<p className="form-footer-link">New consultant? <Link to="/register">Create an End User account</Link></p>}
      </div>
    </GuestLayout>
  );
}

export function ForgotPassword(){
  const [email,setEmail]=useState('');
  const [message,setMessage]=useState('');
  const [developmentToken,setDevelopmentToken]=useState('');
  const [busy,setBusy]=useState(false);
  
  async function submit(e:FormEvent){
    e.preventDefault();
    setBusy(true);
    try{
      const response=await fetch('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
      const data=await response.json();
      setMessage(data.message??'If an account exists, reset instructions have been sent.');setDevelopmentToken(data.development_reset_token??'')
    }catch{
      setMessage('Unable to request a reset. Please try again.')
    }finally{
      setBusy(false)
    }
  }
  
  return (
    <GuestLayout>
      <div className="login-card-inner">
        <span className="form-eyebrow">ACCOUNT RECOVERY</span>
        <h2 className="form-title">Forgot password</h2>
        <p className="form-description">Enter your registered email address to request reset instructions.</p>
        
        <form onSubmit={submit}>
          <Field 
            label="Email Address" 
            name="email" 
            type="email" 
            required 
            placeholder="name@dmastru.com" 
            value={email} 
            onChange={e => setEmail(e.target.value)}
          />
          <Button type="submit" disabled={busy}>
            {busy ? 'Requesting...' : 'Send reset instructions'}
          </Button>
        </form>
        
        {message && <p className="form-success-info" role="status">{message}</p>}{developmentToken&&<p>Local development reset link: <Link to={`/reset-password?token=${encodeURIComponent(developmentToken)}`}>Reset password</Link></p>}
        
        <p className="form-footer-link">
          <Link to="/login">Back to sign in</Link>
        </p>
      </div>
    </GuestLayout>
  );
}

export function ResetPassword(){
  const [params]=useSearchParams();const token=params.get('token')??'';const [message,setMessage]=useState('');const [success,setSuccess]=useState(false);
  async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const values=Object.fromEntries(new FormData(e.currentTarget));try{const result=await api<{message:string}>('/api/auth/reset-password',{method:'POST',body:JSON.stringify({token,new_password:values.new_password,confirm_password:values.confirm_password})});setMessage(result.message);setSuccess(true)}catch(e){setMessage(e instanceof Error?e.message:'Unable to reset password.')}}
  return <GuestLayout><div className="login-card-inner"><span className="form-eyebrow">ACCOUNT RECOVERY</span><h2 className="form-title">Set a new password</h2>{token?<form className="form-grid" onSubmit={submit}><Field label="New password" name="new_password" type="password" minLength={8} required/><Field label="Confirm new password" name="confirm_password" type="password" minLength={8} required/><Button type="submit">Reset password</Button></form>:<p className="form-error" role="alert">This reset link is missing its token. Request a new link.</p>}{message&&<p className={success?'form-success-info':'form-error'} role="status">{message}</p>}{success&&<Link to="/login">Back to sign in</Link>}{!success&&<p className="form-footer-link"><Link to="/forgot-password">Request another reset link</Link></p>}</div></GuestLayout>;
}

export function HomeRedirect(){const {user}=useAuth();const map:Record<Role,string>={ADMIN:'/admin/dashboard',MARKETING:'/marketing',MANAGEMENT:'/management/dashboard',END_USER:'/user/dashboard',UNIT_REPRESENTATIVE:'/approvals',DIRECTOR:'/approvals'};return <Navigate to={user?map[user.role]:'/login'} replace/>}
export function Guard({roles,children}:{roles:Role[];children:ReactElement}){const {user,loading}=useAuth();const location=useLocation();if(loading)return <Loading/>;if(!user)return <Navigate to="/login" state={{from:location}} replace/>;if(!roles.includes(user.role))return <main className="center"><Card title="Access denied"><p>You do not have permission to access this page.</p></Card></main>;return children}
export function Placeholder({title}:{title:string}){const {user}=useAuth();return <><header className="page-header"><div><p className="eyebrow">{user?.role.replace('_',' ')} WORKSPACE</p><h1>{title}</h1><p className="muted">This secure route is ready for its workflow module.</p></div><Status>Connected</Status></header><Card title="Foundation ready"><p>Authentication, session handling, API authorization, responsive layout, and accessible controls are configured.</p></Card></>}
