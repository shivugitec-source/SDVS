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

1. Copy .env.example to .env and replace all development secrets.
2. Start PostgreSQL and API: docker compose up --build.
3. Start the connected evaluator frontend: cd frontend && npm ci && npm run dev.
4. The evaluator frontend defaults to http://localhost:8000 for its API. Set VITE_API_URL when using a different local API address; never put credentials or secrets in Vite variables.
5. API documentation: http://localhost:8000/docs.
6. Frontend: http://localhost:5173.

## Database migrations

PostgreSQL schema changes are versioned with Alembic. The initial baseline creates the 14 current ORM tables and indexes; the next revision installs PostgreSQL triggers that reject mutation or deletion of submitted/approved evaluations and their marks.

- Fresh local Compose databases are migrated by the one-shot \`migrate\` service before the API starts.
- SQLite remains a local-development-only path and may create its schema from ORM metadata.
- PostgreSQL API startup does **not** call \`Base.metadata.create_all\`. It requires the current Alembic head and verifies both immutability triggers.
- Production deployments must run migrations as an explicit, backed-up release step (for example, \`alembic upgrade head\`) before starting the API. The API does not run migrations automatically.

### Adopting a pre-Alembic PostgreSQL database

Do not run the initial migration blindly against an existing database. Stop application writes and take a verified backup first.

1. Run \`python scripts/check_schema_parity.py\` against the existing database. This is read-only and compares tables, columns, indexes, constraints and types with the initial baseline.
2. Continue only if it reports an exact match. Then run \`alembic stamp 0001_initial\` to record the existing schema without recreating tables.
3. Run \`alembic upgrade head\` to install the versioned immutability-trigger revision.
4. Run \`alembic check\` and verify application startup.

For Docker Compose, the equivalent preflight commands are:

\`\`\`sh
docker compose run --rm migrate python scripts/check_schema_parity.py
docker compose run --rm migrate alembic stamp 0001_initial
docker compose run --rm migrate alembic upgrade head
\`\`\`

If the parity check reports any difference, stop. Do not stamp the database to hide drift; prepare and review a data-preserving forward migration for that schema first. Existing production databases require a separately reviewed backup/restore and migration plan.

The initial revision's downgrade drops the SDVS tables and is destructive. CI exercises downgrade only against its disposable PostgreSQL service; do not use \`alembic downgrade base\` as a production rollback procedure.

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
