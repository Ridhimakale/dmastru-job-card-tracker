# Business Requirements Document

## Project

**System name:** D'Mastru Job Card - Visit & Manday Tracker  
**Organization:** D'Mastru Private Limited  
**Document type:** Business Requirements Document  
**Version:** 1.0  
**Date:** 26 August 2026  

## 1. Executive Summary

D'Mastru Private Limited requires a web-based Job Card, Visit, Manday, Document, Approval, Dashboard, and Reporting Management System. The system replaces the existing spreadsheet-based "Job Card - Visit & Manday Tracker" with a secure, role-based digital platform.

The original spreadsheet captures MSME details, ZED ID, certificate level, consultant assignments, project dates, required mandays, visit-level work records, milestone progress, document submissions, consultant confirmation, unit representative confirmation, and director final review. The website must preserve these business controls while improving accuracy, visibility, auditability, mobile usability, approvals, reporting, and data security.

## 2. Business Objectives

The system must:

1. Digitize the complete Job Card lifecycle from creation to final director approval.
2. Track required mandays, completed mandays, remaining mandays, visit count, milestone progress, delays, and overruns.
3. Allow consultants/end users to submit visit records and documents from mobile and desktop.
4. Enforce role-specific workflows for Admin, Consultant/End User, Management, Unit Representative, and Director.
5. Prevent unauthorized access, cross-consultant data leakage, and improper approvals.
6. Maintain full audit history for login, user changes, role changes, status changes, job card changes, approvals, documents, and visits.
7. Provide dashboards, queues, reports, filters, and exports for operational monitoring.
8. Preserve completed Job Cards as locked records unless an Admin performs an audited reopen action.

## 3. Scope

### 3.1 In Scope

- User authentication and role-based access control.
- Admin, Management, Consultant/End User, Unit Representative, and Director workspaces.
- User creation, registration, approval, activation, deactivation, locking, unlocking, role editing, and password reset.
- MSME master creation, editing, searching, and filtering.
- Job Card creation, editing, reassignment, soft deletion, reopen workflow, and completion lock.
- Milestone setup and tracking.
- Visit drafts, editing, submission, rejection, correction, resubmission, reviewer queue, and attachments.
- Manday calculation with at least 4-decimal precision.
- Document upload, validation, versioning, download, approval, rejection, and rework.
- Stage-wise approval workflow.
- Notifications.
- Dashboards, KPIs, attention queues, reports, CSV/XLSX/PDF exports.
- Audit logs.
- Production readiness documentation.

### 3.2 Out of Scope for Initial Release

- External payment gateway integration.
- Real-time chat between users.
- Offline-first mobile app.
- Integration with government/ZED external portals.
- Biometric or digital signature provider integration.

## 4. Stakeholders

| Stakeholder | Business Interest |
| --- | --- |
| Admin | Creates master data, users, MSMEs, Job Cards, assignments, and manages system configuration. |
| Consultant / End User | Records visits, work details, time spent, documents, and tracks approval status. |
| Management | Reviews operational progress, documents, visits, delays, overruns, and reports. |
| Unit Representative | Performs assigned unit-level confirmation or rejection. |
| Director | Performs final review and approves completion. |
| D'Mastru Leadership | Needs accurate visibility into workload, completed mandays, pending approvals, and delayed projects. |

## 5. User Roles

### 5.1 Admin

Admin must be able to:

- Sign in through the Admin login path.
- Create and manage users.
- Assign and change roles.
- Activate, deactivate, unlock, and reset passwords.
- Create and edit MSME records.
- Create, edit, archive, reopen, and reassign Job Cards.
- Configure master data and system settings.
- View all records, audit logs, notifications, reports, and exports.
- Access Admin-only routes.

Admin must not:

- Approve Management-only, Unit Representative-only, or Director-only approval stages unless explicitly assigned through a configured business rule.
- Modify completed Job Cards unless reopened through audited Admin workflow.

### 5.2 Consultant / End User

Consultant/End User must be able to:

- Register through the Consultant/End User registration flow.
- Sign in through the Consultant login path after activation.
- View only assigned Job Cards.
- Create visit drafts.
- Edit own draft visits.
- Submit visits.
- Correct and resubmit rejected visits.
- Upload documents and corrected document versions.
- View milestones, manday totals, documents, approval status, activity history, and notifications.

Consultant/End User must not:

- View other consultants' Job Cards.
- Approve Job Cards.
- Edit completed Job Cards.
- Upload new visits or documents after Job Card completion.

### 5.3 Management

Management must be able to:

- Sign in through the Management login path.
- View dashboards, analytics, filters, reports, and attention queues.
- Review submitted visits and documents where business rules allow.
- Export filtered reports.
- Track delayed, overrun, pending, and completed Job Cards.

Management must not:

- Approve both Unit Representative and Director approval stages.
- Bypass assigned role approval rules.
- Access Admin-only user and master-data mutation routes if strict separation is enabled.

### 5.4 Unit Representative

Unit Representative must be able to:

- Sign in through the Management/Reviewer login path or a configured reviewer workspace.
- View Job Cards assigned to them for unit-level confirmation.
- Approve or reject the Unit Representative approval stage.
- Provide rejection comments.
- Receive approval request, rework, and completion notifications.

Unit Representative must not:

- Approve Director final review.
- Approve Job Cards not assigned to them.

### 5.5 Director

Director must be able to:

- Sign in through the Management/Reviewer login path or a configured Director workspace.
- View Job Cards assigned for Director final review.
- Approve or reject final completion.
- Add final remarks.
- Trigger Job Card completion after final approval.

Director must not:

- Approve Unit Representative stage.
- Approve Job Cards not assigned to them.

## 6. Source Spreadsheet Mapping

| Spreadsheet Section | Website Module |
| --- | --- |
| Header, format number, revision, Job Card number | Job Card detail and PDF export |
| MSME Name, ZED ID, Certificate Level | MSME Master and Job Card detail |
| Lead Consultant, Co-consultant, Subject Expert | Job Card assignments |
| Project Start Date, Project End Date, Location | Job Card planning |
| Required MD, Hours/MD, Required Hours | Manday engine |
| Visit Plan M1/M2/M3 | Milestone planning |
| Visit & Manday Details rows | Visit module |
| Rating A-E | Visit rating |
| Manday Calculation | Manday summary engine |
| Milestone Visit Summary | Milestone progress dashboard |
| Milestone Period & Work Details | Milestone management |
| Case Study, Technology Recommendation, Audit | Document module |
| Consultant Confirmation | Consultant confirmation approval stage |
| Unit Representative Confirmation | Unit Representative approval stage |
| Director Final Review | Director approval stage |

## 7. Functional Requirements

### 7.1 Authentication

The system shall:

- Provide separate login entry choices for Admin, Management, and Consultant/End User.
- Allow Consultant/End User account registration.
- Restrict Admin and Management account creation to Admin-controlled user management.
- Support forgot password and password reset.
- Lock accounts after repeated failed login attempts.
- Record failed login attempts in audit logs.
- Prevent inactive, locked, or pending users from accessing the system.
- Clear session state on logout and prevent back-button access to protected pages.

### 7.2 User and Role Management

The system shall:

- Allow Admin to create users with name, email, role, mobile, status, and temporary password.
- Allow Admin to edit user details and role.
- Allow Admin to activate pending or inactive users.
- Allow Admin to deactivate users except where prevented by business rules.
- Allow Admin to unlock locked accounts.
- Allow Admin reset password.
- Audit old and new values for role, status, and profile changes.

### 7.3 MSME Master

The system shall:

- Capture MSME name, MSME code, ZED ID, certificate level, address, city, state, contact person, and status.
- Allow search and filtering by name, code, ZED ID, certificate level, city, state, and status.
- Prevent duplicate MSME name/code where applicable.
- Support edit and soft deletion/inactivation.

### 7.4 Job Card Management

The system shall:

- Create Job Cards with Job Card number, MSME, location, project start date, project end date, required mandays, lead consultant, optional co-consultant, optional subject expert, Unit Representative, and Director.
- Require Job Card number to be unique and at least 3 characters.
- Require end date to be equal to or later than start date.
- Require active users for all assignments.
- Require distinct users where the same consultant cannot occupy multiple consultant assignment roles.
- Calculate required hours as `required mandays x hours per manday`.
- Default hours per manday to 8 unless configured otherwise.
- Support Job Card filters for MSME, consultant, ZED ID, certificate level, delayed, overrun, status, and search text.
- Support pagination.
- Maintain reassignment history.
- Soft-delete archived Job Cards.
- Prevent edits, reassignment, visits, documents, approvals, or archival changes once a Job Card is completed.
- Allow Admin to reopen a completed Job Card only through an audited reopen workflow with mandatory reason.

### 7.5 Milestone Management

The system shall:

- Support milestones such as M1, M2, and M3.
- Capture milestone code, name, description, planned visits, start date, closing date, milestone payment date, and portal report submission date.
- Track actual completed visits per milestone.
- Calculate milestone progress.
- Validate milestone closing date is not earlier than start date.
- Validate payment date business rules.
- Generate milestone due and delay notifications.

### 7.6 Visit Management

The system shall:

- Allow assigned consultants to create visit drafts.
- Capture milestone, visit date, hours, minutes, work details, rating, remarks, and optional attachment.
- Auto-generate visit number per Job Card.
- Validate visit date falls within Job Card project dates.
- Validate duration is positive before submission.
- Convert hours and minutes into working minutes.
- Calculate mandays using `working minutes / 480`.
- Preserve at least 4 decimal places for manday values.
- Allow editing only own draft visits.
- Allow submission of draft visits.
- Allow reviewer rejection of submitted visits with reason.
- Allow consultant correction and resubmission of rejected visits.
- Maintain visit history and audit records.
- Prevent visit creation, edit, submission, rejection, or resubmission once Job Card is completed.

### 7.7 Manday Engine

The system shall:

- Track required mandays and required hours separately.
- Track completed visits separately from completed mandays.
- Treat 1 manday as 8 working hours by default.
- Calculate 6 hours 30 minutes as `0.8125` manday.
- Calculate completed mandays, remaining mandays, completed hours, remaining hours, progress percentage, overrun status, and delay days.
- Return manday values in APIs with at least 4-decimal precision.
- Mark overrun when completed mandays exceed required mandays.
- Mark delayed when project end date has passed and Job Card is not completed or cancelled.

### 7.8 Document Management

The system shall:

- Allow assigned consultants to upload required and supporting documents.
- Support document types including Case Study, Technology Recommendation, Audit, and Other Supporting Document.
- Validate file type, file size, and base64 content.
- Store file name, MIME type, storage key, version, status, remarks, uploader, and timestamps.
- Version documents by Job Card and document type.
- Mark corrected uploads as resubmissions when prior versions exist.
- Allow authorized document reviewers to see the submitted document request and open the actual uploaded document file.
- Allow authorized document reviewers to preview or download the uploaded document before making a decision.
- Show document metadata to reviewers, including document type, file name, version, upload date, uploader, status, and previous review remarks.
- Allow document-level approval or rejection only after the reviewer has access to the uploaded file.
- Require rejection comments when rejecting a document.
- Notify owner on approval or rejection.
- Prevent document upload, archive, review, or rework on completed Job Cards.

### 7.9 Approval Workflow

The system shall support staged approvals:

1. Consultant confirmation.
2. Unit Representative confirmation.
3. Director final review.

The system shall:

- Enforce stage order.
- Enforce assigned role and assigned responsible person for each stage.
- Require all milestones and required approved documents before consultant confirmation.
- Require rejection reason when a stage is rejected.
- Allow rejected stages to be corrected, resubmitted, and reviewed again in a new approval cycle.
- Generate next-stage approval notifications.
- Generate rejection, rework, final approval, and completion notifications.
- Mark Job Card as completed only after Director final approval.
- Preserve completed status permanently unless Admin reopens through audit workflow.

### 7.10 Notifications

The system shall generate notifications for:

- User activation.
- Assignment to Job Card.
- Visit submission.
- Visit rejection.
- Visit resubmission.
- Document upload.
- Document approval/rejection.
- Milestone due.
- Milestone delay.
- Overrun.
- Approval request.
- Approval rejection/rework.
- Final approval.
- Job Card completion.
- Job Card reopening.

Notifications shall include linked record navigation where possible.

### 7.11 Dashboards

The system shall provide dashboards for:

- Admin operational overview.
- Consultant assigned work overview.
- Management analytics and attention queues.
- Unit Representative approval queue.
- Director approval queue.

Dashboard KPIs shall include:

- Total Job Cards.
- In-progress Job Cards.
- Completed Job Cards.
- Delayed Job Cards.
- Overrun Job Cards.
- Pending visits.
- Pending documents.
- Pending approvals.
- Completed mandays.
- Remaining mandays.
- Milestone progress.

KPI cards shall open relevant filtered records.

### 7.12 Reports and Exports

The system shall provide:

- Job Card report.
- Manday utilization report.
- Milestone status report.
- Visit detail report.
- Document status report.
- Approval status report.
- Delay and overrun report.
- Consultant-wise workload report.

Reports shall support backend-powered filters and export to:

- CSV.
- XLSX.
- PDF.

Exported reports shall use the same filters shown on screen.

### 7.13 Audit Log

The system shall audit:

- Login success/failure.
- Denied login.
- User creation.
- User edit.
- Role change.
- Password reset.
- Account lock/unlock.
- Activation/deactivation.
- MSME creation/edit.
- Job Card creation/edit/reassignment/status change/archive/reopen.
- Milestone creation/edit.
- Visit draft/edit/submit/reject/resubmit.
- Document upload/download/review/archive.
- Approval decisions.
- Report exports where required.

Audit records shall include actor, action, entity type, entity ID, details, timestamp, and old/new values where applicable.

## 8. Page Requirements

### 8.1 Login Page

The login page shall:

- Display D'Mastru branding.
- Offer Admin sign in, Management sign in, and Consultant sign in choices.
- Show editable email and password fields.
- Show Consultant registration link only for Consultant login.
- Show forgot password option.
- Redirect users to their role-specific dashboard.

### 8.2 Admin Workspace

Admin pages shall include:

- Dashboard.
- Users & Roles.
- MSME Master.
- Job Cards.
- Documents & Approvals.
- Audit Log.
- Notifications.
- Settings.

### 8.3 Consultant Workspace

Consultant pages shall include:

- Dashboard.
- My Job Cards.
- Visits.
- Documents.
- Activity.
- Notifications.
- Profile.

### 8.4 Management Workspace

Management pages shall include:

- Dashboard.
- Job Card detail.
- Visit/document review queues.
- Approval queues where role permits.
- Reports.
- Analytics.
- Notifications.

### 8.5 Job Card Detail Page

The Job Card detail page shall show:

- MSME details.
- Assignment details.
- Project dates and location.
- Required mandays/hours.
- Completed and remaining mandays/hours.
- Visit list.
- Milestone summary.
- Documents and versions.
- Approval cycle history.
- Activity history.
- Status and available actions based on role.

## 9. Business Rules

1. A Job Card number must be unique and at least 3 characters.
2. Project end date cannot be earlier than project start date.
3. Required mandays must be greater than zero.
4. Required hours = required mandays x hours per manday.
5. Default hours per manday = 8.
6. Mandays must be calculated from actual visit minutes, not rounded visit count.
7. 6h30m must equal 0.8125 manday.
8. A consultant cannot occupy more than one consultant role on the same Job Card.
9. Job Card assignees must be active.
10. Unit Representative and Director must be assigned before approval workflow completion.
11. Only assigned responsible persons can approve their stage.
12. Management cannot approve both Unit Representative and Director stages.
13. Rejection must include a reason.
14. Rejected work must be correctable and resubmittable.
15. Completed Job Cards are read-only.
16. Completed status can only be changed through Admin reopen with audit reason.
17. Consultant users can only see assigned Job Cards.
18. Inactive, locked, and pending users cannot log in.
19. Admin cannot accidentally remove their own Admin access.
20. Deletions must be soft deletions unless explicitly specified otherwise.

## 10. Non-Functional Requirements

### 10.1 Security

- Token-based authentication.
- Role-based route and API protection.
- Password hashing.
- Account lockout after repeated failed attempts.
- Input validation on frontend and backend.
- File validation for uploads.
- CORS configuration.
- HTTPS-ready deployment configuration.
- Audit trail for sensitive actions.

### 10.2 Performance

- Lists shall support pagination.
- Filters shall be backend-powered.
- Dashboards should load summary data without requiring large client-side calculations.
- Common dashboard and list pages should respond within acceptable business use latency.

### 10.3 Availability and Recovery

- Database backup process shall be documented.
- Restore process shall be documented.
- Docker deployment shall support repeatable setup.
- Environment variables shall be documented.

### 10.4 Usability

- UI shall be responsive for mobile, tablet, and desktop.
- Visit submission must be mobile-first for consultants.
- Errors must be shown inline where possible.
- Buttons and links must navigate to the expected records.
- Protected routes must not show blank pages after logout.

### 10.5 Accessibility

- Forms shall use labels.
- Error states shall be readable by assistive technology where practical.
- Color contrast shall be sufficient.
- Keyboard navigation shall work for core workflows.

## 11. Data Requirements

Primary entities:

- User.
- Session.
- MSME.
- Job Card.
- Assignment.
- Milestone.
- Visit.
- Visit Attachment.
- Document.
- Approval.
- Notification.
- Audit Log.
- Master Data.
- System Setting.

Key relationships:

- One MSME can have many Job Cards.
- One Job Card can have many assignments.
- One Job Card can have many milestones.
- One Job Card can have many visits.
- One Job Card can have many documents.
- One Job Card can have many approval records across cycles.
- One user can receive many notifications and produce many audit records.

## 12. Status Models

### 12.1 User Status

- Pending Activation.
- Active.
- Inactive.
- Locked.

### 12.2 Job Card Status

- Draft/Created.
- In Progress.
- Pending Approval.
- Rework Required.
- Completed.
- Cancelled/Archived, if used.

### 12.3 Visit Status

- Draft.
- Submitted.
- Rejected.
- Resubmitted, if separated.
- Approved, if visit approval is enabled.

### 12.4 Document Status

- Submitted.
- Resubmitted.
- Approved.
- Rejected.
- Archived.

### 12.5 Approval Decision

- Approved.
- Rejected.

## 13. Reporting Filters

Reports and dashboards shall support filters for:

- Date range.
- MSME.
- Consultant.
- Unit Representative.
- Director.
- Job Card status.
- ZED ID.
- Certificate level.
- Delayed.
- Overrun.
- Milestone.
- Document type.
- Approval stage.
- Approval decision.

## 14. Acceptance Criteria

The release shall be accepted when:

1. Admin can create MSME, users, Job Card, assignments, milestones, and settings.
2. Consultant can log in, view assigned Job Card, create visit draft, submit visit, upload documents, and view status.
3. Management can view dashboards, delayed/overrun queues, filters, reports, and exports.
4. Unit Representative can approve or reject only assigned unit-level approvals.
5. Director can approve or reject only assigned final reviews.
6. Rejected visit/document/approval items can be corrected and resubmitted.
7. Completed Job Cards reject new visits, documents, edits, reassignments, and approvals.
8. Admin reopen of completed Job Card requires reason and creates audit record.
9. Manday calculation returns 0.8125 for 6h30m.
10. Consultant A cannot access Consultant B's Job Cards.
11. Failed login, role change, status change, and old/new values are audited.
12. Notification cards navigate to linked records.
13. CSV/XLSX/PDF exports respect active filters.
14. UI works on desktop and mobile.
15. Browser tests cover all major roles and flows.
16. Docker end-to-end run succeeds when Docker daemon is available.

## 15. End-to-End Business Flow

1. Admin signs in.
2. Admin creates or updates MSME.
3. Admin creates users and activates required accounts.
4. Admin creates Job Card with MSME, dates, location, mandays, consultant, Unit Representative, and Director.
5. Admin creates milestones with planned visits and dates.
6. Consultant receives assignment notification.
7. Consultant records visit draft.
8. Consultant submits visit.
9. Reviewer or Management reviews visit and rejects if correction is needed.
10. Consultant corrects and resubmits rejected visit.
11. Consultant uploads required documents.
12. Authorized Admin/Management reviewer opens the submitted document request, views or downloads the actual uploaded file, and reviews the document content.
13. Authorized Admin/Management reviewer approves the document or rejects it with comments.
14. Consultant confirmation is completed once milestones and required documents are approved.
15. Unit Representative receives approval request and approves or rejects.
16. Director receives final review request and approves or rejects.
17. Director final approval marks the Job Card as completed.
18. Completed Job Card becomes locked.
19. Reports and dashboards show final mandays, milestone status, approvals, and completion.

## 16. Risks and Controls

| Risk | Required Control |
| --- | --- |
| Wrong manday calculation | Backend manday engine with decimal precision tests. |
| Unauthorized approvals | Assigned-person and role-based approval checks. |
| Completed Job Card modified later | Completed-card lock and audited reopen only. |
| Consultant sees another consultant's records | Data isolation checks and tests. |
| Rejected items cannot be fixed | Rework and resubmission cycle. |
| Reports differ from dashboard | Shared backend filters. |
| File upload security issues | File type, size, MIME, and content validation. |
| Lost operational data | Backup and restore process. |

## 17. Future Enhancements

- Digital signature integration.
- Email/SMS/WhatsApp notifications.
- Offline visit drafts.
- External ZED portal integration.
- Advanced analytics and forecasting.
- Role-specific mobile app.
- Configurable approval chains.
- Document templates and auto-filled PDF Job Cards.
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    