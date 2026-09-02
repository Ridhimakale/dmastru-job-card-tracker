import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth';
import { AppLayout } from './layout';
import { ForgotPassword, Guard, HomeRedirect, Login } from './pages';
import { AdminDashboard, AuditLog, JobCards, Msmes, Notifications, Settings, Users as LegacyUsers, Workflow } from './admin';
import { Users } from './admin_users';
import { Activity, Documents, MyJobCards, Notifications as UserNotifications, Profile, Registration, UserDashboard, Visits } from './user';
import { ApprovalQueue, ManagementAudit, ManagementDashboard, ManagementNotifications, Projects, Reports } from './management';

export function App(){return <BrowserRouter><AuthProvider><Routes>
  <Route path="/login" element={<Login/>}/><Route path="/forgot-password" element={<ForgotPassword/>}/><Route path="/register" element={<Registration/>}/>
  <Route element={<AppLayout/>}>
    <Route path="/admin/dashboard" element={<Guard roles={['ADMIN']}><AdminDashboard/></Guard>}/><Route path="/admin/job-cards" element={<Guard roles={['ADMIN']}><JobCards/></Guard>}/><Route path="/admin/users" element={<Guard roles={['ADMIN']}><Users/></Guard>}/><Route path="/admin/msmes" element={<Guard roles={['ADMIN']}><Msmes/></Guard>}/><Route path="/admin/workflow" element={<Guard roles={['ADMIN']}><Workflow/></Guard>}/><Route path="/admin/audit" element={<Guard roles={['ADMIN']}><AuditLog/></Guard>}/><Route path="/admin/notifications" element={<Guard roles={['ADMIN']}><Notifications/></Guard>}/><Route path="/admin/settings" element={<Guard roles={['ADMIN']}><Settings/></Guard>}/>
    <Route path="/management/dashboard" element={<Guard roles={['MANAGEMENT']}><ManagementDashboard/></Guard>}/><Route path="/management/projects" element={<Guard roles={['MANAGEMENT']}><Projects/></Guard>}/><Route path="/management/reports" element={<Guard roles={['MANAGEMENT']}><Reports/></Guard>}/><Route path="/management/audit" element={<Guard roles={['MANAGEMENT']}><ManagementAudit/></Guard>}/><Route path="/management/notifications" element={<Guard roles={['MANAGEMENT']}><ManagementNotifications/></Guard>}/>
    <Route path="/user/dashboard" element={<Guard roles={['END_USER']}><UserDashboard/></Guard>}/><Route path="/user/job-cards" element={<Guard roles={['END_USER']}><MyJobCards/></Guard>}/><Route path="/user/visits" element={<Guard roles={['END_USER']}><Visits/></Guard>}/><Route path="/user/documents" element={<Guard roles={['END_USER']}><Documents/></Guard>}/><Route path="/user/activity" element={<Guard roles={['END_USER']}><Activity/></Guard>}/><Route path="/user/notifications" element={<Guard roles={['END_USER']}><UserNotifications/></Guard>}/><Route path="/user/profile" element={<Guard roles={['END_USER']}><Profile/></Guard>}/>
    <Route path="/approvals" element={<Guard roles={['UNIT_REPRESENTATIVE','DIRECTOR']}><ApprovalQueue/></Guard>}/><Route path="/approval-notifications" element={<Guard roles={['UNIT_REPRESENTATIVE','DIRECTOR']}><ManagementNotifications/></Guard>}/>
  </Route><Route path="/" element={<HomeRedirect/>}/><Route path="*" element={<Navigate to="/" replace/>}/>
</Routes></AuthProvider></BrowserRouter>}
