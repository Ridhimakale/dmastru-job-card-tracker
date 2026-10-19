import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth';
import { AppLayout } from './layout';
import { ForgotPassword, Guard, HomeRedirect, Login, ResetPassword } from './pages';
import { AdminDashboard, AdminDocumentReview, AuditLog, JobCards, Notifications, Settings, Workflow } from './admin';
import { MasterData } from './master_data';
import { Activity, Documents, MyJobCards, Notifications as UserNotifications, Profile, Registration, UserDashboard, Visits } from './user';
import { AdminReports, ApprovalDashboard, ApprovalQueue, ManagementAudit, ManagementDashboard, ManagementNotifications, ManagementReviews, Projects, Reports } from './management';
import { FinanceDashboard, MarketingWorkspace, PaymentsWorkspace } from './marketing';
import { ClosureWorkspace } from './closure';
import { Packages } from './packages';
import { JobCardWorkspace } from './job_card_workspace';
import { Referrals } from './referrals';
import { ActionsWorkspace, Workflows } from './workflow';

export function App(){return <BrowserRouter><AuthProvider><Routes>
  <Route path="/login" element={<Login/>}/><Route path="/forgot-password" element={<ForgotPassword/>}/><Route path="/reset-password" element={<ResetPassword/>}/><Route path="/register" element={<Registration/>}/>
  <Route element={<AppLayout/>}>
    <Route path="/referrals" element={<Guard roles={['ADMIN','MANAGEMENT','FINANCE','END_USER','DIRECTOR','MARKETING']}><Referrals/></Guard>}/>
    <Route path="/job-cards/:cardId" element={<Guard roles={['ADMIN','MANAGEMENT','END_USER','DIRECTOR']}><JobCardWorkspace/></Guard>}/>
    <Route path="/admin/dashboard" element={<Guard roles={['ADMIN']}><AdminDashboard/></Guard>}/><Route path="/admin/job-cards" element={<Guard roles={['ADMIN']}><JobCards/></Guard>}/><Route path="/admin/master-data" element={<Guard roles={['ADMIN']}><MasterData/></Guard>}/><Route path="/admin/users" element={<Guard roles={['ADMIN']}><Navigate to="/admin/master-data" replace/></Guard>}/><Route path="/admin/msmes" element={<Guard roles={['ADMIN']}><Navigate to="/admin/master-data" replace/></Guard>}/><Route path="/admin/workflow" element={<Guard roles={['ADMIN']}><Workflow/></Guard>}/><Route path="/admin/documents" element={<Guard roles={['ADMIN']}><AdminDocumentReview/></Guard>}/><Route path="/admin/reports" element={<Guard roles={['ADMIN']}><AdminReports/></Guard>}/><Route path="/admin/audit" element={<Guard roles={['ADMIN']}><AuditLog/></Guard>}/><Route path="/admin/notifications" element={<Guard roles={['ADMIN']}><Notifications/></Guard>}/><Route path="/admin/settings" element={<Guard roles={['ADMIN']}><Settings/></Guard>}/>
    <Route path="/management/dashboard" element={<Guard roles={['MANAGEMENT']}><ManagementDashboard/></Guard>}/><Route path="/management/projects" element={<Guard roles={['MANAGEMENT']}><Projects/></Guard>}/><Route path="/management/reviews" element={<Guard roles={['MANAGEMENT']}><ManagementReviews/></Guard>}/><Route path="/management/reports" element={<Guard roles={['MANAGEMENT']}><Reports/></Guard>}/><Route path="/management/audit" element={<Guard roles={['MANAGEMENT']}><ManagementAudit/></Guard>}/><Route path="/management/notifications" element={<Guard roles={['MANAGEMENT']}><ManagementNotifications/></Guard>}/><Route path="/management/planner" element={<Guard roles={['MANAGEMENT']}><Visits/></Guard>}/>
    <Route path="/marketing" element={<Guard roles={['MARKETING']}><MarketingWorkspace/></Guard>}/>
    <Route path="/marketing/payments" element={<Guard roles={['MANAGEMENT']}><PaymentsWorkspace/></Guard>}/>
    <Route path="/finance/dashboard" element={<Guard roles={['FINANCE']}><FinanceDashboard/></Guard>}/><Route path="/finance/payments" element={<Guard roles={['FINANCE']}><PaymentsWorkspace/></Guard>}/>
    <Route path="/marketing/closure" element={<Guard roles={['ADMIN']}><ClosureWorkspace/></Guard>}/>
    <Route path="/admin/packages" element={<Guard roles={['ADMIN']}><Packages/></Guard>}/>
    <Route path="/admin/planner" element={<Guard roles={['ADMIN']}><Visits/></Guard>}/>
    <Route path="/admin/workflows" element={<Guard roles={['ADMIN','MANAGEMENT','FINANCE','END_USER','DIRECTOR','MARKETING']}><Workflows/></Guard>}/>
    <Route path="/actions" element={<Guard roles={['ADMIN','MANAGEMENT','FINANCE','END_USER','DIRECTOR']}><ActionsWorkspace/></Guard>}/>
    <Route path="/user/dashboard" element={<Guard roles={['END_USER']}><UserDashboard/></Guard>}/><Route path="/user/job-cards" element={<Guard roles={['END_USER']}><MyJobCards/></Guard>}/><Route path="/user/mandays" element={<Guard roles={['END_USER']}><MyJobCards mandaysOnly/></Guard>}/><Route path="/user/visits" element={<Guard roles={['END_USER']}><Visits/></Guard>}/><Route path="/user/documents" element={<Guard roles={['END_USER']}><Documents/></Guard>}/><Route path="/user/activity" element={<Guard roles={['END_USER']}><Activity/></Guard>}/><Route path="/user/activities" element={<Guard roles={['END_USER']}><Navigate to="/user/visits" replace/></Guard>}/><Route path="/user/notifications" element={<Guard roles={['END_USER']}><UserNotifications/></Guard>}/><Route path="/user/profile" element={<Guard roles={['END_USER']}><Profile/></Guard>}/>
    <Route path="/approvals/dashboard" element={<Guard roles={['DIRECTOR']}><ApprovalDashboard/></Guard>}/><Route path="/approvals" element={<Guard roles={['DIRECTOR']}><ApprovalQueue/></Guard>}/><Route path="/approval-notifications" element={<Guard roles={['DIRECTOR']}><ManagementNotifications/></Guard>}/>
  </Route><Route path="/" element={<HomeRedirect/>}/><Route path="*" element={<Navigate to="/" replace/>}/>
</Routes></AuthProvider></BrowserRouter>}
