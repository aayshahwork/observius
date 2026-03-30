from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'timeout', 'cancelled')",
            name="tasks_status_check",
        ),
        CheckConstraint(
            "length(task_description) <= 2000",
            name="tasks_description_length_check",
        ),
        CheckConstraint(
            "max_cost_cents > 0 OR max_cost_cents IS NULL",
            name="tasks_max_cost_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("uuid_generate_v7()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    status: Mapped[str | None] = mapped_column(String)
    success: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    url: Mapped[str] = mapped_column(String, nullable=False)
    task_description: Mapped[str] = mapped_column(String, nullable=False)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    model_used: Mapped[str | None] = mapped_column(String)
    total_steps: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    total_tokens_in: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    total_tokens_out: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    cost_cents: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), server_default=text("0"))
    max_cost_cents: Mapped[int | None] = mapped_column(Integer)
    cumulative_cost_cents: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), server_default=text("0"))
    replay_s3_key: Mapped[str | None] = mapped_column(String)
    session_id: Mapped[uuid.UUID | None] = mapped_column()
    idempotency_key: Mapped[str | None] = mapped_column(String)
    webhook_url: Mapped[str | None] = mapped_column(String)
    webhook_delivered: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    worker_id: Mapped[str | None] = mapped_column(String)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    retry_of_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    error_category: Mapped[str | None] = mapped_column(String(50))
    executor_mode: Mapped[str | None] = mapped_column(String(20), server_default=text("'browser_use'"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    account: Mapped[Account] = relationship(back_populates="tasks")
    steps: Mapped[list[TaskStep]] = relationship(back_populates="task")


from .account import Account  # noqa: E402
from .task_step import TaskStep  # noqa: E402
