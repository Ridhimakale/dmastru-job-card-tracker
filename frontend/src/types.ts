export type Role = 'ADMIN' | 'MARKETING' | 'MANAGEMENT' | 'END_USER' | 'UNIT_REPRESENTATIVE' | 'DIRECTOR';
export type User = { id:number; full_name:string; email:string; mobile?:string|null; role:Role; status:string; department_id?:number|null };
export type Session = { access_token:string; refresh_token:string; token_type:string; user:User };
export type ApiError = { success?:false; error?:{ code:string; message:string } };
