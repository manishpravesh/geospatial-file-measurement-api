"""Zipped shapefiles and the guards around archive extraction."""

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import Point, box

from app.config import Settings
from app.exceptions import GeospatialParseError, UploadRejected
from app.services.shapefile import read_archive
from tests.helpers import zip_bytes, zip_geodataframe


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'unused.db').resolve().as_posix()}",
        upload_dir=tmp_path / "uploads",
    )


def test_projected_shapefile_preserves_crs_attributes_and_geometry(tmp_path):
    frame = gpd.GeoDataFrame({"parcel_id": ["P-100"]}, geometry=[box(700000, 1400000, 701000, 1401000)], crs="EPSG:32643")
    content = zip_geodataframe(frame, tmp_path / "build")

    dataset = read_archive(content, tmp_path / "extracted", _settings(tmp_path))

    assert dataset.file_type == "shapefile"
    assert dataset.crs_label == "EPSG:32643"
    assert dataset.crs == CRS.from_epsg(32643)
    assert len(dataset.features) == 1
    feature = dataset.features[0]
    assert feature.properties["parcel_id"] == "P-100"
    assert feature.geometry.area == pytest.approx(1_000_000)


def test_shapefile_in_a_subdirectory_is_found(tmp_path):
    frame = gpd.GeoDataFrame({"name": ["pin"]}, geometry=[Point(77.5, 12.9)], crs="EPSG:4326")
    flat = zip_geodataframe(frame, tmp_path / "build", name="pins")
    nested: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(flat)) as archive:
        for info in archive.infolist():
            nested[f"survey/{info.filename}"] = archive.read(info)

    dataset = read_archive(zip_bytes(nested), tmp_path / "extracted", _settings(tmp_path))

    assert dataset.crs_label == "EPSG:4326"
    assert dataset.features[0].geometry_type == "Point"
    assert dataset.features[0].properties["name"] == "pin"


def test_missing_prj_leaves_the_crs_empty(tmp_path):
    frame = gpd.GeoDataFrame({"name": ["block"]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    content_dir = tmp_path / "build"
    content_dir.mkdir()
    frame.to_file(content_dir / "block.shp")
    files = {path.name: path.read_bytes() for path in content_dir.glob("block.*") if path.suffix.lower() != ".prj"}

    dataset = read_archive(zip_bytes(files), tmp_path / "extracted", _settings(tmp_path))

    assert dataset.crs is None
    assert dataset.crs_label is None
    assert dataset.features[0].geometry_type == "Polygon"


def test_missing_sidecar_is_a_parse_error(tmp_path):
    with pytest.raises(GeospatialParseError, match="sidecar"):
        read_archive(zip_bytes({"only.shp": b"not a shapefile"}), tmp_path / "extracted", _settings(tmp_path))


def test_archive_without_a_shapefile_is_rejected(tmp_path):
    with pytest.raises(UploadRejected, match="does not contain a shapefile"):
        read_archive(zip_bytes({"readme.txt": b"hello"}), tmp_path / "extracted", _settings(tmp_path))


def test_two_shapefiles_are_rejected(tmp_path):
    frame = gpd.GeoDataFrame({"name": ["a"]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    first = zip_geodataframe(frame, tmp_path / "one", name="first")
    second = zip_geodataframe(frame, tmp_path / "two", name="second")
    combined: dict[str, bytes] = {}
    for blob in (first, second):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for info in archive.infolist():
                combined[info.filename] = archive.read(info)

    with pytest.raises(UploadRejected, match="more than one shapefile"):
        read_archive(zip_bytes(combined), tmp_path / "extracted", _settings(tmp_path))


def test_zip_slip_is_rejected(tmp_path):
    with pytest.raises(UploadRejected, match="unsafe path"):
        read_archive(zip_bytes({"../evil.txt": b"nope"}), tmp_path / "extracted", _settings(tmp_path))

    extracted = tmp_path / "extracted"
    assert not (tmp_path / "evil.txt").exists()
    if extracted.exists():
        assert not any(path.name == "evil.txt" for path in extracted.rglob("*"))


def test_kmz_archive_is_read_as_kml(tmp_path):
    kml = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Placemark><name>Pin</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
</kml>
"""
    dataset = read_archive(zip_bytes({"doc.kml": kml}), tmp_path / "extracted", _settings(tmp_path))

    assert dataset.file_type == "kmz"
    assert dataset.crs_label == "EPSG:4326"
    assert dataset.features[0].properties["name"] == "Pin"
