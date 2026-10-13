import type { ApiError, Session } from './types';
// Local Vite development has no VITE_API_URL by default. Point it at the
// separately running FastAPI service instead of sending /api requests to Vite.
const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
let getToken: () => string | null = () => null;
let onExpired: () => void = () => {};
export function configureApi(token:()=>string|null, expired:()=>void) { getToken=token; onExpired=expired; }
export async function api<T>(path:string, init:RequestInit = {}):Promise<T> {
  const headers=new Headers(init.headers); headers.set('Accept','application/json');
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  const token=getToken(); if(token) headers.set('Authorization',`Bearer ${token}`);
  const response=await fetch(`${API}${path}`,{...init,headers});
  if(response.status===401 && token) onExpired();
  if(!response.ok){ const data=await response.json().catch(()=>({} as ApiError)) as ApiError; throw new Error(data.error?.message ?? 'Unable to complete the request.'); }
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
export const authApi={ login:(email:string,password:string)=>api<Session>('/api/auth/login',{method:'POST',body:JSON.stringify({email,password})}), refresh:(refresh_token:string)=>api<Session>('/api/auth/refresh',{method:'POST',body:JSON.stringify({refresh_token})}), logout:(token?:string)=>api<void>('/api/auth/logout',{method:'POST',headers:token?{Authorization:`Bearer ${token}`}:{}}), me:()=>api<Session['user']>('/api/auth/me') };
