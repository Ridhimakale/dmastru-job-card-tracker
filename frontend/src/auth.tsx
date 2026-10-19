import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, configureApi } from './api';
import type { Role, Session, User } from './types';
type Auth={user:User|null; loading:boolean; completeLogin:(session:Session,allowedRoles?:Role[])=>Promise<void>; logout:()=>Promise<void>};
const AuthContext=createContext<Auth|null>(null); const KEY='dmastru.session';
export function AuthProvider({children}:{children:React.ReactNode}){
 const [session,setSession]=useState<Session|null>(()=>{try{return JSON.parse(sessionStorage.getItem(KEY)??'null')}catch{return null}}); const [loading,setLoading]=useState(true);
 const persist=useCallback((next:Session|null)=>{setSession(next); if(next)sessionStorage.setItem(KEY,JSON.stringify(next));else sessionStorage.removeItem(KEY)},[]);
 const logout=useCallback(async()=>{const revoke=session?.access_token?authApi.logout(session.access_token):Promise.resolve();persist(null);try{await revoke}catch{}},[persist,session]);
 useEffect(()=>{configureApi(()=>session?.access_token??null,()=>persist(null)); if(!session){setLoading(false);return} authApi.me().then(user=>{if(session.user.id!==user.id||session.user.role!==user.role||session.user.status!==user.status)persist({...session,user})}).catch(()=>persist(null)).finally(()=>setLoading(false))},[session,persist]);
 const completeLogin=useCallback(async(next:Session,allowedRoles?:Role[])=>{if(allowedRoles&&!allowedRoles.includes(next.user.role)){try{await authApi.logout(next.access_token)}catch{}throw new Error('This account belongs to a different login category. Choose the category assigned to your account.')}persist(next)},[persist]);
 const value=useMemo(()=>({user:session?.user??null,loading,completeLogin,logout}),[session,loading,completeLogin,logout]); return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('useAuth must be used inside AuthProvider');return value}
