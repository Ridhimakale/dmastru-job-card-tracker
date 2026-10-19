export type Role = 'ADMIN' | 'MARKETING' | 'FINANCE' | 'MANAGEMENT' | 'END_USER' | 'UNIT_REPRESENTATIVE' | 'DIRECTOR';
export type User = { id:number; full_name:string; email:string; mobile?:string|null; role:Role; status:string; department_id?:number|null; mfa_enabled?:boolean };
export type Session = { access_token:string; refresh_token:string; token_type:string; user:User };
export type MfaChallenge = { challenge_token:string; setup_required:boolean; password_change_required:boolean; full_name:string; email:string };
export type LoginResponse = MfaChallenge | Session;
export type MfaSetup = { secret:string; provisioning_uri:string };
export type ApiError = { success?:false; error?:{ code:string; message:string } };
