# Shivashirwad Digital Valuation System (SDVS)

MVP starter scaffold for anonymous, question-wise university examination valuation.

## Included
- FastAPI backend with SQLAlchemy and PostgreSQL configuration
- Institution-scoped users and starter role checks
- Examination, subject, question, script, allocation and evaluation endpoints
- Question-wise mark validation and server-side total calculation
- Draft saving and submission locking
- Audit event records
- React/Vite evaluator interface with English/Kannada toggle
- Docker Compose development environment

## Product surfaces and their boundaries

SDVS contains separate browser and service components. They are not interchangeable:

1. **Offline browser prototype (docs/index.html)** — the GitHub Pages dashboard uses fictional sample data and browser-local IndexedDB. It does not authenticate to the FastAPI API or read institutional records.
2. **Static master-admin client (docs/admin.html)** — this page is a browser-based API client for master-admin OTP sign-in and administrative endpoints. It requires an operator to configure the URL of a separately hosted HTTPS API. Publishing this HTML file to GitHub Pages does not deploy that API; no live backend is currently deployed or verified.
3. **Connected evaluator frontend (frontend/)** — the React/Vite application implements the two-channel email/mobile OTP flow and calls evaluator API routes. CI compiles it with TypeScript and Vite, but the build is not a deployment. A live evaluator session against a hosted API has not been verified.
4. **Backend API (backend/)** — the FastAPI service contains authentication, authorization, valuation and storage endpoints. The repository workflows do not deploy this service. It requires a separately configured HTTPS host, persistent PostgreSQL, private object storage and OTP delivery providers.

The GitHub Pages workflow publishes only docs/. It publishes the offline dashboard prototype and the static master-admin client, but it does not deploy the React evaluator frontend or FastAPI backend. The dashboard prototype remains browser-local; the admin page cannot operate without a separately deployed API. Do not enter real student data in the prototype.

## Local development

1. Copy `.env.example` to `.env` and replace every placeholder.
2. Generate three distinct database passwords, for example with `openssl rand -hex 32`: `POSTGRES_PASSWORD`, `SDVS_MIGRATOR_PASSWORD`, and `SDVS_APP_PASSWORD`. Keep them different.
3. Replace `JWT_SECRET`, `OTP_SECRET`, bootstrap credentials, and any SMTP/Twilio placeholders.
4. Start PostgreSQL, role provisioning, migrations, and API with `docker compose up --build`.
5. Start the connected evaluator frontend: `cd frontend && npm ci && npm run dev`.
6. The evaluator frontend defaults to `http://localhost:8000` for its API. Set `VITE_API_URL` when using a different local API address; never put credentials or secrets in Vite variables.
7. API documentation: `http://localhost:8000/docs`; frontend: `http://localhost:5173`.

Compose deliberately separates database credentials:

- `devs` is the bootstrap PostgreSQL administrator. Only the short-lived `provision` service receives its password.
- `sdvs_migrator` is a non-superuser owner of the SDVS schema and is used only by the Alembic migration service.
- `sdvs_app` is the API runtime role. It has application DML privileges but no schema DDL, trigger administration, or Alembic version-table mutation.

The API container does not receive the bootstrap or migrator passwords. The migration container does not receive the bootstrap or runtime passwords.

## Database migrations and PostgreSQL roles

PostgreSQL schema changes are versioned with Alembic. The initial baseline creates the 14 current ORM tables and indexes; revision 0002 installs valuation immutability triggers, and revision 0003 installs database guards for user identity, credentials, roles, and profile contact fields.

### Separate database roles

| Role | Purpose | Privileges |
|---|---|---|
| `devs` | Bootstrap administrator used only for provisioning | Database administration; never used by API or migration services |
| `sdvs_migrator` | Alembic migration identity and schema owner | Non-superuser DDL on the SDVS `public` schema |
| `sdvs_app` | FastAPI runtime identity | Required application DML; no schema DDL or trigger administration |

The `provision` service executes `backend/sql/provision_roles.sql` as the bootstrap administrator. It creates or updates the two service roles, removes role memberships, transfers existing SDVS objects in the dedicated `public` schema to `sdvs_migrator`, and configures grants/default privileges. Review this script before using it against a database that contains non-SDVS objects. The manual SQL companion `backend/sql/immutability_postgres.sql` now contains both valuation immutability triggers and the user/profile identity guards; Alembic remains the authoritative versioned deployment path.

After every migration, `backend/migrations/env.py` grants runtime DML to `sdvs_app` and reapplies sensitive-table restrictions:

- `sdvs_app` receives table-level UPDATE on `users` because PostgreSQL requires it for the API's `SELECT ... FOR UPDATE` row locks. A database trigger rejects changes to user IDs, institution, username, password hashes, or roles; only `active` may change.
- `sdvs_app` receives table-level UPDATE on `user_profiles` for the same row-lock requirement. A database trigger rejects changes to profile IDs, names, email addresses, and phone numbers; only the email/phone verification flags may change.
- Audit events are readable and insertable but cannot be updated or deleted by the runtime role.
- `alembic_version` is readable by the API but cannot be modified by it.
- The runtime role cannot create schema objects, own application tables, or disable valuation triggers.

Alembic refuses to run unless connected as the non-superuser `sdvs_migrator` schema owner. PostgreSQL API startup refuses to run unless connected as the restricted `sdvs_app` role and verifies the expected grants and enabled immutability triggers.

### Fresh Compose database

Run `docker compose up --build`. Compose starts PostgreSQL, provisions the three-role model, applies `alembic upgrade head` as `sdvs_migrator`, and only then starts the API as `sdvs_app`.

### Existing Compose volume

If an older volume was initialized with a previous `devs` password, `POSTGRES_PASSWORD` must initially match the password already stored in PostgreSQL. Start the database, connect with `docker compose exec db psql -U devs -d devs`, rotate the password using the interactive `\password devs` command, then update `POSTGRES_PASSWORD` in `.env` to that new value. Do not leave the old development password in place.

The provisioning service is idempotent and transfers ownership of existing objects in the dedicated `public` schema to `sdvs_migrator`. Take a verified backup and inspect custom objects before running it on an existing or shared database.

### Adopting a pre-Alembic PostgreSQL database

Do not let the normal migration service run against an unversioned legacy database before checking parity. Stop application writes and take a verified backup first. Then:

1. Start only PostgreSQL: `docker compose up -d db`.
2. Provision roles and transfer schema ownership: `docker compose run --rm provision`.
3. Run the read-only parity check as `sdvs_migrator`: `docker compose run --rm migrate python scripts/check_schema_parity.py`.
4. Continue only if the checker reports an exact match. Then run `docker compose run --rm migrate alembic stamp 0001_initial`.
5. Apply the remaining revision: `docker compose run --rm migrate alembic upgrade head`.
6. Start the application: `docker compose up --build`.

If the parity check reports any difference, stop. Do not stamp the database to hide drift; prepare and review a data-preserving forward migration for that schema first.

### Production migration procedure

Provision the roles as a controlled database-administration step. Run `alembic upgrade head` with `DATABASE_URL` set to the `sdvs_migrator` connection, then run `python scripts/verify_runtime_privileges.py` with `DATABASE_URL` set to `sdvs_app`. The API must receive only the runtime connection string; never inject bootstrap or migration credentials into the API process.

SQLite remains a local-development-only path and may create its schema from ORM metadata. PostgreSQL API startup does not call `Base.metadata.create_all`; it requires the current Alembic head and validates the runtime role and both immutability triggers.

The initial revision's downgrade drops SDVS tables and is destructive. CI exercises downgrade only against its disposable PostgreSQL service; do not use `alembic downgrade base` as a production rollback procedure. Production requires a separately reviewed backup/restore and forward-migration plan.

## Security and production-readiness notice

This is a development MVP scaffold, not a production-certified examination system. Do not use real candidate data until institutional SSO/MFA, private encrypted object storage, upload malware scanning, migrations, database-level immutability, audit archival, privacy/retention review, security testing and disaster-recovery validation are complete.

The current PDF storage adapter is local development storage. PDF metadata removal does not redact names or other identifiers visibly written on scanned pages. The sample dashboard must not be treated as live examination statistics until connected to an authorised deployment.

## Authentication and master administrator

The authentication API now supports two-channel OTP sign-in:
1. User enters their registered Gmail/email address.
2. SDVS sends one six-digit OTP to that email using Gmail SMTP.
3. SDVS sends a separate six-digit OTP to the registered mobile number through Twilio.
4. Both codes must be verified in the same sign-in challenge before a JWT is issued.

Master administrator endpoints (JWT role SUPER_ADMIN):
- POST /admin/users — provision a user with full name, email, E.164 mobile number and role.
- GET /admin/users — list institution users and verification status.
- PATCH /admin/users/{user_id}/active?active=false — deactivate/reactivate a user.
- POST /auth/request-otp — request both OTPs using {"email":"person@example.com"}.
- POST /auth/verify-otp — verify one channel using {"email":"person@example.com","channel":"email|phone","code":"123456"}. Repeat for the other channel; token is returned only after both codes from that challenge are correct.

### Required deployment configuration
- Set DEV_BOOTSTRAP=1 only for the first controlled bootstrap, and configure BOOTSTRAP_USERNAME, BOOTSTRAP_PASSWORD, BOOTSTRAP_NAME, BOOTSTRAP_EMAIL, and BOOTSTRAP_PHONE. The bootstrap account receives SUPER_ADMIN.
- Configure Gmail SMTP with a Google App Password (not your Gmail account password): SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
- Configure Twilio SMS with TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER. Mobile numbers must be in international E.164 format, e.g. +919876543210.
- Set unique random JWT_SECRET and OTP_SECRET. Never commit actual credentials or OTP values.
- Set DEV_BOOTSTRAP=0 after the master administrator has been provisioned.

**Deployment dependency:** OTP delivery, account storage and JWT issuance require the FastAPI backend to be deployed on a secure HTTPS host with persistent PostgreSQL storage and these secrets configured. Do not expose SMTP/Twilio credentials in frontend JavaScript or GitHub Pages.
