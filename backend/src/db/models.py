import enum
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Enum as SAEnum, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.db.base import Base

class SpecStatus(str, enum.Enum):
    draft = "draft"
    confirmed = "confirmed"

class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"

class CheckStatus(str, enum.Enum):
    PROPERTY_HOLDS = "PROPERTY_HOLDS"
    PROPERTY_VIOLATED = "PROPERTY_VIOLATED"
    MODEL_INVALID = "MODEL_INVALID"
    TIMEOUT = "TIMEOUT"
    STATE_LIMIT_REACHED = "STATE_LIMIT_REACHED"
    ENGINE_ERROR = "ENGINE_ERROR"

class ArtifactKind(str, enum.Enum):
    pyspark = "pyspark"
    tla = "tla"
    cfg = "cfg"

class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    
    specifications: Mapped[list["SpecificationModel"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    messages: Mapped[list["MessageModel"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class SpecificationModel(Base):
    __tablename__ = "specifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[SpecStatus] = mapped_column(SAEnum(SpecStatus, name="spec_status"), nullable=False, default=SpecStatus.draft)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="unique_project_version"),
    )

    
    project: Mapped["ProjectModel"] = relationship(back_populates="specifications")
    artifacts: Mapped[list["ArtifactModel"]] = relationship(back_populates="specification", cascade="all, delete-orphan")
    verification_runs: Mapped[list["VerificationRunModel"]] = relationship(back_populates="specification", cascade="all, delete-orphan")


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole, name="message_role"), nullable=False, default=MessageRole.user)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_messages_project_time", "project_id", "created_at"),
    )

    
    project: Mapped["ProjectModel"] = relationship(back_populates="messages")


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spec_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("specifications.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[ArtifactKind] = mapped_column(SAEnum(ArtifactKind, name="artifact_kind"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[Text] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_artifacts_spec_kind", "spec_id", "kind"),
    )

    
    specification: Mapped["SpecificationModel"] = relationship(back_populates="artifacts")


class VerificationRunModel(Base):
    __tablename__ = "verification_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spec_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("specifications.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus, name="run_status"), nullable=False, default=RunStatus.queued)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_verification_runs_queue", "status", "queued_at"),
    )

    
    specification: Mapped["SpecificationModel"] = relationship(back_populates="verification_runs")
    property_results: Mapped[list["PropertyResultModel"]] = relationship(back_populates="verification_run", cascade="all, delete-orphan")


class PropertyResultModel(Base):
    __tablename__ = "property_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("verification_runs.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[CheckStatus] = mapped_column(SAEnum(CheckStatus, name="check_status"), nullable=False)
    counterexample: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "property_id", name="unique_run_property"),
    )

    
    verification_run: Mapped["VerificationRunModel"] = relationship(back_populates="property_results")