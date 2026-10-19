import type { ApiError, LoginResponse, MfaSetup, Session } from './types';
// Use the current local API directly in development. This prevents requests from
// being routed to an older API process still listening on the default port.
// Production can set VITE_API_URL when the API uses another host.
const API = import.meta.env.DEV ? 'http://127.0.0.1:8002' : (import.meta.env.VITE_API_URL ?? '');
let getToken: () => string | null = () => null;
let onExpired: () => void = () => {};
export function configureApi(token:()=>string|null, expired:()=>void) { getToken=token; onExpired=expired; }
export async function api<T>(path:string, init:RequestInit = {}):Promise<T> {
  const headers=new Headers(init.headers); headers.set('Accept','application/json');
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  const token=getToken(); if(token) headers.set('Authorization',`Bearer ${token}`);
  const response=await fetch(`${API}${path}`,{...init,headers});
  if(response.status===401 && token) onExpired();
  if(!response.ok){
    const payload=await response.json().catch(()=>null) as ApiError|null;
    const message=payload?.error?.message;
    throw new Error(message || `Request failed (HTTP ${response.status}).`);
  }
  if(response.status===204) return undefined as T;
  return response.json() as Promise<T>;
}
export async function downloadFile(path:string, fallbackName:string) {
  const token=getToken();
  const response=await fetch(`${API}${path}`,{headers:token?{Authorization:`Bearer ${token}`}:{}}); 
  if(!response.ok) throw new Error('Unable to download the file.');
  const blob=await response.blob();
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download=fallbackName;
  link.style.display='none';
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Some browsers begin the download after the click handler completes. Revoking
  // the URL immediately can cancel the download.
  window.setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export const authApi={ login:(email:string,password:string)=>api<LoginResponse>('/api/auth/login',{method:'POST',body:JSON.stringify({email,password})}), setupMfa:(challenge_token:string)=>api<MfaSetup>('/api/auth/mfa/setup',{method:'POST',body:JSON.stringify({challenge_token})}), verifyMfa:(challenge_token:string,code:string,setup:boolean)=>api<Session>(setup?'/api/auth/mfa/setup/verify':'/api/auth/mfa/verify',{method:'POST',body:JSON.stringify({challenge_token,code})}), refresh:(refresh_token:string)=>api<Session>('/api/auth/refresh',{method:'POST',body:JSON.stringify({refresh_token})}), logout:(token?:string)=>api<void>('/api/auth/logout',{method:'POST',headers:token?{Authorization:`Bearer ${token}`}:{}}), me:()=>api<Session['user']>('/api/auth/me') };
