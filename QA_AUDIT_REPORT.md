# Current QA and Acceptance Status

**Reviewed:** 2026-10-10

**Product reference:** updated `Pasted text.txt` (requirements sections 1-55)

**Scope:** FastAPI backend, React/TypeScript frontend, database migrations, and deployment documentation.

## Module 1 review — 2026-10-12

- Added the four primary login choices: Marketing / Finance, Admin, Consultant, and Management. Marketing and Admin map to their current backend roles; consultant maps to End User; Management also admits the existing Unit Representative and Director approval roles.
- Removed prefilled demo login emails, fabricated login statistics, and the nonfunctional “Keep me signed in” control. The selected portal is checked against the authenticated role; a mismatch triggers session revocation before showing an error. Restored sessions refresh the displayed role from the authenticated backend response.
- Updated role-specific navigation and added guarded planner/report routes. Backend report access now permits Marketing / Finance to read/export payment reports only. Existing supporting routes remain available to their authorized roles.
- Reused the site's responsive layout, mobile table/card treatment, search and status patterns. Added explicit dashboard loading feedback for Admin; existing marketing/search, empty, and error states remain.
- Module 1 checks: TypeScript build and Vite production build passed; backend suite **18 passed**; Python compilation and `git diff --check` passed. API tests cover allowed dashboards, denied cross-role dashboards, and Marketing payment-report scope.
- Remaining screen-depth handoffs: Marketing Job Cards currently lead to the existing combined payment workspace; Admin milestone/allocation links lead to Job Card selection; planner links currently show visit lists rather than consolidated calendars. Those destination workflows are assigned to the relevant later modules. Browser-based click-through was not run in this check.

## Module 2 review — 2026-10-12

- Enquiries already had unique generated numbers, backend create/update/list/search, MSME/contact fields, source, package/scope, assignment validation, history via audit records, duplicate checks, and conversion/linking to the Admin-owned Job Card workflow. Added indicative commercial value with a production Alembic migration, exposed the stored commercial/client/source/package/status/date fields in the Marketing form, added active Marketing assignee selection, and added an edit form for existing enquiries.
- Follow-ups already recorded call/meeting/email/client discussion notes, contact person, next date, responsible user, optional attachments, and immutable history. Added a searchable, paginated upcoming/overdue schedule API and a dedicated Follow-ups navigation view.
- Payments already supported invoices, partial/full receipts, external references, confirmation user/date/amount/remarks, cancellation, duplicate-reference protection, audit events, initial activation gating, per-milestone gates, and payment evidence via versioned Payment record documents. Added confirmation time entry to the receipt UI; payment confirmation continues to unlock eligible work without completing it.
- Expanded the Marketing dashboard with total/new/converted enquiries, upcoming/overdue follow-ups, awaiting initial payment, pending M1/M2/M3 confirmations, confirmed/overdue payments, outstanding balance, and recent receipt activity.
- Module 2 verification: backend suite **18 passed**; backend compilation, TypeScript build, Vite production build, and `git diff --check` passed. Acceptance coverage now checks commercial enquiry create/update, follow-up queue, dashboard metrics, recent receipts, payment confirmation, and that M1 payment does not mark its work complete. No browser click-through or production PostgreSQL migration run was performed.
- No known functional gaps remain within the listed Module 2 scope. Manual browser walkthrough and production PostgreSQL migration validation remain deployment verification steps.

## Module 3 review — 2026-10-12

- MSME Master already provided Admin CRUD, unique code/name checks, search and reusable MSME references. Added email, phone, industry and unit name to the model, API, Admin form and searchable fields; retained certificate, ZED, address, city/state and contact information.
- User lifecycle already supported create/edit, roles, departments, activation/deactivation, unlock and hashed password reset. Added email editing with duplicate checks; API responses continue to exclude password/hash fields.
- Job Cards already generated identifiers, linked finalized enquiries/tickets, captured project/package/client/date/consultant details, created package milestones, and enforced payment gates. Restricted creation to Admin, exposed ticket conversion and remarks, removed the editable generated-number control, and retained optional consultant roles.
- Milestones now expose M1/M2/M3 configuration for scope, dates, visits, mandays, payment amount/percentage, activities/deliverables and assigned responsible consultant. Duplicate Job Card/milestone pairs are also database constrained. Existing package snapshots remain supported.
- Consultant reassignments now require a reason, record allocation start/end dates and preserve historical assignments; Admin can inspect active and ended assignments. Admin dashboard includes requested MSME/Job Card/payment/review/action counts, consultant workload and upcoming visits.
- Document review supports approve, return for correction and reject with a comment. Reviewer identity/date are stored, version records and audit decisions remain available, and Admin review covers both Job Card and milestone related-record files. Payment confirmations remain independent of document/milestone approvals.
- Module 3 acceptance coverage: MSME details, admin-only Job Card creation, generated identifiers, milestone data/uniqueness, allocation history/dates/reason, document return metadata, and dashboard workload/KPIs. Backend suite **18 passed**; TypeScript and Vite production builds passed; Python compilation, SQLite Alembic upgrade through `20261012_27`, and `git diff --check` passed. Browser walkthrough and production PostgreSQL validation remain deployment checks.
- No obsolete Module 3 implementation was found that could safely be removed without dropping an active workflow.

## Delivery status

The core Job Card -> Visit -> Manday workflow and the requested supporting role workflows are implemented. The repository contains an active React/Vite frontend and FastAPI backend, SQLAlchemy models, Alembic migrations, development seed data, deployment configuration, and setup guides.

### Completed modules

1. **Foundation and access:** authentication, sessions, account lifecycle, Admin/Management/Unit Representative/Director/End User roles, authorization, notifications, and audit records.
2. **Job Card operations:** MSME and Job Card workflows, assignments, per-card hours per manday, milestone schedules, consultant-facing details, visit drafts/submissions/corrections, and document upload/version/review flows.
3. **Management and delivery:** role-scoped dashboards, approvals, paginated review queues, filtered/paginated reports and exports, due-date and visit-team reporting, migrations, seed/deployment guidance, and QA coverage.
4. **Business workflow extensions:** CRM tickets/enquiries and follow-ups, payment gates, configurable packages and workflow triggers, action queues/transfers/attachments, project closure/certificates, global search, configurable consultant activities with secured files, and role-specific dashboard summaries.

These module labels group the delivered work for reporting; they do not represent independently deployable services.

## Verification completed

- Backend: `python -m pytest tests/test_auth.py -q` - **18 passed**.
- Frontend: TypeScript project build - **passed**.
- Frontend: TypeScript project build - **passed**; Vite production build - **passed** (387.98 kB JavaScript; 106.06 kB gzip; 24.63 kB CSS).
- Frontend dependencies: `npm audit` - **0 vulnerabilities**.
- Backend Python compilation: `python -m compileall -q app tests` - **passed**.
- Database: migrations through `20261012_27` - **passed** on SQLite, including the visit review/details, record documents, Admin fields and allocation dates.
- `git diff --check` — **passed**; Git reported only line-ending normalization warnings.
- Browser smoke walkthrough: Admin, Management, Consultant, Unit Representative, and Director sign-ins and available navigation routes - **passed**.
- Browser interaction: consultant document list/download and browser-back from Documents to Visits - **passed**.
- Responsive check: 390px login controls fit in viewport; Management dashboard document width fits a 1418px desktop viewport - **passed**.
- Production configuration: Render Blueprint YAML parses with paid PostgreSQL 16 storage and a persistent upload disk; Docker Compose includes persistent database/upload volumes; PostgreSQL, SMTP, CORS, public URL, and upload-path settings are defined - **configuration present**.

The automated suite covers authentication and authorization, Job Cards and assignments, visit state/validation and review history, manday and dashboard summaries, milestone submission and the Management -> Unit Representative review cycle (including return and resubmission), document versions, reports/pagination, payment-linked record attachments, and key workflow boundaries. Timed visit fields and the unified workspace attachment feed are asserted. An end-to-end acceptance test exercises the milestone cycle before final Job Card approval. Production PostgreSQL was not exercised in this run.

## Remaining acceptance work

- **Full interactive UI sign-off:** the browser smoke checks cover login, role navigation, the document download, browser back, and key viewport widths. A full guided review of every form and the Admin -> consultant -> Management workflow in the UI remains a product sign-off item; the equivalent business flow passes end-to-end through the API test.
- **Live production environment validation:** deploy with hosting-account values for the Render API URL, frontend URL, CORS origin, SMTP credentials, and secrets; then verify live PostgreSQL connectivity, email delivery, and upload persistence across a redeploy. Configuration is present, but no hosting account or production credentials are connected to this workspace, so this external validation cannot be completed here.

No known failing automated or build check remains from the verification listed above. Production deployment and live provider integration are still pending environment access.

## Updated requirement gap closure

- Added a unified Job Card workspace linked from Job Card search and review queues. It presents the source Ticket/Enquiry, client, team, milestones and review history, workflow actions, activities, visits, mandays, payments, documents, approvals, closure, and audit history.
- Added visit schedule times, location and findings, with reviewer approve/return decisions and immutable decision history across resubmission cycles.
- Added versioned, reviewable attachments for tickets, enquiries, Job Cards, milestones, actions, visits, payments, milestone/visit review records, and approvals. The Job Card workspace lists and downloads related attachments.
- Added Tailwind CSS 4 with prefixed utilities and no Preflight reset to preserve the existing visual system; applied utilities to the responsive workspace navigation.
- Expanded development seed data with an awaiting-payment case and a completed business chain with linked CRM records, paid milestones, review decisions, activities, approvals, and closure certificate.
- Rebuilt and ran the available acceptance suite: **18 passed**. SQLite migration upgrade through `20261010_23`, Python compilation, TypeScript compilation, Vite production build, and `git diff --check` passed.

## Known operational limitations

- Render's production blueprint provisions paid resources: a Starter API service, 10 GB persistent upload disk, and PostgreSQL 16 with 15 GB storage. Confirm pricing in the provider account before syncing the blueprint.
- Production deployment requires operator-provided secrets and service configuration; development seed credentials must not be used in production. SMTP and the public frontend/API URLs need real deployment values.
- Malware scanning and shared/distributed rate limiting are not included; add these if required by the deployment threat model and scale.
- This MVP intentionally excludes the out-of-scope integrations listed in the requirements (SMS/WhatsApp, payments, ERP, AI, public customer portal, and native mobile app).

## Run and deploy

Follow [README.md](README.md) for local setup and [DEPLOYMENT.md](DEPLOYMENT.md) for environment configuration, Docker/Render deployment, migrations, backups, and operations. Development demo credentials are documented in the README and are for local use only.

## Module 4 review — 2026-10-12

- Consultant access continues to be enforced against active Job Card assignments and payment gates. Consultant dashboard KPIs now include active milestones, today's and overdue tasks, planned visits, pending and returned documents, and planned/actual/remaining mandays.
- Added a separate planned-visit planner with Job Card and milestone links, date/time/duration/location/objective, list and calendar views, date and record filters, overlap detection with explicit confirmation, rescheduling/cancellation reasons, actual visit details/outcomes, audit entries, and notifications. Admin reassignment updates future planned visits and notifies affected consultants.
- Planned visit actual duration contributes to mandays using each Job Card's configured hours per manday. A same-day submitted visit report for the same consultant prevents duplicate counting.
- Consultant document submissions support Draft and Submitted. Admin/Management can mark documents Under Review, approve, return, or reject with remarks; consultants can submit drafts and upload revised versions after return. Version history stays visible in the Job Card workspace.
- Added Alembic migration `20261012_28` for planned consultant visits. Backend acceptance suite **19 passed**; TypeScript, Vite production build, Python compilation, SQLite migration chain through `20261012_28`, and `git diff --check` passed. Browser walkthrough and production PostgreSQL validation remain deployment checks.
## Module 5 review � 2026-10-12

- Expanded the read-only Management dashboard with enquiry conversion, pending payment and task KPIs, visit totals, M1/M2/M3 progress, consultant workload/utilization, project progress by MSME, and attention views.
- Extended Management reports with pending tasks and cross-report filters for date, consultant, MSME, Job Card, milestone, and project status. Existing CSV, Excel, and PDF exports use the same filters.
- Consultant utilization now includes submitted/reviewed visits, completed standalone activities, and completed planner visits while excluding planner visits already represented by a submitted visit report. The Management payment KPI links to the read-only payment report.
- Added acceptance assertions for dashboard rollups, task reporting, and report filters. Backend suite: **19 passed**. TypeScript build, Vite production build, Python compilation, and `git diff --check` passed. Standard `npm run build` remains affected by Windows parsing the workspace's en dash; direct local TypeScript and Vite commands succeeded.
- No Module 5 database schema changes were needed. Production PostgreSQL and live provider integrations remain deployment validation items.

## Module 6 review � 2026-10-12

- New Ticket, Enquiry, and Job Card numbers now use `DMTKT-YYYY-XXXXX`, `DMENQ-YYYY-XXXXX`, and `DMJC-YYYY-XXXXX`. Existing stored identifiers are preserved. The existing atomic sequence allocator remains concurrency-safe on PostgreSQL and SQLite.
- Added ORM and database guards against changing generated numbers. Added migration `20261012_29` with additive notification-deduplication and UUID audit-reference fields; existing records are preserved.
- Ticket and Enquiry audit entries now retain their UUID record reference. In-app notification reads create idempotent upcoming and overdue task reminders for assigned workflow actions and activities.
- Added regression coverage for immutable Job Card numbers, required number prefixes, searchable Enquiry audit references, and one-time overdue/upcoming task notifications. Backend suite: **19 passed**. Python compilation, TypeScript build, Vite production build, and SQLite migration upgrade through `20261012_29` passed.
- PostgreSQL trigger syntax is implemented but live PostgreSQL remains an environment validation item. No existing identifiers or production records were renamed or dropped.
