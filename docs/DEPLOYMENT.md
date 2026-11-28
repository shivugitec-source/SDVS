# SDVS deployment and controlled rollout

## Deployment status — not deployed

The repository does **not** currently have a verified hosted FastAPI deployment or a verified institutional PostgreSQL/R2 environment. GitHub Pages publishes only the static files under `docs/`; it does not deploy the React/Vite evaluator application or the FastAPI API.

The Render Blueprint in `render.yaml` is a configuration template, not evidence that a Render service, database, storage bucket, or OTP provider exists. It does not provision PostgreSQL roles or run Alembic migrations.

Do not create paid or external cloud resources, deploy the API, or use real student records without explicit project-owner approval. The steps below are an operator checklist for a separately approved deployment; they do not authorize one.

## Current hosting boundaries

- **GitHub Pages:** publishes the offline browser prototype and static Master Admin API client from `docs/`. The offline prototype uses fictional sample data and browser-local IndexedDB.
- **React/Vite evaluator application:** `frontend/` is type-checked and built in CI. A successful build is not a hosted deployment. Its evaluation workspace is not yet a complete end-to-end institutional workflow against the API.
- **FastAPI API:** `backend/` contains authentication, authorization, valuation, PostgreSQL, and storage endpoints. No hosted API deployment is verified.
- **PostgreSQL:** production schema changes use the versioned Alembic revisions `0001_initial`, `0002_immutability`, `0003_runtime_identity_guards`, and `0004_active_allocation_uniqueness`.
- **Answer-script storage:** hosted deployments must use a private S3-compatible Cloudflare R2 bucket with `REQUIRE_R2=true`. Do not enable public bucket access or expose object URLs.

The static Pages prototype is not connected to institutional records. Do not enter real candidate details or upload real answer scripts to it.

## Preconditions before an approved deployment

A deployment operator must have an approved hosting plan, a dedicated PostgreSQL database, private object storage, HTTPS, backup/restore procedures, and approved SMTP/SMS delivery. Keep all passwords, API tokens, OTP secrets, and connection strings outside Git.

### 1. Provision distinct PostgreSQL roles

Use a dedicated SDVS database. The provisioning script changes ownership of objects in the `public` schema; do not run it against a shared database containing unrelated application objects without a separately reviewed plan.

Generate three different strong passwords. The migrator and runtime passwords must each contain at least 32 characters. The script receives the bootstrap database administrator through standard PostgreSQL environment variables and the two service passwords through `SDVS_MIGRATOR_PASSWORD` and `SDVS_APP_PASSWORD`.

From the repository root, in a secured operator environment:

```sh
export PGHOST='database-host'
export PGPORT='5432'
export PGDATABASE='sdvs'
export PGUSER='bootstrap-admin'
export PGPASSWORD='bootstrap-admin-password'
export SDVS_MIGRATOR_PASSWORD='distinct-migrator-password-at-least-32-chars'
export SDVS_APP_PASSWORD='different-runtime-password-at-least-32-chars'

psql -v ON_ERROR_STOP=1 -f backend/sql/provision_roles.sql
```

Use real generated values only in the secured environment; the values above are examples, not credentials. Never commit a populated environment file.

The resulting identities are:

| Role | Purpose |
|---|---|
| Bootstrap database administrator | One-time role/object provisioning; never used by the API |
| `sdvs_migrator` | Non-superuser schema owner used for Alembic |
| `sdvs_app` | Restricted FastAPI runtime identity |

The API must never receive the bootstrap or migrator password.

### 2. Apply migrations to a fresh database

Run migrations as `sdvs_migrator`, not as the bootstrap administrator or `sdvs_app`:

```sh
cd backend
export DATABASE_URL='postgresql+psycopg://sdvs_migrator:...@database-host:5432/sdvs'
alembic upgrade head
alembic current
alembic check
python scripts/verify_migrations.py
```

The current head is `0004_active_allocation_uniqueness`. The verification script checks valuation immutability, enabled trigger definitions, active-allocation uniqueness, and submitted-versus-draft mutation behaviour. Migration 0004 creates a partial unique index so each script/evaluator pair can have at most one active allocation while retaining inactive assignment history. It fails closed if duplicate active pairs already exist; it does not delete, deactivate, or rewrite any rows.

### 3. Adopt an existing pre-Alembic database safely

Do not run the normal migration service against an unversioned legacy database first.

1. While the current application is still available, check for duplicate active allocations using the query below. For every returned pair, use the authorized `POST /allocations/{allocation_id}/revoke` endpoint with one active allocation ID from that pair; it revokes all active historical rows for the pair. Do not delete history or resolve duplicates with unaudited SQL.
2. Enter the maintenance window, stop new application writes, take a backup, and verify that it can be restored.
3. Connect as `sdvs_migrator` and run the read-only parity check:

   ```sh
   cd backend
   export DATABASE_URL='postgresql+psycopg://sdvs_migrator:...@database-host:5432/sdvs'
   python scripts/check_schema_parity.py
   ```

4. Continue only if the checker reports an exact match to the `0001_initial` baseline.
5. Repeat the duplicate-active-allocation query under the write freeze:

   ```sql
   SELECT script_id, evaluator_id, COUNT(*)
   FROM allocations
   WHERE active IS TRUE
   GROUP BY script_id, evaluator_id
   HAVING COUNT(*) > 1;
   ```

   If any rows are returned, do not proceed. Reopen only the controlled administrative path needed to revoke each affected pair through the authorized endpoint, then reapply the write freeze and repeat the query. Reassign only after the migration is complete. Do not delete allocation history or resolve this by direct unaudited SQL.

6. Only after schema parity and duplicate checks pass, stamp and upgrade:

   ```sh
   alembic stamp 0001_initial
   alembic upgrade head
   alembic current
   alembic check
   python scripts/verify_migrations.py
   ```

Migration 0004 repeats the duplicate check inside the migration and fails closed if a duplicate appears between the operator check and upgrade. It does not change existing allocation rows.

If the parity check reports any difference, stop. Do not stamp the database to hide schema drift. Prepare and review a data-preserving forward migration first.

The downgrade for `0001_initial` drops SDVS tables. CI uses downgrade only on disposable PostgreSQL. It is **not** a production rollback plan; production recovery requires a separately tested backup/restore or reviewed forward-fix procedure.

### 4. Verify the restricted runtime identity

Before starting the API, run the runtime privilege verification using the `sdvs_app` connection:

```sh
cd backend
export DATABASE_URL='postgresql+psycopg://sdvs_app:...@database-host:5432/sdvs'
python scripts/verify_runtime_privileges.py
```

The API's PostgreSQL startup guard also requires:

- Current Alembic head.
- Connection as `sdvs_app`.
- Non-superuser and non-owner runtime privileges.
- No schema DDL or migration-role membership.
- Required runtime DML and row-lock privileges.
- Enabled valuation immutability and user/profile identity triggers.

If the guard fails, do not bypass it or run the API as a superuser. Correct provisioning or migration state.

### 5. Configure API secrets, OTP delivery, and private storage

Use unique random values for `JWT_SECRET` and `OTP_SECRET` (at least 32 characters). Configure SMTP and Twilio with credentials held in the hosting provider's secret store. Never put secrets in Vite variables, HTML, GitHub Pages, or source control.

For hosted PDF storage:

- Set `REQUIRE_R2=true`.
- Configure the R2 endpoint, access key, secret key, and bucket name.
- Scope the R2 token to the one private bucket with only the required object read/write access.
- Keep public bucket access disabled.
- Do not rely on an ephemeral container filesystem for answer scripts.

The API streams PDFs only after checking the authenticated evaluator's allocation. That backend control does not replace end-to-end testing of the evaluator workflow.

### 6. Bootstrap the first SDVS administrator deliberately

The Render template defaults `DEV_BOOTSTRAP=false`. Keep it false during normal operation.

Only during an approved first-time bootstrap, set `DEV_BOOTSTRAP=true` with a unique bootstrap username, strong temporary password, administrator name, registered email, and E.164 mobile number. Start the API, verify that the intended administrator was created and can complete both OTP channels, then immediately set `DEV_BOOTSTRAP=false` and restart/redeploy.

Do not leave bootstrap mode enabled as a standing production setting. Do not use a personal address or phone number as a source-code default.

## Render Blueprint limitations

`render.yaml` describes only the API web service. It does **not** create the PostgreSQL database, provision `sdvs_app`/`sdvs_migrator`, apply Alembic migrations, create the R2 bucket, or configure SMTP/Twilio accounts.

Before any manually approved Render service deployment:

1. Complete database role provisioning and migrations outside the API service using the controlled procedure above.
2. Set the Render `DATABASE_URL` to the `sdvs_app` runtime connection string only.
3. Supply the approved JWT/OTP, SMTP, Twilio, and private R2 settings.
4. Confirm that the schema and runtime privilege verification pass.
5. Obtain explicit approval before creating or deploying the cloud service.

The Blueprint has `autoDeploy: false` and `DEV_BOOTSTRAP=false` by default to prevent unattended deployment and persistent bootstrap mode. A manual deployment is still a cloud deployment and requires approval.

## Post-deployment acceptance — not yet demonstrated

After a separately approved deployment, verify at minimum:

- `/health` reports database and private storage availability.
- Email and mobile OTPs are delivered through the approved providers.
- JWT is issued only after both channels verify.
- EVALUATOR accounts cannot access administrative routes.
- An evaluator can see only their active allocations.
- PDF retrieval checks the authenticated allocation and returns the expected private object.
- Draft marks can be saved; submitted marks and evaluations cannot be changed or deleted.
- User identity/role changes and audit-log mutation are denied to the runtime database role.
- Audit retention, backup, restoration, incident response, and institutional data-retention rules are tested.

The React evaluator workspace is not yet a complete end-to-end institutional workflow against the backend. Do not upload real student answer scripts or declare the system operational until that integration, role boundaries, privacy review, security testing, retention, and disaster recovery have been validated.

## Deployment evidence

A GitHub Pages workflow success proves only that the static `docs/` artifact was deployed. A backend CI success proves only that the code and disposable PostgreSQL tests passed. Neither is evidence of a live Render service, hosted database, R2 bucket, real OTP delivery, or end-to-end examination valuation.
