import { FormEvent, ReactElement, ReactNode, useState } from 'react';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { useAuth } from './auth';
import { api, authApi } from './api';
import { Button, Card, Field, Status, Logo } from './components';
import type { Role, Session, MfaChallenge, MfaSetup } from './types';
import { createQrMatrix, qrSvgPath } from './qr';

export function GuestLayout({ children, scrollable = false }: { children: ReactNode; scrollable?: boolean }) {
  return (
    <main className={`login-split-container${scrollable ? ' login-mfa-scroll' : ''}`}>
      <div className="login-left-panel">
        <div className="login-left-content">
          <div className="guest-brand">
            <Logo className="guest-logo-mark" size={78} />
            <div className="guest-brand-text">
              <span className="brand-name">D'MASTRU</span>
              <span className="brand-legal">PVT LTD</span>
              <span className="brand-tagline">Support System for Sustainable Growth</span>
            </div>
          </div>
          
          <h1 className="guest-slogan">
            Every project, connected. <span className="highlight">Every step, in control.</span>
          </h1>
          
          <p className="guest-subtext">
            Manage enquiries, payments, job cards, consultant work, and approvals in one workspace.
          </p>
          
        </div>
      </div>
      
      <div className="login-right-panel">
        <div className="login-right-content">
          {children}
        </div>
      </div>
    </main>
  );
}

export function Loading(){return <main className="center"><p>Loading your workspace...</p></main>}
function AuthenticatorQr({uri}:{uri:string}){
  const matrix=createQrMatrix(uri);
  if(!matrix)return <p className="muted">This account label is too long to encode as a QR code. Use the manual setup key below.</p>;
  const size=matrix.length;
  return <svg className="mfa-qr" viewBox={`-4 -4 ${size+8} ${size+8}`} role="img" aria-label="Authenticator app setup QR code" shapeRendering="crispEdges"><rect x={-4} y={-4} width={size+8} height={size+8} rx="2" fill="white"/><path d={qrSvgPath(matrix)} fill="#14263d"/></svg>;
}
export function Login(){
  const {user,completeLogin}=useAuth();
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [showPassword,setShowPassword]=useState(false);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [challenge,setChallenge]=useState<MfaChallenge|null>(null);
  const [setup,setSetup]=useState<MfaSetup|null>(null);
  const [code,setCode]=useState('');
  const [newPassword,setNewPassword]=useState('');
  const [confirmNewPassword,setConfirmNewPassword]=useState('');
  const [notice,setNotice]=useState('');
  if(user)return <HomeRedirect/>
  
  async function submit(e:FormEvent){
    e.preventDefault();
    setError('');
    setBusy(true);
    try{
      const next=await authApi.login(email,password);
      setPassword('');
      if('access_token' in next){await completeLogin(next);return}
      setChallenge(next);
      if(next.setup_required&&!next.password_change_required)setSetup(await authApi.setupMfa(next.challenge_token));
    }catch(e){
      setError(e instanceof Error?e.message:'Unable to sign in.')
    }finally{
      setBusy(false)
    }
  }

  async function verify(e:FormEvent){
    e.preventDefault();if(!challenge)return;setError('');setBusy(true);
    try{const session:Session=await authApi.verifyMfa(challenge.challenge_token,code,challenge.setup_required);await completeLogin(session)}
    catch(e){setError(e instanceof Error?e.message:'Unable to verify authenticator code.')}
    finally{setBusy(false)}
  }
  async function changeInitialPassword(e:FormEvent){
    e.preventDefault();if(!challenge)return;setError('');setBusy(true);
    try{
      const result=await api<{message:string}>('/api/auth/initial-password',{method:'POST',body:JSON.stringify({challenge_token:challenge.challenge_token,new_password:newPassword,confirm_password:confirmNewPassword})});
      setNotice(result.message);setChallenge(null);setSetup(null);setCode('');setPassword('');setNewPassword('');setConfirmNewPassword('');
    }catch(e){setError(e instanceof Error?e.message:'Unable to save your permanent password.')}
    finally{setBusy(false)}
  }
  function startOver(){setChallenge(null);setSetup(null);setCode('');setNewPassword('');setConfirmNewPassword('');setError('')}
  
  return (
    <GuestLayout scrollable={Boolean(challenge && !challenge.password_change_required)}>
      <div className="login-card-inner">
        <span className="form-eyebrow">SECURE TEAM PORTAL</span>
        <h2 className="form-title">{challenge?(challenge.password_change_required?'Set your permanent password':challenge.setup_required?'Set up authenticator':'Verify your identity'):'Welcome back'}</h2>
        <p className="form-description">{challenge?(challenge.password_change_required?'Your Admin-provided initial password is temporary. Create and confirm a permanent password to continue.':challenge.setup_required?'Add this account to an authenticator app, then enter its six-digit code.':'Enter the six-digit code from your authenticator app.'):'Sign in with your registered work email and password. We will open the workspace assigned to your account.'}</p>
        {notice&&<p className="form-success-info" role="status">{notice}</p>}

        {!challenge&&<form onSubmit={submit} noValidate>
          <Field 
          label="Registered work email (login ID)"
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
              <input name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" required placeholder="Enter your password" value={password} onChange={e => setPassword(e.target.value)} />
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
            {busy ? 'Signing in...' : 'Sign in'}
          </Button>
          
        </form>}

        {challenge?.password_change_required&&<form onSubmit={changeInitialPassword} noValidate>
          <Field label="New permanent password" name="new_password" type="password" autoComplete="new-password" minLength={8} required placeholder="Create a permanent password" value={newPassword} onChange={e=>setNewPassword(e.target.value)}/>
          <Field label="Confirm permanent password" name="confirm_password" type="password" autoComplete="new-password" minLength={8} required placeholder="Enter it again" value={confirmNewPassword} onChange={e=>setConfirmNewPassword(e.target.value)}/>
          <p className="muted">Use at least 8 characters with uppercase and lowercase letters, a number, and a special character.</p>
          {error&&<p className="form-error" role="alert">{error}</p>}
          <Button type="submit" disabled={busy||!newPassword||!confirmNewPassword}>{busy?'Saving password...':'Save permanent password'}</Button>
          <Button type="button" variant="quiet" onClick={startOver}>Back to sign in</Button>
        </form>}

        {challenge&&!challenge.password_change_required&&<form onSubmit={verify} noValidate>
          {challenge.setup_required&&setup&&<div className="mfa-enrollment"><p>Scan this QR code with your authenticator app to link this account:</p><AuthenticatorQr uri={setup.provisioning_uri}/><p className="muted">If you can’t scan it, choose <strong>Add account manually</strong> and use this setup key:</p><code>{setup.secret}</code><p className="muted">Account: {challenge.email}<br/>Issuer: D'Mastru</p><Button type="button" variant="secondary" onClick={()=>{void navigator.clipboard?.writeText(setup.secret)}}>Copy setup key</Button></div>}
          <Field label="Authenticator code" name="mfa_code" type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required placeholder="6-digit code" value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,'').slice(0,6))}/>
          {error&&<p className="form-error" role="alert">{error}</p>}
          <Button type="submit" disabled={busy||code.length!==6}>{busy?(challenge.setup_required?'Setting up...':'Verifying...'):(challenge.setup_required?'Enable MFA and sign in':'Verify and sign in')}</Button>
          <Button type="button" variant="quiet" onClick={startOver}>Start over</Button>
        </form>}
        
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
  return <GuestLayout><div className="login-card-inner"><span className="form-eyebrow">ACCOUNT RECOVERY</span><h2 className="form-title">Set a new password</h2>{token?<form className="form-grid" onSubmit={submit}><p className="muted">Use at least 8 characters with uppercase and lowercase letters, a number, and a special character. Confirm the same password below.</p><Field label="New permanent password" name="new_password" type="password" autoComplete="new-password" minLength={8} required/><Field label="Confirm new password" name="confirm_password" type="password" autoComplete="new-password" minLength={8} required/><Button type="submit">Save permanent password</Button></form>:<p className="form-error" role="alert">This reset link is missing its token. Request a new link.</p>}{message&&<p className={success?'form-success-info':'form-error'} role="status">{message}</p>}{success&&<Link to="/login">Back to sign in</Link>}{!success&&<p className="form-footer-link"><Link to="/forgot-password">Request another reset link</Link></p>}</div></GuestLayout>;
}

export function HomeRedirect(){const {user}=useAuth();const map:Record<Role,string>={ADMIN:'/admin/dashboard',MARKETING:'/marketing',FINANCE:'/finance/dashboard',MANAGEMENT:'/management/dashboard',END_USER:'/user/dashboard',UNIT_REPRESENTATIVE:'/referrals'};return <Navigate to={user?map[user.role]:'/login'} replace/>}
export function Guard({roles,children}:{roles:Role[];children:ReactElement}){const {user,loading}=useAuth();const location=useLocation();if(loading)return <Loading/>;if(!user)return <Navigate to="/login" state={{from:location}} replace/>;if(!roles.includes(user.role))return <main className="center"><Card title="Access denied"><p>You do not have permission to access this page.</p></Card></main>;return children}
export function Placeholder({title}:{title:string}){const {user}=useAuth();return <><header className="page-header"><div><p className="eyebrow">{user?.role.replace('_',' ')} WORKSPACE</p><h1>{title}</h1><p className="muted">This secure route is ready for its workflow module.</p></div><Status>Connected</Status></header><Card title="Foundation ready"><p>Authentication, session handling, API authorization, responsive layout, and accessible controls are configured.</p></Card></>}
