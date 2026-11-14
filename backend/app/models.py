"""Relational data model (brief section N). Money/quantities use DecimalStr (exact)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, DecimalStr


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------- identity
class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # External AI is off unless an admin explicitly enables it (data-flow disclosure).
    allow_external_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=0)  # 0 = keep


class AiSettings(Base, TimestampMixin):
    """Per-organization AI provider configuration. The API key is stored encrypted (secretbox)."""
    __tablename__ = "ai_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="offline")  # offline/anthropic
    model: Mapped[str] = mapped_column(String(60), default="claude-opus-5-5")
    effort: Mapped[str] = mapped_column(String(10), default="medium")  # low/medium/high
    monthly_budget_usd: Mapped[object] = mapped_column(DecimalStr, default="10.00")
    max_pages_per_run: Mapped[int] = mapped_column(Integer, default=300)
    key_ciphertext: Mapped[str | None] = mapped_column(Text)
    key_hint: Mapped[str | None] = mapped_column(String(8))
    key_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(Text)


class AiUsage(Base):
    """One row per provider request: measured tokens and the cost at the cached list price."""
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model: Mapped[str] = mapped_column(String(60))
    pages: Mapped[str] = mapped_column(String(200), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[object | None] = mapped_column(DecimalStr)
    outcome: Mapped[str] = mapped_column(String(30))  # ok/refusal/truncated/error/budget_stop
    message: Mapped[str | None] = mapped_column(Text)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    discarded: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(100))


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


ROLES = (
    "org_admin", "tender_manager", "technical_reviewer", "estimator",
    "document_coordinator", "finance_reviewer", "authorized_approver", "auditor",
)


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(40))


# ---------------------------------------------------------------- bidders
class LegalEntity(Base, TimestampMixin):
    __tablename__ = "legal_entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    legal_name: Mapped[str] = mapped_column(String(300))
    entity_type: Mapped[str] = mapped_column(String(60), default="company")  # company/partnership/proprietorship/individual
    registered_address: Mapped[str] = mapped_column(Text, default="")
    pan: Mapped[str | None] = mapped_column(String(20))
    gstin: Mapped[str | None] = mapped_column(String(20))
    signatory_name: Mapped[str | None] = mapped_column(String(200))
    signatory_designation: Mapped[str | None] = mapped_column(String(200))
    contacts: Mapped[dict] = mapped_column(JSON, default=dict)
    registrations: Mapped[dict] = mapped_column(JSON, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class JV(Base, TimestampMixin):
    __tablename__ = "jvs"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"))
    name: Mapped[str] = mapped_column(String(300))
    members: Mapped[list["JVMember"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class JVMember(Base):
    __tablename__ = "jv_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    jv_id: Mapped[int] = mapped_column(ForeignKey("jvs.id"))
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    role: Mapped[str] = mapped_column(String(20), default="member")  # lead/member
    share_pct: Mapped[object] = mapped_column(DecimalStr, default="0")


# ---------------------------------------------------------------- tenders
WORKFLOW_STEPS = [
    "create", "upload", "verify_extraction", "review_clauses", "choose_bidder", "eligibility",
    "collect_evidence", "technical", "estimate", "generate_forms", "sign_certify", "verify_returned",
    "assemble_envelopes", "preflight", "export", "receipt",
]


class Tender(Base, TimestampMixin):
    __tablename__ = "tenders"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    title: Mapped[str] = mapped_column(String(400))
    tender_ref: Mapped[str] = mapped_column(String(200), default="")
    department: Mapped[str] = mapped_column(String(300), default="")
    portal_url: Mapped[str] = mapped_column(String(500), default="")
    bidder_type: Mapped[str] = mapped_column(String(10), default="entity")  # entity/jv
    bidder_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))
    jv_id: Mapped[int | None] = mapped_column(ForeignKey("jvs.id", use_alter=True))
    deadline_raw: Mapped[str | None] = mapped_column(String(200))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_tz: Mapped[str] = mapped_column(String(60), default="Asia/Kolkata")
    deadline_source: Mapped[str | None] = mapped_column(String(300))
    qualification_cutoff: Mapped[date | None] = mapped_column(Date)
    qualification_cutoff_source: Mapped[str | None] = mapped_column(String(300))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    step_state: Mapped[dict] = mapped_column(JSON, default=dict)  # step -> {status, owner_id, due, note}
    status: Mapped[str] = mapped_column(String(30), default="active")
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency
    __mapper_args__ = {"version_id_col": row_version}


class TenderVersion(Base, TimestampMixin):
    __tablename__ = "tender_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))


DOC_CLASSES = (
    "nit", "itb", "bds", "sbd", "gcc", "scc", "specification", "schedule", "drawing",
    "qualification", "boq", "forms", "addendum", "corrigendum", "prebid_reply", "other", "unclassified",
)


class SourceDocument(Base, TimestampMixin):
    __tablename__ = "source_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    filename: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(20))  # pdf/docx/xlsx/xls/csv/image/zip
    doc_class: Mapped[str] = mapped_column(String(30), default="unclassified")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="uploaded")  # uploaded/processing/processed/failed/needs_password/needs_classification/rejected/container
    status_reason: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_tender_version: Mapped[int] = mapped_column(Integer, default=1)
    precedence_rank: Mapped[int | None] = mapped_column(Integer)
    # Legacy .doc/.xls: the original upload (storage_key, sha256) is never modified; the LibreOffice
    # output is stored separately and only used for reading text.
    converted_storage_key: Mapped[str | None] = mapped_column(String(300))
    converted_kind: Mapped[str | None] = mapped_column(String(20))
    converted_by: Mapped[str | None] = mapped_column(String(80))


class SourcePage(Base):
    __tablename__ = "source_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_index"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
    page_index: Mapped[int] = mapped_column(Integer)  # 0-based PDF index
    page_label: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/processed/failed/unreadable/duplicate/excluded
    reason: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    text_source: Mapped[str | None] = mapped_column(String(20))  # text_layer/ocr/docx/xlsx/csv
    confidence: Mapped[float | None] = mapped_column(Float)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)


class ExtractionJob(Base, TimestampMixin):
    __tablename__ = "extraction_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    kind: Mapped[str] = mapped_column(String(30))  # ingest_document / extract_requirements
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued/running/succeeded/failed/cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceAnchor(Base, TimestampMixin):
    __tablename__ = "source_anchors"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
    doc_sha256: Mapped[str] = mapped_column(String(64))
    doc_version: Mapped[int] = mapped_column(Integer)
    page_index: Mapped[int] = mapped_column(Integer)
    page_label: Mapped[str | None] = mapped_column(String(40))
    clause_no: Mapped[str | None] = mapped_column(String(60))
    excerpt: Mapped[str] = mapped_column(Text)
    bbox: Mapped[list | None] = mapped_column(JSON)  # [x0,y0,x1,y1] in PDF points


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("tender_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))  # logical identity, e.g. "fin.avg_turnover"
    category: Mapped[str] = mapped_column(String(40))  # deadline/financial/experience/document/commercial/technical/envelope
    title: Mapped[str] = mapped_column(String(400))
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    has_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    versions: Mapped[list["RequirementVersion"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="RequirementVersion.version_no")


class RequirementVersion(Base, TimestampMixin):
    __tablename__ = "requirement_versions"
    __table_args__ = (UniqueConstraint("requirement_id", "version_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    tender_version: Mapped[int] = mapped_column(Integer, default=1)
    nature: Mapped[str] = mapped_column(String(20), default="extracted")  # extracted/derived/interpretation/question
    value: Mapped[dict] = mapped_column(JSON, default=dict)  # structured value (amounts as strings)
    value_text: Mapped[str] = mapped_column(Text, default="")
    anchor_id: Mapped[int | None] = mapped_column(ForeignKey("source_anchors.id"))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extractor: Mapped[str] = mapped_column(String(40), default="heuristic-v1")
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")  # unreviewed/approved/rejected/needs_review
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    amendment_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    anchor: Mapped[SourceAnchor | None] = relationship(lazy="joined")


class EligibilityRule(Base, TimestampMixin):
    __tablename__ = "eligibility_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id"))
    requirement_version_no: Mapped[int | None] = mapped_column(Integer)
    rule_type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    engine_version: Mapped[str] = mapped_column(String(20), default="rules-1.0")
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed/approved/rejected/superseded
    material: Mapped[bool] = mapped_column(Boolean, default=True)
    proposed_by: Mapped[str] = mapped_column(String(40), default="heuristic-v1")
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    explanation: Mapped[str] = mapped_column(Text, default="")
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)


EVIDENCE_CATEGORIES = (
    "incorporation", "registration", "audited_accounts", "turnover_certificate", "net_worth",
    "solvency", "bank_facility", "existing_commitments", "work_order", "agreement",
    "completion_certificate", "performance_certificate", "executed_quantity", "project_scope", "machinery", "personnel",
    "oem_authorization", "datasheet", "quality_certification", "declaration", "power_of_attorney",
    "pan", "gst", "emd_proof", "fee_proof", "other",
)


class EvidenceRecord(Base, TimestampMixin):
    __tablename__ = "evidence_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(400))
    issuer: Mapped[str | None] = mapped_column(String(300))
    subject_name_on_document: Mapped[str | None] = mapped_column(String(300))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    storage_key: Mapped[str | None] = mapped_column(String(300))
    filename: Mapped[str | None] = mapped_column(String(400))
    sha256: Mapped[str | None] = mapped_column(String(64))
    fields: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"fy":"2023-24","amount":"..."}
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    verification_state: Mapped[str] = mapped_column(String(20), default="unverified")  # unverified/verified/rejected
    issues: Mapped[list] = mapped_column(JSON, default=list)  # missing pages, illegible, name conflict...
    source_page: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_records.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class EvidenceDerivative(Base, TimestampMixin):
    """A cleaned copy of a vault scan (rotated / cropped / straightened page). Added in update 0004.
    The evidence record, its file and its hash are never changed; this is a separate file a user may download
    or, if they choose, upload as a new version through the normal vault versioning."""
    __tablename__ = "evidence_derivatives"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence_records.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="image_edit")
    page_index: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class EvidenceLink(Base, TimestampMixin):
    __tablename__ = "evidence_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence_records.id"))
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("checklist_tasks.id"))
    attribution_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))
    attribution_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    attribution_note: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed/accepted/rejected
    # "task" (checklist), "attribution" (name differs) or "similarity" (reviewer: this work is / is not similar
    # for the criterion `criterion_key` of this tender: accepted = similar, rejected = not similar).
    purpose: Mapped[str | None] = mapped_column(String(20), default="task")
    criterion_key: Mapped[str | None] = mapped_column(String(120))


class Evaluation(Base, TimestampMixin):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("eligibility_rules.id"))
    rule_version: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(100), default="bidder")  # bidder / jv:aggregate / entity:<id>
    result: Mapped[str] = mapped_column(String(30))  # pass/fail/insufficient_evidence/ambiguous/not_applicable
    explanation: Mapped[str] = mapped_column(Text, default="")
    calculation: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_inputs: Mapped[list] = mapped_column(JSON, default=list)
    remedial_action: Mapped[str | None] = mapped_column(Text)
    inputs_hash: Mapped[str] = mapped_column(String(64))
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_reason: Mapped[str | None] = mapped_column(Text)
    override_result: Mapped[str | None] = mapped_column(String(30))
    override_rationale: Mapped[str | None] = mapped_column(Text)
    override_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ChecklistTask(Base, TimestampMixin):
    __tablename__ = "checklist_tasks"
    __table_args__ = (UniqueConstraint("tender_id", "task_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    task_key: Mapped[str] = mapped_column(String(160))  # idempotent identity
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id"))
    title: Mapped[str] = mapped_column(String(400))
    question: Mapped[str | None] = mapped_column(Text)
    group: Mapped[str] = mapped_column(String(40), default="bidder")  # bidder/jv_member/specialist/technical/financial/fee_security/originals
    action_type: Mapped[str] = mapped_column(String(30), default="upload_existing")
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    condition: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("document_templates.id"))
    evidence_category: Mapped[str | None] = mapped_column(String(40))
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_records.id"))
    signing_required: Mapped[bool] = mapped_column(Boolean, default=False)
    certification_required: Mapped[str | None] = mapped_column(String(60))  # CA/bank/notary/OEM/department
    envelope: Mapped[str] = mapped_column(String(30), default="technical")
    slot_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="open")  # open/in_progress/submitted/accepted/rejected/not_applicable
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    history: Mapped[list] = mapped_column(JSON, default=list)


class DocumentTemplate(Base, TimestampMixin):
    __tablename__ = "document_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"))
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20))  # docx/builtin
    builtin_key: Mapped[str | None] = mapped_column(String(60))
    storage_key: Mapped[str | None] = mapped_column(String(300))
    placeholders: Mapped[list] = mapped_column(JSON, default=list)
    envelope: Mapped[str] = mapped_column(String(30), default="technical")
    from_tender_form: Mapped[bool] = mapped_column(Boolean, default=False)


class FieldMapping(Base, TimestampMixin):
    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("template_id", "tender_id", "placeholder"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("document_templates.id"))
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    placeholder: Mapped[str] = mapped_column(String(200))
    source_path: Mapped[str | None] = mapped_column(String(200))  # e.g. "entity.legal_name"
    manual_value: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


DOC_STATUSES = ("draft", "awaiting_signature", "awaiting_certification", "signed_upload_received", "verified")


class GeneratedDocument(Base, TimestampMixin):
    __tablename__ = "generated_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("document_templates.id"))
    name: Mapped[str] = mapped_column(String(300))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    docx_key: Mapped[str | None] = mapped_column(String(300))
    pdf_key: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    qa_issues: Mapped[list] = mapped_column(JSON, default=list)
    upstream: Mapped[dict] = mapped_column(JSON, default=dict)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    envelope: Mapped[str] = mapped_column(String(30), default="technical")
    signing_required: Mapped[bool] = mapped_column(Boolean, default=True)
    certification_required: Mapped[str | None] = mapped_column(String(60))
    signed_key: Mapped[str | None] = mapped_column(String(300))
    signed_sha256: Mapped[str | None] = mapped_column(String(64))
    signed_has_digital_signature: Mapped[bool] = mapped_column(Boolean, default=False)


class TechnicalResponse(Base, TimestampMixin):
    __tablename__ = "technical_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id"))
    clause_ref: Mapped[str] = mapped_column(String(100), default="")
    requirement_text: Mapped[str] = mapped_column(Text, default="")
    response: Mapped[str] = mapped_column(Text, default="")
    compliance: Mapped[str] = mapped_column(String(20), default="pending")  # complies/deviation/exclusion/not_applicable/pending
    deviation_note: Mapped[str | None] = mapped_column(Text)
    make_model: Mapped[str | None] = mapped_column(String(300))
    capacity: Mapped[str | None] = mapped_column(String(100))
    unit: Mapped[str | None] = mapped_column(String(40))
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_records.id"))
    needs_engineering_review: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class BOQ(Base, TimestampMixin):
    __tablename__ = "boqs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    original_key: Mapped[str] = mapped_column(String(300))
    original_sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(400))
    sheet_name: Mapped[str] = mapped_column(String(200))
    header_row: Mapped[int] = mapped_column(Integer)
    columns: Mapped[dict] = mapped_column(JSON, default=dict)  # {"code":"A","desc":"B","qty":"C","unit":"D","rate":"E","amount":"F"}
    mode: Mapped[str] = mapped_column(String(20), default="item_rate")  # item_rate/percentage/lump_sum
    rounding_places: Mapped[int] = mapped_column(Integer, default=2)
    roundtrip_supported: Mapped[bool] = mapped_column(Boolean, default=True)
    roundtrip_issues: Mapped[list] = mapped_column(JSON, default=list)
    flags: Mapped[dict] = mapped_column(JSON, default=dict)  # macros/protection/external_links
    items: Mapped[list["BOQItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="BOQItem.row_index")


class BOQItem(Base):
    __tablename__ = "boq_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    boq_id: Mapped[int] = mapped_column(ForeignKey("boqs.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    item_code: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(40), default="")
    quantity: Mapped[object] = mapped_column(DecimalStr, default="0")
    rate_cell: Mapped[str] = mapped_column(String(20))
    amount_cell: Mapped[str | None] = mapped_column(String(20))
    rate_cell_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    flags: Mapped[list] = mapped_column(JSON, default=list)  # duplicate/unit_inconsistent/qty_missing
    trade: Mapped[str] = mapped_column(String(40), default="civil")


class RateSource(Base, TimestampMixin):
    __tablename__ = "rate_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"))
    resource: Mapped[str] = mapped_column(String(300))
    resource_type: Mapped[str] = mapped_column(String(20), default="material")  # material/labour/equipment/transport/subcontract
    specification: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(40))
    rate: Mapped[object] = mapped_column(DecimalStr)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    tax_basis: Mapped[str] = mapped_column(String(30), default="excl_gst")
    gst_pct: Mapped[object] = mapped_column(DecimalStr, default="0")
    gst_recoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    freight_basis: Mapped[str] = mapped_column(String(30), default="ex_works")
    source_type: Mapped[str] = mapped_column(String(30))  # quotation/public_reference/assumption/user_input
    source_url: Mapped[str | None] = mapped_column(String(500))
    supplier: Mapped[str | None] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(200))
    quote_date: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    storage_key: Mapped[str | None] = mapped_column(String(300))


class RateAnalysis(Base, TimestampMixin):
    __tablename__ = "rate_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    boq_item_id: Mapped[int] = mapped_column(ForeignKey("boq_items.id"), unique=True)
    output_qty: Mapped[object] = mapped_column(DecimalStr, default="1")  # analysis basis quantity
    components: Mapped[list] = mapped_column(JSON, default=list)
    overheads: Mapped[dict] = mapped_column(JSON, default=dict)  # pct values as strings
    margin_pct: Mapped[object] = mapped_column(DecimalStr, default="0")
    margin_mode: Mapped[str] = mapped_column(String(20), default="markup_on_cost")  # markup_on_cost/margin_on_sales
    direct_rate: Mapped[object | None] = mapped_column(DecimalStr)  # user-entered rate when no analysis
    computed_rate: Mapped[object | None] = mapped_column(DecimalStr)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class QuoteVersion(Base, TimestampMixin):
    __tablename__ = "quote_versions"
    __table_args__ = (UniqueConstraint("tender_id", "version_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    boq_id: Mapped[int] = mapped_column(ForeignKey("boqs.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/estimator_reviewed/approved/invalidated
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    total: Mapped[object] = mapped_column(DecimalStr, default="0")
    total_words: Mapped[str] = mapped_column(Text, default="")
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    invalidated_reason: Mapped[str | None] = mapped_column(Text)
    export_key: Mapped[str | None] = mapped_column(String(300))
    export_report: Mapped[dict] = mapped_column(JSON, default=dict)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": row_version}


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(40))  # quote/package/rule/document
    subject_id: Mapped[int] = mapped_column(Integer)
    subject_hash: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(30))  # estimator_review/approval/package_approval
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))  # approved/rejected
    comment: Mapped[str | None] = mapped_column(Text)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text)


class EnvelopeProfile(Base, TimestampMixin):
    __tablename__ = "envelope_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), unique=True)
    envelopes: Mapped[list] = mapped_column(JSON, default=list)  # ["fee_emd","technical","financial"]
    zip_accepted: Mapped[bool | None] = mapped_column(Boolean)  # None = unknown -> never assume
    source_note: Mapped[str | None] = mapped_column(Text)
    portal_name: Mapped[str | None] = mapped_column(String(200))


class UploadSlot(Base, TimestampMixin):
    __tablename__ = "upload_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    envelope: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(300))
    allowed_types: Mapped[list] = mapped_column(JSON, default=lambda: ["pdf"])
    max_size_mb: Mapped[object | None] = mapped_column(DecimalStr)
    max_count: Mapped[int] = mapped_column(Integer, default=1)
    naming: Mapped[str | None] = mapped_column(String(200))
    signature_required: Mapped[bool] = mapped_column(Boolean, default=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    assignments: Mapped[list] = mapped_column(JSON, default=list)  # [{"type":"generated","id":1}|{"type":"evidence","id":2}|{"type":"quote_export","id":3}]


class ExportPackage(Base, TimestampMixin):
    __tablename__ = "export_packages"
    __table_args__ = (UniqueConstraint("tender_id", "revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/final_ready/frozen/superseded
    preflight: Mapped[dict] = mapped_column(JSON, default=dict)
    upstream: Mapped[dict] = mapped_column(JSON, default=dict)
    package_hash: Mapped[str | None] = mapped_column(String(64))
    zip_key: Mapped[str | None] = mapped_column(String(300))
    guide_key: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    entries: Mapped[list["ExportManifest"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class ExportManifest(Base):
    __tablename__ = "export_manifest"
    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("export_packages.id"), index=True)
    filename: Mapped[str] = mapped_column(String(400))
    slot: Mapped[str | None] = mapped_column(String(300))
    envelope: Mapped[str] = mapped_column(String(30))
    size: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(300))
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    signature_status: Mapped[str] = mapped_column(String(40), default="not_required")
    preparer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    internal_only: Mapped[bool] = mapped_column(Boolean, default=False)


class SubmissionReceipt(Base, TimestampMixin):
    __tablename__ = "submission_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("export_packages.id"))
    receipt_identifier: Mapped[str] = mapped_column(String(200))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"))
    message: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    tender_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(80))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # never document text, passwords or secrets
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
