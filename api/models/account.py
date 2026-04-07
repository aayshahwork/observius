from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, String, Integer, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "tier IN ('free', 'startup', 'growth', 'enterprise')",
            name="accounts_tier_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("uuid_generate_v7()")
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str | None] = mapped_column(String, server_default=text("'free'"))
    stripe_customer_id: Mapped[str | None] = mapped_column(String, unique=True)
    monthly_step_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("500"))
    monthly_steps_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    encryption_key_id: Mapped[str] = mapped_column(String, nullable=False)
    webhook_secret: Mapped[str | None] = mapped_column(String(64))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(server_default=text("now()"))

    # Relationships
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="account")
    tasks: Mapped[list[Task]] = relationship(back_populates="account")
    sessions: Mapped[list[Session]] = relationship(back_populates="account")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="account")
    alerts: Mapped[list[Alert]] = relationship(back_populates="account")


# Resolve forward refs after all models are imported
from .api_key import ApiKey  # noqa: E402
from .task import Task  # noqa: E402
from .session import Session  # noqa: E402
from .audit_log import AuditLog  # noqa: E402
from .alert import Alert  # noqa: E402
