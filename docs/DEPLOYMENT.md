# SDVS deployment

## Hosting architecture

- Frontend: GitHub Pages, published from `docs/`.
- API: Render Docker web service defined by `render.yaml`.
- Database: Neon PostgreSQL through SQLAlchemy's psycopg 3 driver.
- Answer-script PDFs: private Cloudflare R2 bucket through its S3-compatible API. The bucket must remain private; do not enable public access.

## 1. Create the Neon database

1. Open the Neon console and create a PostgreSQL project.
2. Choose a region reasonably close to the Render Singapore service.
3. From the project's Connect panel, copy the connection string.
4. In Render, set `DATABASE_URL` to that connection string. Both `postgres://` and `postgresql://` are normalized by the application to the installed `postgresql+psycopg://` driver.
5. Keep the connection string private. Never commit it to GitHub or paste it into chat.

## 2. Create the private Cloudflare R2 bucket

1. In Cloudflare, open **Storage & databases → R2 Object Storage**.
2. Create a bucket, for example `sdvs-private-answer-scripts`. Keep public access disabled.
3. Under R2 API Tokens, create credentials with **Object Read & Write** permission scoped only to this bucket.
4. Copy the S3 endpoint URL shown by Cloudflare. It has the form `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
5. In the Render service environment, set:
   - `REQUIRE_R2=true`
   - `R2_ENDPOINT_URL` = the Cloudflare S3 endpoint
   - `R2_ACCESS_KEY_ID` = Access Key ID
   - `R2_SECRET_ACCESS_KEY` = Secret Access Key
   - `R2_BUCKET_NAME` = exact bucket name

The API writes PDFs to R2 and streams them only after checking the evaluator's authenticated allocation. No public object URL is generated. Cloudflare R2 uses its S3-compatible API; see the official [R2 S3 documentation](https://developers.cloudflare.com/r2/get-started/s3/).

## 3. Deploy the backend on Render

1. Sign in to Render and select **New → Blueprint**.
2. Connect `shivugitec-source/SDVS`, branch `main`, and select the repository's `render.yaml`.
3. Review the `sdvs-api` service. The Blueprint asks for private values rather than storing credentials in source.
4. Set the following values in the Render service's Environment page (do not share them in chat):
   - `DATABASE_URL`: Neon connection string
   - `BOOTSTRAP_PASSWORD`: a unique, strong password for the initial administrator
   - `BOOTSTRAP_EMAIL`: `shivugitec@gmail.com`
   - `BOOTSTRAP_PHONE`: administrator's mobile in international format, e.g. `+91...`
   - `SMTP_PASSWORD`: Gmail App Password for `shivugitec@gmail.com` (not the normal Gmail password)
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`: Twilio credentials and sender number
   - the five R2 values listed in section 2
5. Keep the generated `JWT_SECRET` and `OTP_SECRET` values private. The Blueprint creates them automatically.
6. Deploy. The service must pass its `/health` check. A healthy response includes database and storage status.
7. Copy the Render service's public HTTPS URL.

The application intentionally fails startup if the JWT/OTP secrets are weak or required R2 configuration is missing. Do not disable `REQUIRE_R2` for a hosted deployment.

## 4. Connect the dashboard

1. Open the [SDVS Master Admin page](https://shivugitec-source.github.io/SDVS/admin.html).
2. Enter the Render HTTPS service URL in **Backend connection** and save it.
3. Request both OTPs for `shivugitec@gmail.com`, then sign in.
4. Create evaluator accounts only after verifying the first administrator can sign in and manage users.

## 5. Current implementation and important limits

- R2 upload and authenticated PDF retrieval are integrated in the API.
- The API checks PostgreSQL and R2 from `/health`.
- The frontend dashboard still uses browser-local IndexedDB for its current PDF/evaluation workspace; it is not yet wired end-to-end to the backend APIs. The secure backend APIs are a separate service layer at this stage.
- `Base.metadata.create_all` is still used at startup; versioned database migrations must be added before schema evolution in production.
- OTP controls need a further security review and integration tests before handling high-stakes examinations.
- Render free services may sleep when idle. SMS delivery requires a configured provider and may incur charges.
- Do not upload real student answer scripts until the dashboard-to-API workflow, role boundaries, audit trail, retention policy, and backups have been tested end-to-end.
