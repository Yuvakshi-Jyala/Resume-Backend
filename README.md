# Resume Screening — Backend (FastAPI)

FastAPI backend for the CogitX Resume Screening ATS. Talks to CogitX workflows
and MongoDB Atlas; the frontend is a separate repo.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values
python -m uvicorn main:app --reload --port 8000
```

## Deploy to Azure App Service (Linux, Python)
1. Create an **App Service** → Runtime stack: **Python 3.11**, OS: **Linux**.
2. Deploy this repo (Deployment Center → GitHub → this repo/branch; Azure
   generates the GitHub Actions workflow for you).
3. **Configuration → General settings → Startup Command:**
   ```
   python -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```
   (same as `startup.txt`)
4. **Configuration → Application settings** — add every variable from
   `.env.example` (MONGODB_URI, all COGITX_*, FRONTEND_ORIGINS, …).
   Set `FRONTEND_ORIGINS` to your deployed frontend URL.
5. Save & restart.

Notes: Azure's Oryx build runs `pip install -r requirements.txt` automatically.
App listens on port 8000 (Azure's default for Python).
