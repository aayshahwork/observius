from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("uuid_generate_v7()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    account: Mapped[Account] = relationship(back_populates="audit_logs")


from .account import Account  # noqa: E402
