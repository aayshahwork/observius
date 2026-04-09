from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class TaskStep(Base):
    __tablename__ = "task_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("uuid_generate_v7()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    screenshot_s3_key: Mapped[str | None] = mapped_column(String)
    llm_tokens_in: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    llm_tokens_out: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    error_message: Mapped[str | None] = mapped_column(String)
    context: Mapped[dict | None] = mapped_column(JSONB)
    failure_class: Mapped[str | None] = mapped_column(String)
    patch_applied: Mapped[dict | None] = mapped_column(JSONB)
    validator_verdict: Mapped[str | None] = mapped_column(String)
    har_ref: Mapped[str | None] = mapped_column(String)
    trace_ref: Mapped[str | None] = mapped_column(String)
    video_ref: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(server_default=text("now()"))

    task: Mapped[Task] = relationship(back_populates="steps")


from .task import Task  # noqa: E402
