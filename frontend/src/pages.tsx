import { FormEvent, ReactElement, ReactNode, useState } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './auth';
import { Button, Card, Field, Status, Logo } from './components';
import type { Role } from './types';
type PrimaryRole='Admin'|'Management'|'Consultant';
const accounts:Record<PrimaryRole,string>={Admin:'admin@dmastru.local',Management:'management@dmastru.local',Consultant:'consultant@dmastru.local'};

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
          
          <div className="guest-stats">
            <div className="guest-stat-item">
              <div className="stat-value">128</div>
              <div className="stat-label">JOB CARDS OPEN</div>
            </div>
            <div className="guest-stat-item">
              <div className="stat-value">34</div>
              <div className="stat-label">VISITS THIS WEEK</div>
            </div>
            <div className="guest-stat-item">
              <div className="stat-value">6</div>
              <div className="stat-label">MANDATES DUE</div>
            </div>
          </div>
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
      await login(email,password)
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
            {(Object.keys(accounts) as PrimaryRole[]).map(role => (
              <Button key={role} type="button" onClick={() => { setSelected(role); setEmail(accounts[role]); }}>
                {role} Login
              </Button>
            ))}
          </div>
          
          <p className="form-footer-link">
            New consultant? <Link to="/register">Create an End User account</Link>
          </p>
          
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
        <span className="form-eyebrow">{selected.toUpperCase()} LOGIN</span>
        <h2 className="form-title">Welcome back</h2>
        <p className="form-description">Enter your credentials to reach your {selected.toLowerCase()} dashboard.</p>
        
        <form onSubmit={submit} noValidate>
          <Field 
            label="Employee ID or Email" 
            name="email" 
            type="email" 
            autoComplete="email" 
            required 
            placeholder="e.g. DM-0142 or name@dmastru.com"
            value={email} 
            onChange={e => setEmail(e.target.value)} 
          />          <label className="field">
            <span>Password</span>
            <span className="password-input">
              <input name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" required placeholder="••••••••••••" value={password} onChange={e => setPassword(e.target.value)} />
              <button className="password-toggle" type="button" aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} title={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword(visible => !visible)}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" /></svg>
              </button>
            </span>
          </label>
          
          <div className="form-options">
            <label className="checkbox-label">
              <input type="checkbox" defaultChecked />
              <span>Keep me signed in</span>
            </label>
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
      </div>
    </GuestLayout>
  );
}

export function ForgotPassword(){
  const [email,setEmail]=useState('');
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  
  async function submit(e:FormEvent){
    e.preventDefault();
    setBusy(true);
    try{
      const response=await fetch('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
      const data=await response.json();
      setMessage(data.message??'If an account exists, reset instructions have been sent.')
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
        
        {message && <p className="form-success-info" role="status">{message}</p>}
        
        <p className="form-footer-link">
          <Link to="/login">Back to sign in</Link>
        </p>
      </div>
    </GuestLayout>
  );
}

export function HomeRedirect(){const {user}=useAuth();const map:Record<Role,string>={ADMIN:'/admin/dashboard',MANAGEMENT:'/management/dashboard',END_USER:'/user/dashboard',UNIT_REPRESENTATIVE:'/approvals',DIRECTOR:'/approvals'};return <Navigate to={user?map[user.role]:'/login'} replace/>}
export function Guard({roles,children}:{roles:Role[];children:ReactElement}){const {user,loading}=useAuth();const location=useLocation();if(loading)return <Loading/>;if(!user)return <Navigate to="/login" state={{from:location}} replace/>;if(!roles.includes(user.role))return <main className="center"><Card title="Access denied"><p>You do not have permission to access this page.</p></Card></main>;return children}
export function Placeholder({title}:{title:string}){const {user}=useAuth();return <><header className="page-header"><div><p className="eyebrow">{user?.role.replace('_',' ')} WORKSPACE</p><h1>{title}</h1><p className="muted">This secure route is ready for its workflow module.</p></div><Status>Connected</Status></header><Card title="Foundation ready"><p>Authentication, session handling, API authorization, responsive layout, and accessible controls are configured.</p></Card></>}

