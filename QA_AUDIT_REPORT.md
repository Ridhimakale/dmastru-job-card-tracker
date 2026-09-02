# Complete Platform QA, Flow & Feature Audit

Audit date: 2026-08-26  
Scope: existing FastAPI backend and React/TypeScript frontend. This was an audit only; no product behaviour was changed.

## Evidence and test method

- Ran the end-to-end backend suite: **13 passed** in 32.17 seconds (`backend/tests/test_auth.py`). It covers seeded Admin, Management and End User accounts; Job Cards; milestones; visits; document review/versioning; ordered approvals; exports; session refresh/reset; audit events; and several authorization boundaries.
- Started the API locally and used HTTP requests as all three roles. Each role logged in, received a 200 response from its dashboard, logged out with 204, and received 401 from `/api/auth/me` afterward.
- Confirmed direct-API denials: Management -> `/api/users` = 403; End User -> `/api/msmes` = 403; End User -> `POST /api/job-cards` = 403.
- Performed a destructive-workflow probe only against the disposable test database: a COMPLETED Job Card accepted a new End User draft and submission, then changed to IN_PROGRESS. This is a confirmed production blocker.
- Built the production frontend bundle successfully. No browser automation framework is installed in this workspace, so visual click-by-click, responsive-device, browser-back, and download UI checks were not executed. Those items are explicitly marked as unverified rather than passed.

## A. Working (verified)

- Authentication: valid login, generic invalid-credential response, pending account denial, registration as End User only, activation, password reset, password change, refresh-token rotation, deactivation, and logout/session invalidation.
- Three role dashboards respond with database-derived values; End User dashboard is scoped to assigned cards.
- Admin can create MSMEs and Job Cards; duplicate Job Card and MSME checks work. Required 14 MD is calculated server-side as 112 hours.
- Consultant assignment, reassignment history, assignment notification, Job Card list paging, Job Card search by number, and soft archival are implemented.
- Draft visits do not add completed minutes, MD, or milestone progress. Submitted visits do. Rejection and resubmission state transitions work.
- Document upload content validation, version increment on resubmission, review, download authorization, and document rejection notification work.
- Ordered approval progression prevents later stages before earlier approval and completes a card when all current backend conditions are met.
- CSV, XLSX, and PDF report endpoints work and are authorized for Admin/Management.
- Audit/activity records exist for the principal tested workflow actions. CORS preflight, baseline rate limiting, and principal security headers are tested.

## B. Failed

| Feature | Role | Reproduction and expected result | Actual result | Severity | Root cause / recommendation |
|---|---|---|---|---|---|
| Completed Job Card immutability | End User | Log in as consultant; open a COMPLETED assigned card; create and submit a visit. Expected rejection and status remains COMPLETED. | Draft creation returned DRAFT; submission returned SUBMITTED; Job Card changed from COMPLETED to IN_PROGRESS. | **Critical** | `save_draft` and `submit_visit` do not reject completed cards. Enforce terminal-status checks on every mutation endpoint and add regression tests. |
| Manday precision | All/reporting | Submit 6h 30m. Requirement expects 0.8125 MD. | API and report values round to 0.81 MD. | **High** | `manday_summary` quantizes to two decimals. Store/return sufficient precision (at least four decimals) and format only in UI. |
| Approval rejection recovery | Consultant/Management | Reject an approval stage, correct work, and resubmit it. Expected a returned/reopened workflow. | A rejection creates an immutable stage record; the same stage cannot be decided again, so completion cannot resume. | **High** | Approval model has no revision/reopen semantics. Add cycle/versioning or reopen the rejected stage and notify the responsible actor. |
| Strict role separation | Admin | Call `GET /api/dashboard/management` with an Admin token. Expected 403 if Management-only. | Returned 200. | **Medium** | Management dashboard permits `ADMIN` in backend role guard. Decide the intended privilege model; restrict if the specification requires Management-only access. |

## C. Missing requirements

- Separate Unit Representative and Director identities/roles. There are only ADMIN, MANAGEMENT, and END_USER; one Management user can make both management approval stages.
- Admin user edit, role-change, and admin password-reset endpoints/UI; there is no user `PATCH` route or role-change action.
- MSME edit and MSME-specific search/filter UI/API.
- Milestone payment date and portal-report submission date fields/workflow.
- Visit editing, visit attachments, and an explicit visit-review queue UI.
- Admin KPI set required by the audit brief: pending documents, milestones due, overdue visits, pending audits, and pending approvals.
- Management summary report, report-filter API, and export-respects-filter behaviour.
- Notification deep linking: backend stores a link but all role UIs only allow “Mark read.”
- Failed-login audit events, role-change audit events, old/new value tracking, and audit filtering/export.
- Project-delay, milestone-due, manday-overrun, final-approval, and completion notifications.
- Automated browser/UI, responsive/mobile, network-failure, and database-integrity QA coverage.

## D. Partially implemented

- Management dashboard charts are data-driven, but KPI cards do not navigate to filtered records and the Projects status filter is browser-only; it filters already-loaded dashboard data rather than querying the backend.
- Management reports UI exposes only CSV and Excel despite a working backend PDF endpoint; it has no report filters.
- Admin Job Card UI creates only a lead consultant even though the backend supports co-consultant and subject-expert IDs. Its management drawer shows documents/approvals but does not provide full reassignment, review, or edit workflows.
- End User Job Card detail omits MSME, assignment roster, required/completed/remaining manday summary, current milestone, approval status detail, document download, rejected-visit resubmission, and document version history controls.
- End User “Visits” and “Documents” navigation routes simply render My Job Cards; they are not dedicated operational lists.
- The settings screen saves an hours-per-manday setting, but calculation code always uses the fixed 480-minute constant; the setting is not connected to calculations.
- The backend accepts and records a document reviewer comment for approval/rejection, but the UI has no dedicated document-review controls for Management.

## E. Security and authorization findings

- Confirmed protected API denials for the tested End User/Management attempts and confirmed logout invalidates the server session.
- **Medium:** Admin can access the Management dashboard directly, contrary to strict route separation in the audit brief.
- **High:** completed records are mutable by the assigned consultant (see failed table).
- **Medium:** rate limiting is process-local, so it does not protect a multi-worker/container deployment consistently. Use Redis/proxy/WAF enforcement for production.
- **Medium:** upload validation checks declared MIME/extension/signatures, but there is no malware scanning or object-storage isolation.
- Data isolation for two independently assigned consultants was not automated in this audit; endpoint guards are present but require a dedicated regression test before release.

## F. Calculation findings

- Required hours calculation passed: 14 MD -> 112 hours.
- Draft handling passed: draft visits do not count.
- Submitted visit aggregation passed in the tested flow: 6h30m -> 390 minutes, but MD is returned as **0.81** rather than required **0.8125**.
- Milestone 1 of 2 submitted visits returned 50%, as expected.
- Remaining MD/hours, overrun display, delayed dashboard propagation, and multi-visit 8h/4h/6h30m precision were not comprehensively executed; current two-decimal rounding makes the stated precision requirement fail already.

## G. UI/UX findings

- Several required screens/actions are missing or partial as described above.
- Error feedback frequently uses `alert()` rather than durable inline error/success states.
- No verified loading, disabled-submit, retry, offline/backend-unavailable, browser-back, or responsive mobile test evidence.
- No browser automation was available in the workspace, therefore every-page button/link audit remains unverified rather than passed.

## H. Final status

**NOT READY FOR PRODUCTION**

The completed-card mutation is a critical workflow/data-integrity defect. Approval rejection cannot reliably re-enter the workflow, the required manday precision is lost, and multiple required role/workflow/UI capabilities are missing or only partial. Fix and regression-test the Critical and High findings before a production release.
