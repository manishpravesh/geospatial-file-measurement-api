"""API request and response models."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: Literal["COMPLETED"]
    file_type: Literal["shapefile", "kml", "kmz"]
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _as_utc(value).isoformat()


class FileList(BaseModel):
    count: int
    limit: int
    offset: int
    files: list[FileRead]


class MeasurementRead(BaseModel):
    supported: bool
    kind: Literal["area", "length"] | None = None
    value: float | None = None
    unit: Literal["square_meters", "meters"] | None = None
    measurement_crs: str | None = None
    note: str | None = None


class FeatureMeasurementRead(BaseModel):
    feature_index: int
    feature_id: str
    geometry_type: str
    geometry: dict | None
    crs: str | None
    properties: dict
    measurement: MeasurementRead


class MeasurementsRead(BaseModel):
    file_id: str
    filename: str
    crs: str | None
    feature_count: int
    limit: int
    offset: int
    total_area_square_meters: float | None = None
    total_length_meters: float | None = None
    unsupported_count: int = 0
    features: list[FeatureMeasurementRead] = Field(default_factory=list)
