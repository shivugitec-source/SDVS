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
