# D'Mastru Job Card – Visit & Manday Tracker

Role-based web application for MSME Job Cards, consultant visits, mandays, milestones, documents, reviews, approvals, dashboards, and operational reports.

## Stack and structure

- Frontend: React, TypeScript, Vite, React Router.
- Backend: FastAPI, Pydantic, SQLAlchemy.
- Database: PostgreSQL in deployment; SQLite is supported for local development.
- `frontend/src`: role-specific pages, shared components, API client, and layouts.
- `backend/app`: API, data models, services, authentication, reporting, and development seed data.
- `backend/alembic`: versioned database migrations.
- `docs`: product requirements and deployment guidance.

## Local setup

Backend (Python 3.12+):

```powershell
cd backend
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m app.seed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend (Node 22+):

```powershell
cd frontend
npm install
node .\node_modules\vite\bin\vite.js
```

Open `http://127.0.0.1:5173`.

Development accounts: `admin@dmastru.local` / `Admin@123`, `management@dmastru.local` / `Manage@123`, and `consultant@dmastru.local` / `Consult@123`.
Additional local review accounts: `unit.rep@dmastru.local` / `UnitRep@123` and `director@dmastru.local` / `Director@123`. Development seeding creates a sample MSME, Job Card, assignments, milestones, and visits. Never use these credentials in a deployed environment.

## Database and migrations

Configure `DATABASE_URL` for PostgreSQL, or leave it unset for the local SQLite database. Apply schema changes with `python -m alembic upgrade head`. Development startup seeds demo users and workflow data; deployed environments do not create demo accounts.

## Build and deployment

Build the frontend with `npm ci && npm run build`. Build and deploy the backend from `backend/`; its container applies Alembic migrations before starting FastAPI. Configure `DATABASE_URL`, `JWT_SECRET`, `APP_ENV=production`, `CORS_ORIGINS`, and persistent upload storage as described in [DEPLOYMENT.md](DEPLOYMENT.md).

## Verification

```powershell
cd backend
python -m pytest tests/test_auth.py -q
cd ..\frontend
node .\node_modules\typescript\bin\tsc -b
node .\node_modules\vite\bin\vite.js build
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for Docker, TLS, backups, recovery, and production-operation guidance.
