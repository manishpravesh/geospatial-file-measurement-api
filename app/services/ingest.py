"""Validate an upload, read its features, measure them, and store the result."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import UploadRejected
from app.models import StoredFeature, StoredFile
from app.services.kml import parse_kml_bytes
from app.services.projection import measure_geometry
from app.services.serialize import choose_feature_id, geometry_document
from app.services.shapefile import read_archive

logger = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = {".zip", ".kml", ".kmz"}


def ingest_upload(db: Session, settings: Settings, filename: str | None, content: bytes) -> StoredFile:
    safe_name = safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    _validate_content(suffix, content, settings)

    file_id = str(uuid.uuid4())
    root = settings.upload_dir.resolve()
    workspace = (root / file_id).resolve()
    if not workspace.is_relative_to(root):
        raise UploadRejected("Could not create a storage directory for this upload.")
    workspace.mkdir(parents=True, exist_ok=False)
    stored_path = workspace / safe_name

    try:
        stored_path.write_bytes(content)
        dataset = _parse(suffix, content, workspace, settings)
        if len(dataset.features) > settings.max_features:
            raise UploadRejected(
                f"The file has {len(dataset.features)} features, which exceeds the limit of "
                f"{settings.max_features}.",
                status_code=413,
            )
        record = StoredFile(
            id=file_id,
            filename=safe_name,
            file_type=dataset.file_type,
            stored_path=str(stored_path),
            feature_count=len(dataset.features),
            crs=dataset.crs_label,
            status="COMPLETED",
        )
        db.add(record)
        for index, feature in enumerate(dataset.features):
            measurement = measure_geometry(
                feature.geometry,
                dataset.crs,
                segment_length_degrees=settings.segment_length_degrees,
                read_error=feature.read_error,
            )
            db.add(
                StoredFeature(
                    file_id=file_id,
                    feature_index=index,
                    feature_id=choose_feature_id(feature.properties, index, feature.source_fid),
                    geometry_type=feature.geometry_type,
                    geometry_json=json.dumps(geometry_document(feature.geometry)),
                    crs=dataset.crs_label,
                    properties_json=json.dumps(feature.properties),
                    measurement_supported=measurement.supported,
                    measurement_kind=measurement.kind,
                    measurement_value=measurement.value,
                    measurement_unit=measurement.unit,
                    measurement_crs=measurement.measurement_crs,
                    measurement_message=measurement.note,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    db.refresh(record)
    logger.info("Processed %s (%s features, crs=%s)", safe_name, record.feature_count, record.crs)
    return record


def safe_filename(filename: str | None) -> str:
    if not filename or "\x00" in filename:
        raise UploadRejected("The uploaded file needs a filename.")
    base = Path(filename).name.strip()
    if base in {"", ".", ".."}:
        raise UploadRejected("The uploaded file needs a filename.")
    if len(base) > 255:
        raise UploadRejected("The filename is too long.")
    return base


def _validate_content(suffix: str, content: bytes, settings: Settings) -> None:
    if suffix not in _ALLOWED_SUFFIXES:
        raise UploadRejected("Upload a .zip shapefile archive, a .kml file, or a .kmz file.")
    if len(content) == 0:
        raise UploadRejected("The uploaded file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise UploadRejected(
            f"The file exceeds the {settings.max_upload_bytes} byte upload limit.",
            status_code=413,
        )
    if suffix == ".kml" and content[:2] == b"PK":
        raise UploadRejected(
            "This .kml upload is a zip archive. Upload it as .kmz, or upload the .kml file inside it."
        )
    if suffix in {".zip", ".kmz"} and content[:2] != b"PK":
        raise UploadRejected("The file does not look like a zip archive.")


def _parse(suffix: str, content: bytes, workspace: Path, settings: Settings):
    if suffix == ".kml":
        return parse_kml_bytes(content)
    return read_archive(content, workspace / "extracted", settings)
