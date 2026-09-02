# Deployment and recovery guide

## Render deployment

This repository includes `render.yaml` for a Render PostgreSQL database, FastAPI web service, and React static site. It is configured for a free-tier client trial: uploads are stored on the API's temporary filesystem and can be lost when Render restarts, redeploys, or replaces the service. Use only test documents during the trial; switch to a paid API plan with a persistent `/data` disk before production.

1. Push this project to a GitHub repository (do not commit `.env`, databases, or uploaded files).
2. In Render, select **New +** → **Blueprint**, connect the repository, and accept `render.yaml`.
3. Enter `VITE_API_URL` as the public API URL, for example `https://dmastru-jobcard-api.onrender.com`. Render builds this value into the frontend, so redeploy the static site whenever it changes.
4. Once Render shows the static-site URL, set the API's `CORS_ORIGINS` environment variable to that exact URL (no trailing slash), then redeploy the API.
5. Open `https://<api-service>.onrender.com/health`, then test all three sign-ins, a document upload/download, and a report export.

Render automatically provides the Postgres connection string and `PORT`; the application converts Render's PostgreSQL URL to the installed psycopg driver format. Keep the generated `JWT_SECRET` unchanged after the first deployment or all existing sessions will be invalidated.

## Production prerequisites

- PostgreSQL 16 with automated encrypted backups.
- A reverse proxy terminating TLS (for example Nginx, Azure Application Gateway, or AWS ALB).
- A long random `JWT_SECRET`; never use the Compose sample secret.
- Set `APP_ENV=production`, `HTTPS_ENABLED=true`, and `CORS_ORIGINS` to exact deployed frontend origins.
- Persistent object storage for uploads. The local Docker volume is only suitable for development.

## Launch

Copy `.env.production.example` to a protected `.env` file beside `docker-compose.yml`, replacing every placeholder with a secret-store value. Compose refuses to start when either `POSTGRES_PASSWORD` or `JWT_SECRET` is absent. Configure the same CORS, HTTPS, and rate-limit values in that file. Start the stack with:

```powershell
docker-compose up --build -d
```

Verify `http://localhost:8000/health` through the internal network/proxy. Run migrations before any rolling deployment; the current image performs `python -m alembic upgrade head` before Uvicorn starts.

Build the static frontend with `npm ci` followed by `npm run build`, then serve `frontend/dist` from the same TLS proxy. Set its `VITE_API_URL` to the public API origin before building. Do not expose Uvicorn directly to the internet.

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
