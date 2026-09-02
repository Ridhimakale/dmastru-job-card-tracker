import type { ApiError, Session } from './types';
const API = import.meta.env.VITE_API_URL ?? '';
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
  link.click();
  URL.revokeObjectURL(url);
}
export const authApi={ login:(email:string,password:string)=>api<Session>('/api/auth/login',{method:'POST',body:JSON.stringify({email,password})}), refresh:(refresh_token:string)=>api<Session>('/api/auth/refresh',{method:'POST',body:JSON.stringify({refresh_token})}), logout:()=>api<void>('/api/auth/logout',{method:'POST'}), me:()=>api<Session['user']>('/api/auth/me') };
