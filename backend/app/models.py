import enum,uuid
from datetime import datetime,timezone
from sqlalchemy import String,Integer,ForeignKey,DateTime,Boolean,Numeric,Text,Enum,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
def uid():return str(uuid.uuid4())
def now():return datetime.now(timezone.utc)
class Role(str,enum.Enum):
 SUPER_ADMIN="SUPER_ADMIN";EXAM_CONTROLLER="EXAM_CONTROLLER";CHIEF_EXAMINER="CHIEF_EXAMINER";EVALUATOR="EVALUATOR";MODERATOR="MODERATOR";AUDITOR="AUDITOR"
class Status(str,enum.Enum):
 DRAFT="DRAFT";SUBMITTED="SUBMITTED";APPROVED="APPROVED"
class Institution(Base):
 __tablename__="institutions"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);name:Mapped[str]=mapped_column(String(200));code:Mapped[str]=mapped_column(String(40),unique=True)
class User(Base):
 __tablename__="users"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);institution_id:Mapped[str]=mapped_column(ForeignKey("institutions.id"),index=True);username:Mapped[str]=mapped_column(String(120),index=True);password_hash:Mapped[str]=mapped_column(String(255));role:Mapped[Role]=mapped_column(Enum(Role));active:Mapped[bool]=mapped_column(Boolean,default=True)
 __table_args__=(UniqueConstraint("institution_id","username"),)
class Examination(Base):
 __tablename__="examinations"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);institution_id:Mapped[str]=mapped_column(ForeignKey("institutions.id"),index=True);name:Mapped[str]=mapped_column(String(200));academic_year:Mapped[str]=mapped_column(String(20));status:Mapped[str]=mapped_column(String(30),default="DRAFT")
class Subject(Base):
 __tablename__="subjects"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);examination_id:Mapped[str]=mapped_column(ForeignKey("examinations.id"),index=True);code:Mapped[str]=mapped_column(String(40));name:Mapped[str]=mapped_column(String(160));max_marks:Mapped[float]=mapped_column(Numeric(8,2))
class Question(Base):
 __tablename__="questions"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);subject_id:Mapped[str]=mapped_column(ForeignKey("subjects.id"),index=True);question_number:Mapped[str]=mapped_column(String(30));max_marks:Mapped[float]=mapped_column(Numeric(8,2));rubric:Mapped[str|None]=mapped_column(Text,nullable=True)
 __table_args__=(UniqueConstraint("subject_id","question_number"),)
class Candidate(Base):
 __tablename__="candidates"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);institution_id:Mapped[str]=mapped_column(ForeignKey("institutions.id"),index=True);register_number:Mapped[str]=mapped_column(String(80));full_name:Mapped[str]=mapped_column(String(200))
class EvaluationScript(Base):
 __tablename__="evaluation_scripts"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);institution_id:Mapped[str]=mapped_column(ForeignKey("institutions.id"),index=True);subject_id:Mapped[str]=mapped_column(ForeignKey("subjects.id"),index=True);anonymous_code:Mapped[str]=mapped_column(String(40),unique=True,index=True);object_key:Mapped[str]=mapped_column(String(500));pdf_sha256:Mapped[str]=mapped_column(String(64));page_count:Mapped[int]=mapped_column(Integer);status:Mapped[str]=mapped_column(String(30),default="UPLOADED")
class IdentityMapping(Base):
 __tablename__="identity_mappings"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);candidate_id:Mapped[str]=mapped_column(ForeignKey("candidates.id"),unique=True);script_id:Mapped[str]=mapped_column(ForeignKey("evaluation_scripts.id"),unique=True)
class Allocation(Base):
 __tablename__="allocations"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);script_id:Mapped[str]=mapped_column(ForeignKey("evaluation_scripts.id"),index=True);evaluator_id:Mapped[str]=mapped_column(ForeignKey("users.id"),index=True);assigned_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now);active:Mapped[bool]=mapped_column(Boolean,default=True)
class Evaluation(Base):
 __tablename__="evaluations"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);script_id:Mapped[str]=mapped_column(ForeignKey("evaluation_scripts.id"),index=True);evaluator_id:Mapped[str]=mapped_column(ForeignKey("users.id"),index=True);status:Mapped[Status]=mapped_column(Enum(Status),default=Status.DRAFT);version:Mapped[int]=mapped_column(Integer,default=1);submitted_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True);total_marks:Mapped[float|None]=mapped_column(Numeric(8,2),nullable=True);content_hash:Mapped[str|None]=mapped_column(String(64),nullable=True)
class QuestionMark(Base):
 __tablename__="question_marks"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);evaluation_id:Mapped[str]=mapped_column(ForeignKey("evaluations.id"),index=True);question_id:Mapped[str]=mapped_column(ForeignKey("questions.id"));marks:Mapped[float]=mapped_column(Numeric(8,2));remark:Mapped[str|None]=mapped_column(Text,nullable=True);private_note:Mapped[str|None]=mapped_column(Text,nullable=True)
 __table_args__=(UniqueConstraint("evaluation_id","question_id"),)
class AuditEvent(Base):
 __tablename__="audit_events"
 id:Mapped[str]=mapped_column(String,primary_key=True,default=uid);institution_id:Mapped[str]=mapped_column(String,index=True);actor_id:Mapped[str]=mapped_column(String,index=True);action:Mapped[str]=mapped_column(String(100));entity_type:Mapped[str]=mapped_column(String(80));entity_id:Mapped[str]=mapped_column(String(80));detail:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
