export type Role = 'ADMIN' | 'MANAGEMENT' | 'END_USER' | 'UNIT_REPRESENTATIVE' | 'DIRECTOR';
export type User = { id:number; full_name:string; email:string; mobile?:string|null; role:Role; status:string };
export type Session = { access_token:string; refresh_token:string; token_type:string; user:User };
export type ApiError = { success?:false; error?:{ code:string; message:string } };
