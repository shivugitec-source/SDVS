"""Initial SDVS relational schema.

This baseline matches the SQLAlchemy metadata that previously created the
application schema. Existing databases must pass the read-only schema parity
check before this revision is stamped.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "audit_events", "institutions", "candidates", "examinations", "users",
    "otp_challenges", "subjects", "user_profiles", "evaluation_scripts",
    "questions", "allocations", "evaluations", "identity_mappings", "question_marks",
)
_INDEXES = (
    ("ix_audit_events_actor_id", "audit_events", ("actor_id",), False),
    ("ix_audit_events_institution_id", "audit_events", ("institution_id",), False),
    ("ix_candidates_institution_id", "candidates", ("institution_id",), False),
    ("ix_examinations_institution_id", "examinations", ("institution_id",), False),
    ("ix_users_institution_id", "users", ("institution_id",), False),
    ("ix_users_username", "users", ("username",), False),
    ("ix_otp_challenges_batch_id", "otp_challenges", ("batch_id",), False),
    ("ix_otp_challenges_user_id", "otp_challenges", ("user_id",), False),
    ("ix_subjects_examination_id", "subjects", ("examination_id",), False),
    ("ix_user_profiles_email", "user_profiles", ("email",), True),
    ("ix_user_profiles_phone", "user_profiles", ("phone",), True),
    ("ix_user_profiles_user_id", "user_profiles", ("user_id",), True),
    ("ix_evaluation_scripts_anonymous_code", "evaluation_scripts", ("anonymous_code",), True),
    ("ix_evaluation_scripts_institution_id", "evaluation_scripts", ("institution_id",), False),
    ("ix_evaluation_scripts_subject_id", "evaluation_scripts", ("subject_id",), False),
    ("ix_questions_subject_id", "questions", ("subject_id",), False),
    ("ix_allocations_evaluator_id", "allocations", ("evaluator_id",), False),
    ("ix_allocations_script_id", "allocations", ("script_id",), False),
    ("ix_evaluations_evaluator_id", "evaluations", ("evaluator_id",), False),
    ("ix_evaluations_script_id", "evaluations", ("script_id",), False),
    ("ix_question_marks_evaluation_id", "question_marks", ("evaluation_id",), False),
)


def upgrade() -> None:
    op.create_table("audit_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("institution_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("institutions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("code"))
    op.create_table("candidates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("institution_id", sa.String(), nullable=False),
        sa.Column("register_number", sa.String(80), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("examinations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("institution_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("academic_year", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("institution_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("SUPER_ADMIN", "EXAM_CONTROLLER", "CHIEF_EXAMINER", "EVALUATOR", "MODERATOR", "AUDITOR", name="role"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("institution_id", "username"))
    op.create_table("otp_challenges",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("subjects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("examination_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("max_marks", sa.Numeric(8, 2), nullable=False),
        sa.ForeignKeyConstraint(["examination_id"], ["examinations.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("user_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("phone_verified", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("evaluation_scripts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("institution_id", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("anonymous_code", sa.String(40), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False),
        sa.Column("pdf_sha256", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("questions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("question_number", sa.String(30), nullable=False),
        sa.Column("max_marks", sa.Numeric(8, 2), nullable=False),
        sa.Column("rubric", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("subject_id", "question_number"))
    op.create_table("allocations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("script_id", sa.String(), nullable=False),
        sa.Column("evaluator_id", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["evaluator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["script_id"], ["evaluation_scripts.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("evaluations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("script_id", sa.String(), nullable=False),
        sa.Column("evaluator_id", sa.String(), nullable=False),
        sa.Column("status", sa.Enum("DRAFT", "SUBMITTED", "APPROVED", name="status"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_marks", sa.Numeric(8, 2), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["evaluator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["script_id"], ["evaluation_scripts.id"]),
        sa.PrimaryKeyConstraint("id"))
    op.create_table("identity_mappings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("candidate_id", sa.String(), nullable=False),
        sa.Column("script_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["script_id"], ["evaluation_scripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id"), sa.UniqueConstraint("script_id"))
    op.create_table("question_marks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("evaluation_id", sa.String(), nullable=False),
        sa.Column("question_id", sa.String(), nullable=False),
        sa.Column("marks", sa.Numeric(8, 2), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("private_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["evaluation_id"], ["evaluations.id"]),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evaluation_id", "question_id"))
    for name, table, columns, unique in _INDEXES:
        op.create_index(name, table, list(columns), unique=unique)


def downgrade() -> None:
    for name, table, _columns, _unique in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
    for table in reversed(_TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS status")
        op.execute("DROP TYPE IF EXISTS role")
