"""Upload, file, and measurement endpoints."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db
from app.config import Settings
from app.exceptions import GeospatialParseError, UploadRejected
from app.models import StoredFeature, StoredFile
from app.schemas import (
    FeatureMeasurementRead,
    FileList,
    FileRead,
    MeasurementRead,
    MeasurementsRead,
)
from app.services.ingest import ingest_upload

router = APIRouter(prefix="/files", tags=["files"])

_UPLOAD_RESPONSES = {
    400: {"description": "The upload is empty, unsafe, or not a shapefile archive, KML, or KMZ file."},
    413: {"description": "The upload exceeds a size or feature limit."},
    422: {"description": "The file could not be parsed as geospatial data."},
}


@router.post("", status_code=201, response_model=FileRead, include_in_schema=False)
@router.post("/", status_code=201, response_model=FileRead, responses=_UPLOAD_RESPONSES)
async def upload_file(
    file: UploadFile = File(..., description="A .zip shapefile, a .kml file, or a .kmz file."),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> StoredFile:
    content = await _read_limited(file, settings.max_upload_bytes)
    try:
        return ingest_upload(db, settings, file.filename, content)
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except GeospatialParseError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc


@router.get("", response_model=FileList, include_in_schema=False)
@router.get("/", response_model=FileList)
def list_files(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> FileList:
    total = db.query(func.count(StoredFile.id)).scalar() or 0
    rows = (
        db.query(StoredFile)
        .order_by(StoredFile.created_at.desc(), StoredFile.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return FileList(count=total, limit=limit, offset=offset, files=[FileRead.model_validate(row) for row in rows])


@router.get("/{id}/", response_model=FileRead)
@router.get("/{id}", response_model=FileRead, include_in_schema=False)
def read_file(id: str, db: Session = Depends(get_db)) -> StoredFile:
    return _get_or_404(db, id)


@router.get("/{id}/measurements", response_model=MeasurementsRead, include_in_schema=False)
@router.get("/{id}/measurements/", response_model=MeasurementsRead)
def read_measurements(
    id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> MeasurementsRead:
    record = _get_or_404(db, id)
    rows = (
        db.query(StoredFeature)
        .filter(StoredFeature.file_id == record.id)
        .order_by(StoredFeature.feature_index)
        .offset(offset)
        .limit(limit)
        .all()
    )
    total_area = _sum(db, record.id, "area")
    total_length = _sum(db, record.id, "length")
    unsupported = (
        db.query(func.count(StoredFeature.id))
        .filter(StoredFeature.file_id == record.id, StoredFeature.measurement_supported.is_(False))
        .scalar()
        or 0
    )
    return MeasurementsRead(
        file_id=record.id,
        filename=record.filename,
        crs=record.crs,
        feature_count=record.feature_count,
        limit=limit,
        offset=offset,
        total_area_square_meters=total_area,
        total_length_meters=total_length,
        unsupported_count=unsupported,
        features=[_feature_payload(row) for row in rows],
    )


@router.delete("/{id}", status_code=204)
@router.delete("/{id}/", status_code=204, include_in_schema=False)
def delete_file(
    id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    record = _get_or_404(db, id)
    workspace = Path(record.stored_path).resolve().parent
    root = settings.upload_dir.resolve()
    db.delete(record)
    db.commit()
    if workspace.is_relative_to(root) and workspace != root:
        shutil.rmtree(workspace, ignore_errors=True)
    return Response(status_code=204)


def _get_or_404(db: Session, file_id: str) -> StoredFile:
    record = db.get(StoredFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


def _sum(db: Session, file_id: str, kind: str) -> float | None:
    total = (
        db.query(func.sum(StoredFeature.measurement_value))
        .filter(
            StoredFeature.file_id == file_id,
            StoredFeature.measurement_kind == kind,
            StoredFeature.measurement_supported.is_(True),
        )
        .scalar()
    )
    if total is None:
        return None
    return float(total)


def _feature_payload(row: StoredFeature) -> FeatureMeasurementRead:
    try:
        geometry = json.loads(row.geometry_json)
    except json.JSONDecodeError:
        geometry = None
    try:
        properties = json.loads(row.properties_json)
    except json.JSONDecodeError:
        properties = {}
    return FeatureMeasurementRead(
        feature_index=row.feature_index,
        feature_id=row.feature_id,
        geometry_type=row.geometry_type,
        geometry=geometry,
        crs=row.crs,
        properties=properties,
        measurement=MeasurementRead(
            supported=row.measurement_supported,
            kind=row.measurement_kind,
            value=row.measurement_value,
            unit=row.measurement_unit,
            measurement_crs=row.measurement_crs,
            note=row.measurement_message,
        ),
    )


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await upload.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"The file exceeds the {limit} byte upload limit.",
            )
        chunks.append(block)
    return b"".join(chunks)
