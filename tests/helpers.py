"""Builders for KML text and zipped shapefiles."""

import io
import zipfile
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
SURVEY_KML = (ROOT / "samples" / "survey.kml").read_text(encoding="utf-8")


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def zip_geodataframe(frame: gpd.GeoDataFrame, directory: Path, name: str = "layer") -> bytes:
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_file(directory / f"{name}.shp")
    files: dict[str, bytes] = {}
    for path in directory.glob(f"{name}.*"):
        files[path.name] = path.read_bytes()
    return zip_bytes(files)
