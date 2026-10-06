"""Stored uploads and the features extracted from them."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StoredFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    features: Mapped[list["StoredFeature"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StoredFeature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("file_id", "feature_index", name="uq_feature_file_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_id: Mapped[str] = mapped_column(String(256), nullable=False)
    geometry_type: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry_json: Mapped[str] = mapped_column(Text, nullable=False)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties_json: Mapped[str] = mapped_column(Text, nullable=False)
    measurement_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    measurement_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    measurement_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    measurement_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measurement_crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    file: Mapped[StoredFile] = relationship(back_populates="features")
