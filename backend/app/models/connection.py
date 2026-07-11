"""Connection model — database connection configurations."""

import uuid

from sqlalchemy import JSON, Boolean, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Connection(TimestampMixin, Base):
    """Database connection configuration."""

    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(
            "db_type IN ('postgresql', 'clickhouse', 'mysql', 'cassandra', "
            "'greenplum', 'supabase', 'snowflake', 'bigquery', 'mssql')",
            name="ck_connections_db_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    db_type: Mapped[str] = mapped_column(String(30), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=5432)
    database: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    ssl_mode: Mapped[str] = mapped_column(String(20), default="prefer")
    pool_min_size: Mapped[int] = mapped_column(Integer, default=2)
    pool_max_size: Mapped[int] = mapped_column(Integer, default=10)
    extra_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Optimistic locking version (OL-2)
    version: Mapped[int] = mapped_column(Integer, default=1)

    # Queue / rate-limiting settings
    max_concurrent_queries: Mapped[int] = mapped_column(Integer, default=5)
    queue_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    queue_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tools = relationship("Tool", back_populates="connection", cascade="all, delete-orphan")
    resources = relationship(
        "Resource", back_populates="connection", cascade="all, delete-orphan"
    )
