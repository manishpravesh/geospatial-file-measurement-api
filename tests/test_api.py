"""HTTP contract for upload, file details, and measurements."""

import io
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from app.models import StoredFeature
from tests.conftest import build_client
from tests.helpers import SURVEY_KML, zip_bytes, zip_geodataframe


def _upload(client, filename: str, content: bytes, content_type: str = "application/octet-stream"):
    return client.post(
        "/api/files/",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_health_and_required_paths(client):
    assert client.get("/health").json() == {"status": "ok"}
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/files/" in paths
    assert "/api/files/{id}/" in paths
    assert "/api/files/{id}/measurements/" in paths


def test_kml_upload_file_record_and_measurements(client):
    created = _upload(client, "survey.kml", SURVEY_KML.encode(), "application/vnd.google-earth.kml+xml")

    assert created.status_code == 201
    body = created.json()
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 4
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert body["file_type"] == "kml"
    file_id = body["id"]

    fetched = client.get(f"/api/files/{file_id}/")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == file_id
    assert fetched.json()["feature_count"] == 4

    measurements = client.get(f"/api/files/{file_id}/measurements/").json()
    assert measurements["feature_count"] == 4
    assert measurements["crs"] == "EPSG:4326"
    assert measurements["unsupported_count"] == 1

    plot, path, mark, mixed = measurements["features"]
    assert plot["feature_index"] == 0
    assert plot["feature_id"] == "plot-a"
    assert plot["geometry_type"] == "Polygon"
    assert plot["geometry"]["type"] == "Polygon"
    assert plot["crs"] == "EPSG:4326"
    assert plot["properties"]["parcel_id"] == "A-1"
    assert plot["measurement"]["kind"] == "area"
    assert plot["measurement"]["unit"] == "square_meters"
    assert plot["measurement"]["value"] > 1_000_000
    assert plot["measurement"]["measurement_crs"].startswith("+proj=laea")

    assert path["geometry_type"] == "LineString"
    assert path["feature_id"] == "Path"
    assert path["measurement"]["kind"] == "length"
    assert path["measurement"]["unit"] == "meters"
    assert path["measurement"]["value"] > 100
    assert path["measurement"]["measurement_crs"].startswith("+proj=aeqd")

    assert mark["geometry_type"] == "Point"
    assert mark["measurement"]["supported"] is True
    assert mark["measurement"]["kind"] is None
    assert mark["measurement"]["value"] is None

    assert mixed["geometry_type"] == "GeometryCollection"
    assert mixed["measurement"]["supported"] is False
    assert mixed["measurement"]["value"] is None
    assert "GeometryCollection" in mixed["measurement"]["note"]

    assert measurements["total_area_square_meters"] == plot["measurement"]["value"]
    assert measurements["total_length_meters"] == path["measurement"]["value"]


def test_measurements_can_be_paged(client):
    created = _upload(client, "survey.kml", SURVEY_KML.encode())
    file_id = created.json()["id"]

    page = client.get(f"/api/files/{file_id}/measurements/", params={"limit": 1, "offset": 1})

    assert page.status_code == 200
    body = page.json()
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert body["feature_count"] == 4
    assert [feature["feature_index"] for feature in body["features"]] == [1]


def test_projected_shapefile_area_is_exact(client, tmp_path):
    frame = gpd.GeoDataFrame(
        {"parcel_id": ["P-100"]},
        geometry=[box(700000, 1400000, 701000, 1401000)],
        crs="EPSG:32643",
    )
    created = _upload(client, "parcels.zip", zip_geodataframe(frame, tmp_path / "shp"))

    assert created.status_code == 201
    assert created.json()["crs"] == "EPSG:32643"
    assert created.json()["file_type"] == "shapefile"
    file_id = created.json()["id"]
    feature = client.get(f"/api/files/{file_id}/measurements/").json()["features"][0]

    assert feature["feature_id"] == "P-100"
    assert feature["measurement"]["value"] == 1_000_000
    assert feature["measurement"]["measurement_crs"] == "EPSG:32643"
    assert feature["measurement"]["unit"] == "square_meters"


def test_upload_without_trailing_slash(client):
    response = client.post(
        "/api/files",
        files={"file": ("survey.kml", SURVEY_KML.encode(), "application/vnd.google-earth.kml+xml")},
        follow_redirects=False,
    )

    assert response.status_code == 201


def test_filename_with_a_directory_prefix_is_stripped(client):
    response = _upload(client, r"..\survey.kml", SURVEY_KML.encode())

    assert response.status_code == 201
    assert response.json()["filename"] == "survey.kml"


def test_missing_prj_still_returns_the_features(client, tmp_path):
    frame = gpd.GeoDataFrame({"name": ["block"]}, geometry=[box(77.5, 12.9, 77.6, 13.0)], crs="EPSG:4326")
    target = tmp_path / "build"
    target.mkdir()
    frame.to_file(target / "block.shp")
    files = {path.name: path.read_bytes() for path in target.glob("block.*") if path.suffix.lower() != ".prj"}
    created = _upload(client, "block.zip", zip_bytes(files))

    assert created.status_code == 201
    assert created.json()["status"] == "COMPLETED"
    assert created.json()["crs"] is None
    feature = client.get(f"/api/files/{created.json()['id']}/measurements/").json()["features"][0]
    assert feature["geometry_type"] == "Polygon"
    assert feature["properties"]["name"] == "block"
    assert feature["measurement"]["supported"] is False
    assert feature["measurement"]["value"] is None


def test_list_and_delete(client):
    first = _upload(client, "one.kml", SURVEY_KML.encode()).json()["id"]
    second = _upload(client, "two.kml", SURVEY_KML.encode()).json()["id"]

    listed = client.get("/api/files/").json()
    assert listed["count"] == 2
    assert {item["id"] for item in listed["files"]} == {first, second}

    assert client.delete(f"/api/files/{first}").status_code == 204
    assert client.get(f"/api/files/{first}/").status_code == 404

    session = client.app.state.database.session_factory()
    try:
        assert session.query(StoredFeature).filter_by(file_id=first).count() == 0
    finally:
        session.close()


def test_unknown_file_is_not_found(client):
    assert client.get("/api/files/missing/").status_code == 404
    assert client.get("/api/files/missing/measurements/").status_code == 404


def test_rejected_uploads(client, tmp_path):
    assert _upload(client, "notes.txt", b"hello").status_code == 400
    assert _upload(client, "empty.kml", b"").status_code == 400
    assert _upload(client, "notes.zip", b"this is not a zip").status_code == 400
    assert _upload(client, "broken.kml", b"<kml><unclosed>").status_code == 422
    assert _upload(client, "slip.zip", zip_bytes({"../evil.txt": b"nope"})).status_code == 400
    assert client.get("/api/files/").json()["count"] == 0

    limited = build_client(tmp_path / "limited", max_upload_bytes=32, max_features=1)
    with limited as limited_client:
        too_big = _upload(limited_client, "survey.kml", SURVEY_KML.encode())
        assert too_big.status_code == 413
        too_many = _upload(limited_client, "small.kml", b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Placemark><Point><coordinates>0,0</coordinates></Point></Placemark>
  <Placemark><Point><coordinates>1,1</coordinates></Point></Placemark>
</kml>
""")
        assert too_many.status_code == 413
        assert limited_client.get("/api/files/").json()["count"] == 0


def test_repository_samples(client):
    parcels = Path(__file__).resolve().parents[1] / "samples" / "parcels.zip"
    created = _upload(client, "parcels.zip", parcels.read_bytes())

    assert created.status_code == 201
    assert created.json()["crs"] == "EPSG:32643"
    features = client.get(f"/api/files/{created.json()['id']}/measurements/").json()["features"]
    assert [(feature["feature_id"], feature["measurement"]["value"]) for feature in features] == [
        ("P-100", 1_000_000),
        ("P-200", 100_000),
    ]


def test_bad_zip_does_not_report_success(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "no geometry here")
    response = _upload(client, "readme.zip", buffer.getvalue())

    assert response.status_code == 400
    assert "shapefile" in response.json()["detail"]
