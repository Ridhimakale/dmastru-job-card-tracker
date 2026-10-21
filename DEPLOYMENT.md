# Deployment and recovery guide

## Render deployment

`render.yaml` defines a free Render Postgres database, a free Docker API service, and a free static frontend. This is suitable for a demo or trial, not for storing business records long term: free Postgres expires after 30 days and has a 1 GB limit; free web-service files are ephemeral, so uploaded attachments disappear when the API restarts, redeploys, or spins down. The free API also sleeps after inactivity and takes time to wake. Upgrade to paid persistent storage and database before using real client data.

1. Push the repository to GitHub (never commit `.env`, database files, uploads, or secrets), then in Render choose **New + → Blueprint** and connect the repository.
2. When Render requests Blueprint values, provide `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_NAME`, and a strong `INITIAL_ADMIN_PASSWORD` (8+ chars with uppercase, lowercase, number, and special character). The API creates the first Admin on startup because Render Free has no shell access. After confirming Admin login, remove all three `INITIAL_ADMIN_*` variables from the API's Render environment and redeploy. The password is stored as an Argon2 hash in Postgres.
3. Set `FIELD_ENCRYPTION_KEY` to a Fernet key generated with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable: changing it makes previously stored Google OAuth tokens unreadable.
4. After both services have URLs, set the frontend's `VITE_API_URL` to the API URL (for example `https://dmastru-jobcard-api.onrender.com`). Set the API's `CORS_ORIGINS` and `PUBLIC_APP_URL` to the exact frontend URL (no trailing slash). Redeploy the frontend after changing `VITE_API_URL` and the API after changing its settings.
5. Optional integrations need their own credentials: set `GROQ_API_KEY` for AI report drafts; set the Google OAuth client ID, secret, and callback URI for Google Docs/Sheets (`https://<API-host>/api/google-workspace/callback`). Enable the Google Drive and Docs APIs in the Google Cloud project and add that callback URL to the OAuth client's authorized redirect URIs. Set SMTP only if the mail provider supports a port Render allows; free web services cannot send outbound traffic on SMTP ports 25, 465, or 587.
6. Sign in as Admin and create the Management, Unit Representative, consultant, Finance, and other staff accounts. A consultant connects their Google account from the Job Card document area. Confirm `/health` returns success, sign-in works, then try the enabled integrations. File uploads are temporary on the free plan.

Render supplies the Postgres connection URL and `PORT`; the API normalizes the connection URL for psycopg and binds to Render's assigned port. Keep the generated `JWT_SECRET` unchanged after first deploy or existing sessions will be invalidated. Render's free Postgres is limited to one active free database per workspace and is deleted after its expiry window; export any demo data before expiry.
## Production prerequisites

- PostgreSQL 16 with automated encrypted backups.
- A reverse proxy terminating TLS (for example Nginx, Azure Application Gateway, or AWS ALB).
- A long random `JWT_SECRET`; never use the Compose sample secret.
- Set `APP_ENV=production`, `HTTPS_ENABLED=true`, and `CORS_ORIGINS` to exact deployed frontend origins.
- Set `MFA_REQUIRED=true` when ready to require authenticator OTP after password verification. Existing authenticator enrollment is retained.
- Set `PUBLIC_APP_URL` and SMTP connection values (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_SSL`) to enable password-reset links by email.
- Persistent storage for uploads. Docker Compose uses a named `uploads_data` volume. The free Render setup uses ephemeral uploads; use a paid persistent disk or object storage before storing real client documents.
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
