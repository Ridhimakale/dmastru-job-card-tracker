# D'Mastru Job Card – Visit & Manday Tracker

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

## Verification

```powershell
cd backend
python -m pytest tests/test_auth.py -q
cd ..\frontend
node .\node_modules\typescript\bin\tsc -b
node .\node_modules\vite\bin\vite.js build
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for Docker, TLS, backups, recovery, and production-operation guidance.
