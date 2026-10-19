# Deployment and recovery guide

## Render deployment

This repository includes `render.yaml` for a Render PostgreSQL database, FastAPI web service, and React static site. The API blueprint is configured with a paid Starter service and a persistent 10 GB disk mounted at `/data`; Postgres is configured on the paid `0.1c-256mb` compute plan with 15 GB of storage. These resource choices are required for persistent uploads/database storage and may incur provider charges when the Blueprint is created or synced. Render disks are local to a single service instance; use object storage if you later need multiple API instances or shared file storage.

1. Push this project to a GitHub repository (do not commit `.env`, databases, or uploaded files). The Render resources in this blueprint use paid plans and can incur charges.
2. In Render, select **New +** → **Blueprint**, connect the repository, and accept `render.yaml`.
3. Enter `VITE_API_URL` as the public API URL, for example `https://dmastru-jobcard-api.onrender.com`. Render builds this value into the frontend, so redeploy the static site whenever it changes.
4. Once Render shows the static-site URL, set the API's `CORS_ORIGINS` and `PUBLIC_APP_URL` environment variables to that exact URL (no trailing slash), then redeploy the API.
5. Open the API service shell and provision the first administrator with `python -m app.create_admin --email admin@your-domain.com --name "D'Mastru Admin"`. Enter a unique strong password at the prompt; it is not stored in deployment configuration.
6. Configure the Google OAuth settings described below, then sign in as that administrator and create the Management, Unit Representative, Director, and consultant accounts needed for the team. A consultant connects their own Google account from the Job Card document area. Verify login, a document upload/download, and a report export.

Render automatically provides the Postgres connection string and `PORT`; the application converts Render's PostgreSQL URL to the installed psycopg driver format. Keep the generated `JWT_SECRET` unchanged after the first deployment or all existing sessions will be invalidated.

## Production prerequisites

- PostgreSQL 16 with automated encrypted backups.
- A reverse proxy terminating TLS (for example Nginx, Azure Application Gateway, or AWS ALB).
- A long random `JWT_SECRET`; never use the Compose sample secret.
- Set `APP_ENV=production`, `HTTPS_ENABLED=true`, and `CORS_ORIGINS` to exact deployed frontend origins.
- MFA_REQUIRED=false temporarily skips authenticator OTP after password verification; set it to 	rue when ready to require OTP again. Existing authenticator enrollment is retained.
- Set `PUBLIC_APP_URL` and SMTP connection values (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_SSL`) to enable password-reset links by email.
- Persistent storage for uploads. The Render blueprint mounts a persistent disk at `/data`; Docker Compose uses a named `uploads_data` volume. Use shared object storage instead when scaling the API beyond one instance.
## Google Docs and Sheets integration

The app creates Google Docs/Sheets files and opens Google's editor. Consultants connect a Google Account using the same email address as their portal account. The email provider does not need to be Google Workspace; a company email can be registered as a Google Account without creating a Gmail inbox. The app requests access only to files it creates (`drive.file`) and the account email. Google saves the live working file in Drive; while the Job Card workspace is open, the portal syncs a DOCX/XLSX copy every minute and always syncs again immediately before submission. Admin reviews the portal copy using the existing approval flow. Google OAuth client settings and the existing `FIELD_ENCRYPTION_KEY` are required; refresh tokens are encrypted in the application database.

1. In Google Cloud Console, create/select a project and enable the **Google Drive API**.
2. Configure the OAuth consent screen for the organization's intended user type and add the team's test users if the app remains in testing mode.
3. Create an **OAuth client ID** of type **Web application**. Add the exact callback URI as an authorized redirect URI. For local Vite development use `http://localhost:5173/api/google-workspace/callback`; for deployment use the API's public URL followed by `/api/google-workspace/callback`.
4. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REDIRECT_URI` in the API environment. Keep the client secret in the deployment secret store; do not commit it to source control. Redeploy/restart the API.
5. Apply migrations (`python -m alembic upgrade head`). The Docker image applies migrations when it starts.
6. Open a Job Card as a consultant, select **Create document** or **Spreadsheet**, connect the Google account, create the file, and edit it in Google. Keep the Job Card tab open for periodic portal sync, or use **Sync and submit for approval** to force the latest copy into the portal.

Before first use, each consultant must register their existing `@dmastru.com` email as a Google Account (without adding Gmail) if it is not already a Google Account. Use [Google's signup without Gmail page](https://accounts.google.com/signupwithoutgmail). The portal verifies that the connected Google Account email matches the user's portal email; Google Account credentials remain separate from the company's email password.

The portal's synchronized copy is a periodic snapshot, not a live Google editor replica. Submitting always requests a fresh export from Google before placing that version in Admin's review queue.

For local PowerShell development, set the same three variables in the terminal that starts Uvicorn. Use `http://localhost:5173/api/google-workspace/callback` as the redirect URI registered in Google Cloud and configured in `GOOGLE_OAUTH_REDIRECT_URI`. Do not save the client secret in a committed file.

## Launch

Copy `.env.production.example` to a protected `.env` file beside `docker-compose.yml`, replacing every placeholder with a secret-store value. Compose refuses to start when either `POSTGRES_PASSWORD` or `JWT_SECRET` is absent. Configure the same CORS, HTTPS, and rate-limit values in that file. Start the stack with:

```powershell
docker-compose up --build -d
```

Verify `http://localhost:8000/health` through the internal network/proxy. Run migrations before any rolling deployment; the current image performs `python -m alembic upgrade head` before Uvicorn starts.

The container does not seed predictable demo accounts. After the first start, create the initial Admin from the trusted host shell:

```powershell
docker-compose exec backend python -m app.create_admin --email admin@your-domain.com --name "D'Mastru Admin"
```

Enter the password when prompted. This command only works while no active Admin exists; use Admin user management for later account creation.

Build the static frontend with `npm ci` followed by `npm run build`, then serve `frontend/dist` from the same TLS proxy. Copy `frontend/.env.example` to `frontend/.env.production`, replace `VITE_API_URL` with the public API origin, and build. On Render, set the static service's `VITE_API_URL` to the same API origin and redeploy. Do not expose Uvicorn directly to the internet.

## Backup and recovery

Schedule `scripts/backup-postgres.ps1` at least daily and retain both the `.dump` and its SHA-256 sidecar. Back up the upload object store/versioned volume on the same schedule.

For a recovery exercise: restore into an isolated database using `scripts/restore-postgres.ps1`, verify the checksum, run migrations, then validate `/health`, login, Job Card totals, document access, and report export before redirecting traffic. Test recovery at least quarterly.

## Security and operations

- Rate limits are in-process baseline protection. Use proxy/WAF or Redis-backed rate limiting for multi-worker production deployments.
- File uploads validate extension, declared MIME type, size, and file signatures/OOXML structure. Add malware scanning before production document access.
- The app sets security headers; TLS/HSTS must be enabled only behind HTTPS.
- Monitor 5xx responses, login lockouts, backup jobs, disk/object-storage usage, and database connection saturation.

## Release verification

Before a release, run the backend test suite and frontend production build, then run `scripts/smoke-load.ps1` against a staging deployment. Execute an authenticated acceptance pass for each role (Admin, Management, and End User), including upload validation, visit submission/rejection/resubmission, approval sequencing, PDF/XLSX/CSV export, and document download authorization.
