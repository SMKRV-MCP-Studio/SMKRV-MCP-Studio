"""AdminUser model — single admin account for authentication."""

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AdminUser(TimestampMixin, Base):
    """Admin user for Studio authentication. Single-user, no RBAC."""

    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # --- 2FA (TOTP) ---
    totp_secret_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    recovery_codes_hash: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
