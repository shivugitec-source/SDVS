# SDVS deployment

## Current hosting layout

- Frontend: GitHub Pages, published from `docs/` by `.github/workflows/pages.yml`.
- API: Render web service using the Dockerfile in `backend/`.
- Database: external PostgreSQL (Neon is supported through `DATABASE_URL`).
- PDF storage: **not production-ready yet**. The current API writes files to local disk. Render's free filesystem is ephemeral; do not upload real examination scripts until durable private object storage is implemented.

## Deploy the API on Render

1. Sign in to Render with the project owner's account and choose **New → Blueprint**.
2. Connect the public repository `shivugitec-source/SDVS` and select the `main` branch. Render will read `render.yaml`.
3. Create a PostgreSQL database on Neon and copy its pooled or direct connection string into Render's `DATABASE_URL` environment variable. Ensure the URL uses the driver supported by the installed `psycopg` package (for example, `postgresql+psycopg://...`).
4. Set the private environment values requested by the Blueprint:
   - `BOOTSTRAP_PASSWORD`: a unique, strong random password (never commit it).
   - `BOOTSTRAP_EMAIL`: the Master Admin email.
   - `BOOTSTRAP_PHONE`: the Master Admin mobile in international format.
   - `SMTP_PASSWORD`: Gmail App Password, not the normal Gmail password.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`: Twilio credentials and verified sender.
5. Deploy and verify `https://<your-render-service>.onrender.com/health` returns JSON with status `ok`.
6. Open the GitHub Pages Master Admin page, enter the Render API URL, then request email and mobile OTPs.

## Required security work before real examination data

- Implement durable private object storage (e.g. Cloudflare R2) and signed/authorized PDF delivery.
- Replace `Base.metadata.create_all` with versioned database migrations.
- Add stronger OTP lifecycle controls, audit coverage, backups, and integration tests.
- Keep all secrets in Render's environment settings, never in this public repository.
- Configure the Master Admin profile using private environment values only.

The free Render service may sleep when idle. Email delivery needs a Gmail App Password; SMS delivery requires a configured provider and may incur charges.
