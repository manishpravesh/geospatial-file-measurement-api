"""Read a zipped shapefile, or a KMZ archive, into parsed features."""

from __future__ import annotations

import io
import math
from pathlib import Path
from zipfile import BadZipFile, ZipFile, ZipInfo

import geopandas as gpd
from pyproj import CRS
from pyproj.exceptions import CRSError

from app.config import Settings
from app.exceptions import GeospatialParseError, UploadRejected
from app.services.kml import parse_kml_bytes
from app.services.parsed import ParsedDataset, ParsedFeature
from app.services.projection import format_crs
from app.services.serialize import properties_document


def read_archive(content: bytes, destination: Path, settings: Settings) -> ParsedDataset:
    """Extract a shapefile zip or KMZ archive and read the single dataset inside it."""

    destination.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            _validate_archive(archive, destination, settings)
            for info in archive.infolist():
                if info.is_dir():
                    continue
                archive.extract(info, destination)
    except BadZipFile as exc:
        raise UploadRejected("The .zip archive could not be opened.") from exc

    shapefiles = _find(destination, ".shp")
    kml_files = _find(destination, ".kml")
    if shapefiles and kml_files:
        raise UploadRejected(
            "The archive contains both a shapefile and a KML file. Upload one dataset at a time."
        )
    if kml_files and not shapefiles:
        if len(kml_files) != 1:
            raise UploadRejected("The archive contains more than one KML file.")
        dataset = parse_kml_bytes(kml_files[0].read_bytes())
        dataset.file_type = "kmz"
        return dataset
    if not shapefiles:
        raise UploadRejected("The .zip archive does not contain a shapefile (.shp).")
    if len(shapefiles) != 1:
        raise UploadRejected(
            "The archive contains more than one shapefile. Upload one shapefile per archive."
        )
    return _read_shapefile(shapefiles[0])


def _read_shapefile(shp_path: Path) -> ParsedDataset:
    missing = [suffix for suffix in (".shx", ".dbf") if _sibling(shp_path, suffix) is None]
    if missing:
        joined = ", ".join(missing)
        raise GeospatialParseError(f"The shapefile is missing required sidecar files: {joined}.")

    frame = _read_frame(shp_path)
    crs = _coerce_crs(frame.crs)
    geometry_name = frame.geometry.name
    attributes = frame.drop(columns=geometry_name)
    fids = _fids(frame)
    features: list[ParsedFeature] = []
    for index, geometry in enumerate(frame.geometry):
        properties = properties_document(attributes.iloc[index].to_dict())
        parsed_geometry = _as_geometry(geometry)
        source_fid = fids[index] if index < len(fids) else None
        if parsed_geometry is None:
            features.append(
                ParsedFeature(
                    geometry=None,
                    geometry_type="None",
                    properties=properties,
                    read_error="Feature has no geometry.",
                    source_fid=source_fid,
                )
            )
            continue
        features.append(
            ParsedFeature(
                geometry=parsed_geometry,
                geometry_type=parsed_geometry.geom_type,
                properties=properties,
                source_fid=source_fid,
            )
        )
    return ParsedDataset(
        crs=crs,
        crs_label=format_crs(crs),
        features=features,
        file_type="shapefile",
    )


def _read_frame(shp_path: Path):
    encoding = _read_cpg(shp_path)
    candidates: list[str | None] = []
    if encoding:
        candidates.append(encoding)
    candidates.extend(["utf-8", "latin-1"])
    errors: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return gpd.read_file(shp_path, encoding=candidate, fid_as_index=True)
        except TypeError:
            try:
                return gpd.read_file(shp_path, encoding=candidate)
            except Exception as exc:  # GDAL/pyogrio raises a variety of runtime errors.
                errors.append(f"{candidate}: {exc}")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    detail = " | ".join(errors) if errors else "unknown read error"
    raise GeospatialParseError(f"The shapefile could not be read. {detail}")


def _fids(frame) -> list[str | None]:
    if frame.index is None:
        return []
    values: list[str | None] = []
    for value in frame.index:
        if value is None:
            values.append(None)
            continue
        if isinstance(value, float) and math.isnan(value):
            values.append(None)
            continue
        values.append(str(value))
    return values


def _as_geometry(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "geom_type"):
        return value
    return None


def _coerce_crs(value) -> CRS | None:
    if value is None:
        return None
    if isinstance(value, CRS):
        return value
    try:
        return CRS.from_user_input(value)
    except CRSError:
        return None


def _read_cpg(shp_path: Path) -> str | None:
    cpg = _sibling(shp_path, ".cpg")
    if cpg is None:
        return None
    text = cpg.read_text(encoding="ascii", errors="ignore").strip()
    return text or None


def _sibling(shp_path: Path, suffix: str) -> Path | None:
    for candidate in shp_path.parent.glob(shp_path.stem + ".*"):
        if candidate.suffix.lower() == suffix:
            return candidate
    return None


def _find(root: Path, suffix: str) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != suffix:
            continue
        if "__MACOSX" in path.parts or path.name.startswith("._"):
            continue
        found.append(path)
    return found


def _validate_archive(archive: ZipFile, destination: Path, settings: Settings) -> None:
    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > settings.max_zip_files:
        raise UploadRejected(
            f"The archive has more than {settings.max_zip_files} files.",
            status_code=413,
        )
    total = 0
    root = destination.resolve()
    for info in members:
        name = info.filename.replace("\\", "/")
        if not name or name.startswith("/") or ":" in name:
            raise UploadRejected("The archive contains an unsafe path.")
        parts = Path(name).parts
        if any(part in {"..", ""} for part in parts):
            raise UploadRejected("The archive contains an unsafe path.")
        target = (destination / name).resolve()
        if not target.is_relative_to(root):
            raise UploadRejected("The archive contains an unsafe path.")
        if _is_symlink(info):
            raise UploadRejected("The archive contains a symbolic link, which is not allowed.")
        total += info.file_size
        if total > settings.max_unzipped_bytes:
            raise UploadRejected("The uncompressed archive exceeds the size limit.", status_code=413)


def _is_symlink(info: ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return (mode & 0o170000) == 0o120000
