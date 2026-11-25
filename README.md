# Shivashirwad Digital Valuation System (SDVS)

MVP starter scaffold for anonymous, question-wise university examination valuation.

## Included
- FastAPI backend with SQLAlchemy and PostgreSQL configuration
- Institution-scoped users and starter role checks
- Examination, subject, question, script, allocation and evaluation endpoints
- Question-wise mark validation and server-side total calculation
- Draft saving and submission locking
- Audit event records
- React/Vite teacher interface with English/Kannada toggle
- Docker Compose development environment

## Local development
1. Copy `.env.example` to `.env` and replace all development secrets.
2. Start PostgreSQL and API: `docker compose up --build`
3. Start the frontend: `cd frontend && npm install && npm run dev`
4. API documentation: `http://localhost:8000/docs`
5. Frontend: `http://localhost:5173`

## Security and production-readiness notice
This is a development MVP scaffold, not a production-certified examination system. Do not use real candidate data until institutional SSO/MFA, private encrypted object storage, upload malware scanning, migrations, database-level immutability, audit archival, privacy/retention review, security testing and disaster-recovery validation are complete.

The current PDF storage adapter is local development storage. PDF metadata removal does not redact names or other identifiers visibly written on scanned pages. The sample dashboard must not be treated as live examination statistics until connected to an authorised deployment.

## Authentication and master administrator

The authentication API now supports two-channel OTP sign-in:
1. User enters their registered Gmail/email address.
2. SDVS sends one six-digit OTP to that email using Gmail SMTP.
3. SDVS sends a separate six-digit OTP to the registered mobile number through Twilio.
4. Both codes must be verified in the same sign-in challenge before a JWT is issued.

Master administrator endpoints (JWT role `SUPER_ADMIN`):
- `POST /admin/users` — provision a user with full name, email, E.164 mobile number and role.
- `GET /admin/users` — list institution users and verification status.
- `PATCH /admin/users/{user_id}/active?active=false` — deactivate/reactivate a user.
- `POST /auth/request-otp` — request both OTPs using `{"email":"person@example.com"}`.
- `POST /auth/verify-otp` — verify one channel using `{"email":"person@example.com","channel":"email|phone","code":"123456"}`. Repeat for the other channel; token is returned only after both codes from that challenge are correct.

### Required deployment configuration
- Set `DEV_BOOTSTRAP=1` only for the first controlled bootstrap, and configure `BOOTSTRAP_USERNAME`, `BOOTSTRAP_PASSWORD`, `BOOTSTRAP_NAME`, `BOOTSTRAP_EMAIL`, and `BOOTSTRAP_PHONE`. The bootstrap account receives `SUPER_ADMIN`.
- Configure Gmail SMTP with a Google App Password (not your Gmail account password): `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
- Configure Twilio SMS with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER`. Mobile numbers must be in international E.164 format, e.g. `+919876543210`.
- Set unique random `JWT_SECRET` and `OTP_SECRET`. Never commit actual credentials or OTP values.
- Set `DEV_BOOTSTRAP=0` after the master administrator has been provisioned.

**Deployment dependency:** GitHub Pages hosts only the static web UI. OTP delivery, account storage and JWT issuance require the FastAPI backend to be deployed on a secure HTTPS host with persistent PostgreSQL storage and these secrets configured. Do not expose SMTP/Twilio credentials in frontend JavaScript or GitHub Pages.
