import hashlib, json, secrets, io, hmac, re
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text, update
from pypdf import PdfReader, PdfWriter
from .db import Base, engine, get_db, SessionLocal
from .models import *
from .schemas import *
from .security import verify_password, create_token, hash_password, current_user, require_roles
from .config import settings
from .auth_delivery import send_email_otp, send_sms_otp
from .storage import put_object, get_object, check_storage, r2_enabled

app = FastAPI(title="Shivashirwad Digital Valuation System API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_origin_regex=r"https://shivugitec-source\.github\.io", allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])

@app.on_event("startup")
def startup():
    if len(settings.jwt_secret) < 32 or settings.jwt_secret == "INSECURE-DEV-ONLY-CHANGE-ME":
        raise RuntimeError("JWT_SECRET must be a private random value of at least 32 characters.")
    if len(settings.otp_secret) < 32:
        raise RuntimeError("OTP_SECRET must be a private random value of at least 32 characters.")
    if settings.dev_bootstrap and not all([settings.bootstrap_password, settings.bootstrap_email, settings.bootstrap_phone]):
        raise RuntimeError("DEV_BOOTSTRAP requires BOOTSTRAP_PASSWORD, BOOTSTRAP_EMAIL and BOOTSTRAP_PHONE.")
    check_storage()
    Base.metadata.create_all(bind=engine)
    if settings.dev_bootstrap:
        with SessionLocal() as db:
            inst = db.scalar(select(Institution).where(Institution.code == "DEMO"))
            if not inst:
                inst = Institution(name="Shivashirwad SDVS", code="DEMO")
                db.add(inst); db.flush()
            master = db.scalar(select(User).where(User.institution_id == inst.id,
                                                   User.username == settings.bootstrap_username))
            if not master:
                master = User(institution_id=inst.id, username=settings.bootstrap_username,
                              password_hash=hash_password(settings.bootstrap_password), role=Role.SUPER_ADMIN)
                db.add(master); db.flush()
            if settings.bootstrap_email and settings.bootstrap_phone:
                profile = db.scalar(select(UserProfile).where(UserProfile.user_id == master.id))
                if not profile:
                    if db.scalar(select(UserProfile).where((UserProfile.email == settings.bootstrap_email.strip().lower()) |
                                                            (UserProfile.phone == settings.bootstrap_phone.strip()))):
                        raise RuntimeError("Master admin email or phone is already assigned to another account.")
                    db.add(UserProfile(user_id=master.id, full_name=settings.bootstrap_name,
                                       email=settings.bootstrap_email.strip().lower(),
                                       phone=settings.bootstrap_phone.strip()))
            db.commit()

def audit(db, user, action, entity_type, entity_id, detail=None):
    db.add(AuditEvent(institution_id=user.institution_id, actor_id=user.id, action=action,
                      entity_type=entity_type, entity_id=entity_id,
                      detail=json.dumps(detail or {}, sort_keys=True)))

@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        storage = check_storage()
        return {"status": "ok", "service": "sdvs-api", "database": "ok", "storage": storage}
    except Exception:
        raise HTTPException(503, "Service dependency check failed.")

@app.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    raise HTTPException(410, "Password-only login is disabled. Use email and mobile OTP verification.")

@app.get("/admin/dashboard")
def admin_dashboard(db: Session = Depends(get_db), user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER"))):
    scripts = db.scalars(select(EvaluationScript).where(EvaluationScript.institution_id == user.institution_id)).all()
    ids = [s.id for s in scripts]
    evaluated = db.scalar(select(func.count(Evaluation.id)).where(Evaluation.script_id.in_(ids),
                       Evaluation.status == Status.SUBMITTED)) if ids else 0
    active = db.scalar(select(func.count(User.id)).where(User.institution_id == user.institution_id,
                      User.role == Role.EVALUATOR, User.active == True)) or 0
    return {"total_scripts": len(scripts), "evaluated": evaluated or 0,
            "pending": max(0, len(scripts) - (evaluated or 0)), "active_evaluators": active}

@app.post("/examinations")
def create_exam(body: ExamIn, db: Session = Depends(get_db), user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER"))):
    obj = Examination(institution_id=user.institution_id, name=body.name, academic_year=body.academic_year)
    db.add(obj); db.flush(); audit(db, user, "EXAM_CREATED", "examination", obj.id); db.commit()
    return {"id": obj.id, "name": obj.name}

@app.post("/examinations/{exam_id}/subjects")
def create_subject(exam_id: str, body: SubjectIn, db: Session = Depends(get_db), user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER"))):
    exam = db.get(Examination, exam_id)
    if not exam or exam.institution_id != user.institution_id: raise HTTPException(404, "Examination not found")
    obj = Subject(examination_id=exam.id, code=body.code, name=body.name, max_marks=body.max_marks)
    db.add(obj); db.flush(); audit(db, user, "SUBJECT_CREATED", "subject", obj.id); db.commit()
    return {"id": obj.id, "name": obj.name}

@app.post("/subjects/{subject_id}/questions")
def create_question(subject_id: str, body: QuestionIn, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER", "CHIEF_EXAMINER"))):
    subject = db.get(Subject, subject_id)
    if not subject or db.get(Examination, subject.examination_id).institution_id != user.institution_id:
        raise HTTPException(404, "Subject not found")
    q = Question(subject_id=subject.id, question_number=body.question_number, max_marks=body.max_marks, rubric=body.rubric)
    db.add(q); db.flush(); audit(db, user, "QUESTION_CREATED", "question", q.id); db.commit()
    return {"id": q.id, "question_number": q.question_number, "max_marks": float(q.max_marks)}

@app.get("/subjects/{subject_id}/questions")
def list_questions(subject_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    subject = db.get(Subject, subject_id)
    if not subject or db.get(Examination, subject.examination_id).institution_id != user.institution_id:
        raise HTTPException(404, "Subject not found")
    return [{"id": q.id, "question_number": q.question_number, "max_marks": float(q.max_marks), "rubric": q.rubric}
            for q in db.scalars(select(Question).where(Question.subject_id == subject.id)).all()]

@app.post("/users/evaluators")
def create_evaluator(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db),
                     user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER"))):
    if len(password) < 12: raise HTTPException(422, "Temporary password must contain at least 12 characters")
    if db.scalar(select(User).where(User.institution_id == user.institution_id, User.username == username)):
        raise HTTPException(409, "Username already exists")
    evaluator = User(institution_id=user.institution_id, username=username,
                     password_hash=hash_password(password), role=Role.EVALUATOR)
    db.add(evaluator); db.flush(); audit(db, user, "EVALUATOR_CREATED", "user", evaluator.id); db.commit()
    return {"id": evaluator.id, "username": evaluator.username, "role": evaluator.role.value}

@app.post("/scripts/upload")
async def upload_script(subject_id: str = Form(...), candidate_name: str = Form(...),
                        register_number: str = Form(...), file: UploadFile = File(...),
                        db: Session = Depends(get_db), user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER"))):
    subject = db.get(Subject, subject_id)
    if not subject or db.get(Examination, subject.examination_id).institution_id != user.institution_id:
        raise HTTPException(404, "Subject not found")
    if not (file.filename or "").lower().endswith(".pdf") or file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(415, "Only PDF answer scripts are accepted")
    raw = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(raw) > settings.max_upload_mb * 1024 * 1024: raise HTTPException(413, "PDF exceeds upload limit")
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted: raise HTTPException(422, "Encrypted PDFs are not accepted")
        if not reader.pages: raise HTTPException(422, "PDF has no pages")
        writer = PdfWriter()
        for page in reader.pages: writer.add_page(page)
        out = io.BytesIO(); writer.write(out); clean = out.getvalue()
    except HTTPException: raise
    except Exception: raise HTTPException(422, "PDF is invalid or unreadable")
    code = "SDVS-" + secrets.token_hex(6).upper()
    digest = hashlib.sha256(clean).hexdigest()
    key = code + ".pdf"
    try:
        put_object(key, clean)
    except Exception as exc:
        raise HTTPException(503, "Private PDF storage is unavailable. Check R2 configuration.") from exc
    candidate = Candidate(institution_id=user.institution_id, register_number=register_number, full_name=candidate_name)
    db.add(candidate); db.flush()
    script = EvaluationScript(institution_id=user.institution_id, subject_id=subject.id, anonymous_code=code,
                              object_key=key, pdf_sha256=digest, page_count=len(reader.pages))
    db.add(script); db.flush(); db.add(IdentityMapping(candidate_id=candidate.id, script_id=script.id))
    audit(db, user, "SCRIPT_UPLOADED", "script", script.id, {"anonymous_code": code, "sha256": digest}); db.commit()
    return {"script_id": script.id, "anonymous_code": code, "page_count": script.page_count, "sha256": digest}

@app.post("/allocations")
def allocate(body: AllocationIn, db: Session = Depends(get_db), user: User = Depends(require_roles("SUPER_ADMIN", "EXAM_CONTROLLER"))):
    script = db.get(EvaluationScript, body.script_id); evaluator = db.get(User, body.evaluator_id)
    if not script or script.institution_id != user.institution_id: raise HTTPException(404, "Script not found")
    if not evaluator or evaluator.institution_id != user.institution_id or evaluator.role != Role.EVALUATOR or not evaluator.active:
        raise HTTPException(400, "Evaluator must be active in this institution")
    obj = Allocation(script_id=script.id, evaluator_id=evaluator.id)
    db.add(obj); db.flush(); audit(db, user, "SCRIPT_ALLOCATED", "allocation", obj.id); db.commit()
    return {"allocation_id": obj.id}

@app.get("/evaluator/scripts")
def my_scripts(db: Session = Depends(get_db), user: User = Depends(require_roles("EVALUATOR"))):
    rows = db.scalars(select(Allocation).where(Allocation.evaluator_id == user.id, Allocation.active == True)).all()
    result = []
    for allocation in rows:
        script = db.get(EvaluationScript, allocation.script_id)
        if script and script.institution_id == user.institution_id:
            subject = db.get(Subject, script.subject_id)
            result.append({"script_id": script.id, "anonymous_code": script.anonymous_code, "subject_id": script.subject_id,
                           "subject_name": subject.name, "page_count": script.page_count, "status": script.status})
    return result

def assigned_script(script_id, user, db, *, lock_allocation=False):
    script = db.get(EvaluationScript, script_id)
    if not script or script.institution_id != user.institution_id:
        raise HTTPException(404, "Script not found")
    allocation_query = select(Allocation).where(
        Allocation.script_id == script.id,
        Allocation.evaluator_id == user.id,
        Allocation.active == True,
    )
    if lock_allocation:
        allocation_query = allocation_query.with_for_update()
    allocation = db.scalar(allocation_query)
    if not allocation:
        raise HTTPException(404, "Script not found")
    return script


def get_evaluation_for_update(
    db: Session,
    evaluator_id: str,
    *,
    evaluation_id: str | None = None,
    script_id: str | None = None,
):
    """Lock and refresh one evaluator's draft row after its allocation is locked."""
    if (evaluation_id is None) == (script_id is None):
        raise ValueError("Provide exactly one of evaluation_id or script_id.")
    statement = select(Evaluation).where(Evaluation.evaluator_id == evaluator_id)
    if evaluation_id is not None:
        statement = statement.where(Evaluation.id == evaluation_id)
    else:
        statement = statement.where(Evaluation.script_id == script_id)
    return db.scalar(
        statement.with_for_update().execution_options(populate_existing=True)
    )

@app.get("/evaluator/scripts/{script_id}/questions")
def assigned_questions(script_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("EVALUATOR"))):
    script = assigned_script(script_id, user, db)
    return [{"id": q.id, "question_number": q.question_number, "max_marks": float(q.max_marks), "rubric": q.rubric}
            for q in db.scalars(select(Question).where(Question.subject_id == script.subject_id)).all()]

@app.get("/evaluator/scripts/{script_id}/pdf")
def assigned_pdf(script_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("EVALUATOR"))):
    script = assigned_script(script_id, user, db)
    try:
        content = get_object(script.object_key)
    except FileNotFoundError:
        raise HTTPException(404, "PDF not found")
    except Exception as exc:
        raise HTTPException(503, "Private PDF storage is unavailable.") from exc
    return StreamingResponse(io.BytesIO(content), media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="anonymous-answer-script.pdf"',
                 "Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"})

@app.get("/evaluator/scripts/{script_id}/evaluation")
def get_evaluation(script_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("EVALUATOR"))):
    assigned_script(script_id, user, db)
    ev = db.scalar(select(Evaluation).where(Evaluation.script_id == script_id, Evaluation.evaluator_id == user.id))
    if not ev: return {"id": None, "status": "DRAFT", "marks": []}
    marks = db.scalars(select(QuestionMark).where(QuestionMark.evaluation_id == ev.id)).all()
    return {"id": ev.id, "status": ev.status.value, "total_marks": str(ev.total_marks) if ev.total_marks is not None else None,
            "marks": [{"question_id": m.question_id, "marks": str(m.marks), "remark": m.remark, "private_note": m.private_note} for m in marks]}

@app.post("/evaluations")
def save_draft(body: EvaluationIn, db: Session = Depends(get_db), user: User = Depends(require_roles("EVALUATOR"))):
    # Serialize draft writes and submission for this assignment.
    script = assigned_script(body.script_id, user, db, lock_allocation=True)
    ev = get_evaluation_for_update(db, user.id, script_id=script.id)
    if ev and ev.status != Status.DRAFT: raise HTTPException(409, "Submitted evaluation is locked")
    if not ev: ev = Evaluation(script_id=script.id, evaluator_id=user.id, status=Status.DRAFT); db.add(ev); db.flush()
    questions = db.scalars(select(Question).where(Question.subject_id == script.subject_id)).all()
    qmap = {q.id: q for q in questions}; incoming = {m.question_id: m for m in body.marks}
    if len(incoming) != len(body.marks): raise HTTPException(422, "Duplicate question entries")
    for qid, mark in incoming.items():
        if qid not in qmap: raise HTTPException(422, "Question does not belong to this subject")
        if Decimal(str(mark.marks)) > Decimal(str(qmap[qid].max_marks)):
            raise HTTPException(422, f"Marks exceed maximum for {qmap[qid].question_number}")
    existing_marks = {
        mark.question_id: mark
        for mark in db.scalars(
            select(QuestionMark).where(QuestionMark.evaluation_id == ev.id)
        ).all()
    }
    for question_id, existing in existing_marks.items():
        if question_id not in incoming:
            db.delete(existing)
    for question_id, mark in incoming.items():
        existing = existing_marks.get(question_id)
        if existing is None:
            db.add(
                QuestionMark(
                    evaluation_id=ev.id,
                    question_id=question_id,
                    marks=mark.marks,
                    remark=mark.remark,
                    private_note=mark.private_note,
                )
            )
        else:
            existing.marks = mark.marks
            existing.remark = mark.remark
            existing.private_note = mark.private_note
    ev.total_marks = sum((Decimal(str(m.marks)) for m in incoming.values()), Decimal("0"))
    audit(db, user, "DRAFT_SAVED", "evaluation", ev.id, {"question_count": len(incoming)}); db.commit()
    return {"id": ev.id, "evaluation_id": ev.id, "status": ev.status.value, "draft_total": str(ev.total_marks)}

@app.post("/evaluations/{evaluation_id}/submit")
def submit(evaluation_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("EVALUATOR"))):
    ev = db.get(Evaluation, evaluation_id)
    if not ev or ev.evaluator_id != user.id: raise HTTPException(404, "Evaluation not found")
    # Use the same lock order as draft saves: allocation first, evaluation second.
    # Refresh the evaluation after acquiring both locks so a concurrent save or
    # submission cannot commit marks that disagree with the submitted hash.
    script = assigned_script(ev.script_id, user, db, lock_allocation=True)
    ev = get_evaluation_for_update(db, user.id, evaluation_id=evaluation_id)
    if not ev: raise HTTPException(404, "Evaluation not found")
    if ev.status != Status.DRAFT: raise HTTPException(409, "Already submitted")
    questions = db.scalars(select(Question).where(Question.subject_id == script.subject_id)).all()
    marks = db.scalars(select(QuestionMark).where(QuestionMark.evaluation_id == ev.id)).all(); byq = {m.question_id: m for m in marks}
    missing = [q.question_number for q in questions if q.id not in byq]
    if missing: raise HTTPException(422, {"message": "Evaluate every question before submission", "missing": missing})
    total = sum((Decimal(str(byq[q.id].marks)) for q in questions), Decimal("0"))
    payload = [{"question_id": q.id, "marks": str(byq[q.id].marks), "remark": byq[q.id].remark, "private_note": byq[q.id].private_note} for q in questions]
    digest = hashlib.sha256(json.dumps({"script_id": script.id, "evaluator_id": user.id, "version": ev.version,
                      "total": str(total), "marks": payload}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ev.total_marks = total; ev.content_hash = digest; ev.status = Status.SUBMITTED
    ev.submitted_at = datetime.now(timezone.utc); script.status = "SUBMITTED"
    audit(db, user, "EVALUATION_SUBMITTED", "evaluation", ev.id, {"sha256": digest, "total": str(total)}); db.commit()
    return {"id": ev.id, "status": ev.status.value, "total_marks": str(total), "content_sha256": digest}

def otp_digest(code:str)->str:
    secret=(settings.otp_secret or settings.jwt_secret).encode()
    return hmac.new(secret,code.encode(),hashlib.sha256).hexdigest()

def claim_otp_attempt(
    db: Session,
    challenge: OTPChallenge,
    *,
    consume: bool,
    now: datetime | None = None,
) -> bool:
    """Atomically claim one unexpired OTP attempt so stale requests cannot reuse it."""
    now = now or datetime.now(timezone.utc)
    result = db.execute(
        update(OTPChallenge)
        .where(
            OTPChallenge.id == challenge.id,
            OTPChallenge.consumed.is_(False),
            OTPChallenge.attempts < 5,
            OTPChallenge.expires_at > now,
        )
        .values(
            attempts=OTPChallenge.attempts + 1,
            consumed=consume,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1

def get_otp_request_user_for_update(db: Session, user_id: str):
    """Lock the account row so concurrent OTP quota checks serialize on PostgreSQL."""
    return db.scalar(select(User).where(User.id == user_id).with_for_update())


@app.post("/auth/request-otp")
def request_otp(body: OTPRequestIn, db: Session = Depends(get_db)):
    email=body.email.strip().lower()
    profile=db.scalar(select(UserProfile).where(UserProfile.email==email))
    if not profile: raise HTTPException(404,"This email is not registered. Contact the SDVS master administrator.")
    user=get_otp_request_user_for_update(db, profile.user_id)
    if not user or not user.active: raise HTTPException(403,"This account is inactive.")
    recent=db.scalar(select(func.count(OTPChallenge.id)).where(OTPChallenge.user_id==user.id, OTPChallenge.channel=="email",
        OTPChallenge.created_at >= datetime.now(timezone.utc)-__import__('datetime').timedelta(seconds=60)))
    if recent and recent>=2: raise HTTPException(429,"Please wait 60 seconds before requesting another OTP.")
    # A new OTP pair replaces every older unconsumed challenge for this user.
    # Retain consumed rows, but do not let a partially completed older batch
    # become valid again after the replacement batch has been used.
    db.execute(
        update(OTPChallenge)
        .where(
            OTPChallenge.user_id == user.id,
            OTPChallenge.consumed.is_(False),
        )
        .values(consumed=True)
        .execution_options(synchronize_session=False)
    )
    email_code=f"{secrets.randbelow(1000000):06d}"
    phone_code=f"{secrets.randbelow(1000000):06d}"
    batch_id=secrets.token_urlsafe(24)
    expiry=datetime.now(timezone.utc)+__import__('datetime').timedelta(minutes=settings.otp_ttl_minutes)
    db.add_all([OTPChallenge(user_id=user.id,batch_id=batch_id,channel="email",code_hash=otp_digest(email_code),expires_at=expiry),
                OTPChallenge(user_id=user.id,batch_id=batch_id,channel="phone",code_hash=otp_digest(phone_code),expires_at=expiry)])
    db.commit()
    try:
        send_email_otp(profile.email,email_code)
        send_sms_otp(profile.phone,phone_code)
    except HTTPException:
        # A partial delivery must never leave a usable one-channel OTP batch.
        db.execute(
            update(OTPChallenge)
            .where(
                OTPChallenge.user_id == user.id,
                OTPChallenge.batch_id == batch_id,
                OTPChallenge.consumed.is_(False),
            )
            .values(consumed=True)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        raise
    return {"message":"Verification codes sent to your registered email and mobile number.",
            "email_masked":profile.email[:2]+"***"+profile.email[profile.email.index("@"):],
            "phone_masked":"******"+profile.phone[-4:],"expires_in_minutes":settings.otp_ttl_minutes}

@app.post("/auth/verify-otp")
def verify_otp(body: OTPVerifyIn, db: Session = Depends(get_db)):
    email=body.email.strip().lower(); channel=body.channel.strip().lower()
    if channel not in ("email","phone"): raise HTTPException(422,"Channel must be email or phone.")
    profile=db.scalar(select(UserProfile).where(UserProfile.email==email))
    if not profile: raise HTTPException(404,"Account not found.")
    user=db.get(User,profile.user_id)
    if not user or not user.active: raise HTTPException(403,"This account is inactive.")
    challenge=db.scalar(select(OTPChallenge).where(OTPChallenge.user_id==user.id,
        OTPChallenge.channel==channel,OTPChallenge.consumed==False).order_by(OTPChallenge.created_at.desc()))
    if not challenge: raise HTTPException(400,"Request a new OTP first.")
    now=datetime.now(timezone.utc); expiry=challenge.expires_at
    if expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
    if expiry < now: raise HTTPException(400,"OTP expired. Request a new one.")
    if challenge.attempts>=5: raise HTTPException(429,"Too many incorrect attempts. Request a new OTP.")

    challenge_id=challenge.id
    correct=hmac.compare_digest(challenge.code_hash,otp_digest(body.code))
    if not claim_otp_attempt(db,challenge,consume=correct):
        db.rollback()
        latest=db.get(OTPChallenge,challenge_id)
        if latest and latest.attempts>=5:
            raise HTTPException(429,"Too many incorrect attempts. Request a new OTP.")
        raise HTTPException(400,"Request a new OTP first.")
    if not correct:
        db.commit()
        raise HTTPException(400,"Incorrect OTP.")
    if channel=="email": profile.email_verified=True
    else: profile.phone_verified=True
    db.commit()
    pair=db.scalars(select(OTPChallenge).where(OTPChallenge.user_id==user.id,OTPChallenge.batch_id==challenge.batch_id)).all()
    if len(pair)==2 and all(item.consumed for item in pair):
        return {"verified":True,"both_channels_verified":True,"access_token":create_token(user),"token_type":"bearer",
                "user":{"id":user.id,"name":profile.full_name,"email":profile.email,"phone":profile.phone,"role":user.role.value}}
    return {"verified":True,"both_channels_verified":False,"next_channel":"phone" if channel=="email" else "email"}

@app.get("/admin/users")
def admin_users(db:Session=Depends(get_db),user:User=Depends(require_roles("SUPER_ADMIN"))):
    rows=db.scalars(select(User).where(User.institution_id==user.institution_id)).all()
    result=[]
    for item in rows:
        profile=db.scalar(select(UserProfile).where(UserProfile.user_id==item.id))
        result.append({"id":item.id,"name":profile.full_name if profile else item.username,
          "email":profile.email if profile else None,"phone":profile.phone if profile else None,
          "email_verified":profile.email_verified if profile else False,
          "phone_verified":profile.phone_verified if profile else False,"role":item.role.value,"active":item.active})
    return result

@app.post("/admin/users")
def admin_create_user(body:AdminUserCreateIn,db:Session=Depends(get_db),user:User=Depends(require_roles("SUPER_ADMIN"))):
    name=body.full_name.strip();email=body.email.strip().lower();phone=body.phone.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): raise HTTPException(422,"Enter a valid email address.")
    if not re.fullmatch(r"\+[1-9]\d{7,14}",phone): raise HTTPException(422,"Phone must use international format, e.g. +919876543210.")
    try: role=Role(body.role)
    except ValueError: raise HTTPException(422,"Invalid role.")
    if role==Role.SUPER_ADMIN and user.role!=Role.SUPER_ADMIN: raise HTTPException(403,"Only master admin can create administrators.")
    if db.scalar(select(UserProfile).where((UserProfile.email==email)|(UserProfile.phone==phone))):
        raise HTTPException(409,"Email or mobile number is already registered.")
    temporary=secrets.token_urlsafe(32)
    account=User(institution_id=user.institution_id,username=email,password_hash=hash_password(temporary),role=role)
    db.add(account);db.flush()
    db.add(UserProfile(user_id=account.id,full_name=name,email=email,phone=phone))
    audit(db,user,"USER_CREATED","user",account.id,{"email":email,"role":role.value})
    db.commit()
    return {"id":account.id,"name":name,"email":email,"phone":phone,"role":role.value,"message":"Account created. User must verify both email and mobile OTP to sign in."}

@app.patch("/admin/users/{user_id}/active")
def admin_set_user_active(user_id:str,active:bool,db:Session=Depends(get_db),user:User=Depends(require_roles("SUPER_ADMIN"))):
    target=db.get(User,user_id)
    if not target or target.institution_id!=user.institution_id: raise HTTPException(404,"User not found.")
    if target.id==user.id and not active: raise HTTPException(400,"Master admin cannot deactivate their own account.")
    target.active=active;audit(db,user,"USER_ACTIVATED" if active else "USER_DEACTIVATED","user",target.id);db.commit()
    return {"id":target.id,"active":target.active}

