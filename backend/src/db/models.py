import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class SpecStatus(enum.StrEnum):
    draft = "DRAFT"
    confirmed = "APPROVED"


class RunStatus(enum.StrEnum):
    queued = "QUEUED"
    running = "RUNNING"
    completed = "FINISHED"


class CheckStatus(enum.StrEnum):
    PROPERTY_HOLDS = "PROPERTY_HOLDS"
    PROPERTY_VIOLATED = "PROPERTY_VIOLATED"
    MODEL_INVALID = "MODEL_INVALID"
    TIMEOUT = "TIMEOUT"
    STATE_LIMIT_REACHED = "STATE_LIMIT_REACHED"
    ENGINE_ERROR = "ENGINE_ERROR"


class ArtifactKind(enum.StrEnum):
    pyspark = "pyspark"
    tla = "tla"
    cfg = "cfg"
    generation_manifest = "generation_manifest"


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    analyst_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )

    specifications: Mapped[list["SpecificationModel"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    messages: Mapped[list["MessageModel"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class SpecificationModel(Base):
    __tablename__ = "specifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[SpecStatus] = mapped_column(Text, nullable=False, default=SpecStatus.draft)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )

    __table_args__ = (UniqueConstraint("project_id", "version", name="unique_project_version"),)

    project: Mapped["ProjectModel"] = relationship(back_populates="specifications")
    artifacts: Mapped[list["ArtifactModel"]] = relationship(
        back_populates="specification", cascade="all, delete-orphan"
    )
    verification_runs: Mapped[list["VerificationRunModel"]] = relationship(
        back_populates="specification", cascade="all, delete-orphan"
    )


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(Text, nullable=False, default=MessageRole.user)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )

    __table_args__ = (Index("idx_messages_project_time", "project_id", "created_at"),)

    project: Mapped["ProjectModel"] = relationship(back_populates="messages")


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("specifications.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ArtifactKind] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )

    __table_args__ = (
        Index("idx_artifacts_spec_kind", "spec_id", "kind"),
        UniqueConstraint("spec_id", "kind", "filename", name="unique_artifact_filename"),
    )

    specification: Mapped["SpecificationModel"] = relationship(back_populates="artifacts")


class VerificationRunModel(Base):
    __tablename__ = "verification_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("specifications.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(Text, nullable=False, default=RunStatus.queued)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_verification_runs_queue", "status", "queued_at"),)

    specification: Mapped["SpecificationModel"] = relationship(back_populates="verification_runs")
    property_results: Mapped[list["PropertyResultModel"]] = relationship(
        back_populates="verification_run", cascade="all, delete-orphan"
    )


class PropertyResultModel(Base):
    __tablename__ = "property_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("verification_runs.id", ondelete="CASCADE"), nullable=False
    )
    property_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[CheckStatus] = mapped_column(Text, nullable=False)
    counterexample: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("run_id", "property_id", name="unique_run_property"),)

    verification_run: Mapped["VerificationRunModel"] = relationship(
        back_populates="property_results"
    )
