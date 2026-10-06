# Geospatial File Measurement API

Upload a zipped shapefile, a KML file, or a KMZ archive. The service reads every feature and returns its identifier, geometry type, geometry, coordinate reference system, and attributes. Polygon area and line length are calculated in metres after the geometry is transformed into a projected coordinate system. Geographic degrees are never treated as distances.

## Setup

Python 3.11 or newer is required. Pyogrio ships its own GDAL build, so a separate GDAL install is not required.

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

macOS and Linux:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The API listens on `http://127.0.0.1:8000`. Interactive documentation is at `http://127.0.0.1:8000/docs`.

Copy `.env.example` to `.env` to change the database path, upload directory, or size limits. Every setting uses the `GEO_` prefix.

| Variable | Default | Purpose |
|---|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/measurements.db` | SQLite database created on startup |
| `GEO_UPLOAD_DIR` | `./data/uploads` | Stored uploads |
| `GEO_MAX_UPLOAD_BYTES` | `52428800` | Maximum request body, 50 MB |
| `GEO_MAX_UNZIPPED_BYTES` | `209715200` | Maximum uncompressed archive, 200 MB |
| `GEO_MAX_ZIP_FILES` | `100` | Maximum members in one archive |
| `GEO_MAX_FEATURES` | `50000` | Maximum features in one file |
| `GEO_SEGMENT_LENGTH_DEGREES` | `0.05` | Densify geographic edges before projecting them |

Run the tests with `pytest`.

### Docker

```bash
docker build -t geospatial-measurement-api .
docker run --rm -p 8000:8000 geospatial-measurement-api
```

The container keeps the database and uploads in `/data`.

## API

The upload field name is `file`. These three endpoints are the contract. List and delete are included so a local database can be inspected and cleared.

### Upload

`POST /api/files/`

Accepts `.zip` (one shapefile, or a KMZ-style archive that contains one `.kml`), `.kml`, and `.kmz`.

```powershell
curl.exe -F "file=@samples/survey.kml" http://127.0.0.1:8000/api/files/
```

```json
{
  "id": "1f5537cb-fed7-47e1-9d9d-47749f81105a",
  "filename": "survey.kml",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "file_type": "kml",
  "created_at": "2026-10-06T19:22:18.732362+00:00"
}
```

`id` is a UUID. `status` is `COMPLETED` after a successful read. A file that cannot be accepted is not stored.

| Status | When |
|---|---|
| 201 | The file was read and measured |
| 400 | Empty file, wrong extension, unsafe archive path, or the zip is not a shapefile |
| 413 | The upload, the uncompressed archive, or the feature count exceeds the configured limit |
| 422 | The extension is valid, but the contents are not readable geospatial data |

`POST /api/files` without the trailing slash is accepted as well.

### File information

`GET /api/files/{id}/`

Returns the same object as the upload response.

```powershell
curl.exe http://127.0.0.1:8000/api/files/1f5537cb-fed7-47e1-9d9d-47749f81105a/
```

An unknown id returns `404` with `{"detail": "File not found."}`.

### Measurements

`GET /api/files/{id}/measurements/`

Optional query parameters: `limit` (default 100, maximum 1000) and `offset` (default 0). Totals cover the whole file. `features` is the requested page, ordered by `feature_index`.

`samples/survey.kml` contains a polygon, a line, a point, and a mixed `MultiGeometry` near Bengaluru. The polygon is 0.01 by 0.01 degrees. Its area is about 1.20 km², which a degree calculation would report as `0.0001`.

```powershell
curl.exe http://127.0.0.1:8000/api/files/1f5537cb-fed7-47e1-9d9d-47749f81105a/measurements/
```

```json
{
  "file_id": "1f5537cb-fed7-47e1-9d9d-47749f81105a",
  "filename": "survey.kml",
  "crs": "EPSG:4326",
  "feature_count": 4,
  "limit": 100,
  "offset": 0,
  "total_area_square_meters": 1200618.2453711506,
  "total_length_meters": 1549.735169514981,
  "unsupported_count": 1,
  "features": [
    {
      "feature_index": 0,
      "feature_id": "plot-a",
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.5, 12.9, 0.0], [77.51, 12.9, 0.0], [77.51, 12.91, 0.0], [77.5, 12.91, 0.0], [77.5, 12.9, 0.0]]]
      },
      "crs": "EPSG:4326",
      "properties": {"parcel_id": "A-1", "id": "plot-a", "name": "Plot A", "folder": "Survey"},
      "measurement": {
        "supported": true,
        "kind": "area",
        "value": 1200618.2453711506,
        "unit": "square_meters",
        "measurement_crs": "+proj=laea +lat_0=12.90500000 +lon_0=77.50500000 +datum=WGS84 +units=m +no_defs",
        "note": "Area was calculated after transforming EPSG:4326 to a Lambert azimuthal equal-area projection centered at 12.905000°N, 77.505000°E. Geographic degrees were not used as distances."
      }
    },
    {
      "feature_index": 1,
      "feature_id": "Path",
      "geometry_type": "LineString",
      "geometry": {"type": "LineString", "coordinates": [[77.5, 12.9, 0.0], [77.51, 12.91, 0.0]]},
      "crs": "EPSG:4326",
      "properties": {"name": "Path", "folder": "Survey"},
      "measurement": {
        "supported": true,
        "kind": "length",
        "value": 1549.735169514981,
        "unit": "meters",
        "measurement_crs": "+proj=aeqd +lat_0=12.90000000 +lon_0=77.50000000 +datum=WGS84 +units=m +no_defs",
        "note": "Length was calculated after transforming EPSG:4326 to an azimuthal equidistant projection centered at 12.900000°N, 77.500000°E. Geographic degrees were not used as distances."
      }
    },
    {
      "feature_index": 2,
      "feature_id": "Mark",
      "geometry_type": "Point",
      "geometry": {"type": "Point", "coordinates": [77.505, 12.905, 0.0]},
      "crs": "EPSG:4326",
      "properties": {"name": "Mark", "folder": "Survey"},
      "measurement": {
        "supported": true,
        "kind": null,
        "value": null,
        "unit": null,
        "measurement_crs": null,
        "note": "Point geometries do not have an area or a length."
      }
    },
    {
      "feature_index": 3,
      "feature_id": "Mixed",
      "geometry_type": "GeometryCollection",
      "geometry": {
        "type": "GeometryCollection",
        "geometries": [
          {"type": "Point", "coordinates": [77.5, 12.9, 0.0]},
          {"type": "LineString", "coordinates": [[77.5, 12.9, 0.0], [77.51, 12.9, 0.0]]}
        ]
      },
      "crs": "EPSG:4326",
      "properties": {"name": "Mixed", "folder": "Survey"},
      "measurement": {
        "supported": false,
        "kind": null,
        "value": null,
        "unit": null,
        "measurement_crs": null,
        "note": "Geometry type GeometryCollection is not supported for measurement."
      }
    }
  ]
}
```

`feature_id` is taken from an id-like attribute (`id`, `fid`, `parcel_id`, and similar), then from `name`, then from the source record id, then from `feature_index`.

`samples/parcels.zip` is a shapefile in EPSG:32643 (UTM zone 43N). North plot is a 1 km square, so its area is exactly 1,000,000 square metres. South plot is 400 m by 250 m, so its area is exactly 100,000 square metres. Because the file is already in a linear projected CRS, those values are calculated in EPSG:32643 and reported in metres.

```powershell
curl.exe -F "file=@samples/parcels.zip" http://127.0.0.1:8000/api/files/
```

```json
{
  "id": "d821beae-ae14-4471-9ffa-c4b1e3438b25",
  "filename": "parcels.zip",
  "feature_count": 2,
  "crs": "EPSG:32643",
  "status": "COMPLETED",
  "file_type": "shapefile",
  "created_at": "2026-10-06T19:22:19.091724+00:00"
}
```

A shapefile with no `.prj` is still returned. Its `crs` is `null`, and each area or length measurement has `supported: false` and a note that a `.prj` is required. The service does not guess EPSG:4326.

### Other endpoints

`GET /api/files/?limit=50&offset=0` lists uploads.

`DELETE /api/files/{id}` removes the database rows and the stored files. It returns `204`.

`GET /health` returns `{"status": "ok"}`.

## Architecture

```
app/
  main.py                 application factory, /health
  config.py               environment settings
  database.py             SQLite engine and sessions
  models.py               uploaded files and features
  schemas.py              response models
  api/routes.py           HTTP endpoints
  services/ingest.py      validate, store, measure, commit
  services/kml.py         KML and KMZ placemarks
  services/shapefile.py   safe zip extraction and shapefile reading
  services/projection.py  CRS choice and measurement
  services/serialize.py   feature ids, attributes, GeoJSON
```

Tests live in `tests/`. `samples/` holds a KML file and a zipped shapefile used by the README and by `test_repository_samples`. `scripts/generate_samples.py` rebuilds `samples/parcels.zip`.

### File-processing flow

1. The upload is size-limited while it is read. The filename is reduced to its base name.
2. `.kml`, `.kmz`, and `.zip` are accepted. Anything else is rejected before it is stored.
3. The original bytes are written under `data/uploads/{id}/`.
4. A KML file is parsed as XML. A zip is checked for path traversal, symbolic links, file count, and uncompressed size, then extracted.
5. A shapefile archive must contain one `.shp` together with `.shx` and `.dbf`. A `.prj` supplies the CRS. A `.cpg` supplies the attribute encoding. An archive that contains only a `.kml` is read as KMZ.
6. Each feature keeps its index, chosen id, geometry type, GeoJSON geometry, CRS, and attributes.
7. Measurements are calculated and the file is stored with status `COMPLETED`.
8. If reading fails, the database transaction is rolled back and the upload directory is deleted. The client receives 400 or 422.

Parsing one bad placemark does not reject the file. That feature is stored with `measurement.supported` set to false and a note explaining why.

### Measurement calculation flow

| Geometry | Result |
|---|---|
| Polygon, MultiPolygon | Area in square metres. Holes are subtracted. |
| LineString, MultiLineString | Length in metres. Parts are summed. |
| Point, MultiPoint | No measurement. The feature is still returned. |
| Anything else, including GeometryCollection and Model | No measurement, and the request does not fail. |

Invalid polygons are repaired with `make_valid` before measuring. Empty geometries and geometries that cannot be repaired are reported on that feature. Altitude is kept in the GeoJSON response and ignored for the planimetric measurement.

### CRS handling

KML 2.2 coordinates are longitude, latitude, and optional altitude in WGS 84. The file does not need a CRS tag. The service records `EPSG:4326`.

Shapefile CRS comes from the `.prj`. A missing `.prj` leaves the CRS empty. Measurements that need a CRS are skipped for that file.

When the source CRS is projected, uses linear units, and is suitable for measurement, the geometry is measured in that CRS. US survey feet and international feet are converted to metres. World Mercator (including EPSG:3857) is not used for measurement, because its scale changes sharply with latitude. Those coordinates are transformed to WGS 84 and then into a local projection.

Geographic coordinates take this path:

1. Transform to EPSG:4326 if the source CRS is a different geographic CRS. Longitude stays in x and latitude stays in y (`always_xy`).
2. Walk each ring and take the short longitude step where a coordinate jumps across the antimeridian, so a feature stored as 179.9 to -179.9 stays a small feature.
3. Densify edges (default 0.05 degrees) so the projected boundary follows the ellipsoid.
4. Build a feature-local projected CRS centered on a point that lies on the feature.
5. Transform into that CRS and read the planar area or length, in metres.

Polygons use Lambert azimuthal equal-area (`+proj=laea`), which preserves area. Lines use azimuthal equidistant (`+proj=aeqd`), which preserves distance from the center. The PROJ definition is returned as `measurement_crs`.

## Design decisions

**FastAPI.** The service is a small JSON API with file upload and typed responses. FastAPI provides that surface, request validation, and OpenAPI docs with little boilerplate. Django REST Framework would be the stronger choice if this needed an admin, user accounts, or a large relational schema. Those are not part of the measurement contract, so they were left out.

**SQLite, written during the request.** The upload response already has `status: COMPLETED` and a stable id, and later GET requests must return the same result. Processing finishes before the response is sent, and the features are stored. A background queue would be the next step for files near the 50,000-feature limit. The status field is the extension point for that.

**Reject unreadable files instead of storing a FAILED row.** A bad upload should not look like a dataset. Clients get 400 or 422, and nothing is left on disk. Unsupported geometry inside an otherwise valid file is different: the file is `COMPLETED`, and that feature explains itself in `measurement.note`.

**Local azimuthal projections instead of one UTM zone.** UTM is a reasonable default and has a stable EPSG code. It is a poor fit at the poles, on a zone boundary, and for area, because a transverse Mercator projection is conformal rather than equal-area. A Lambert azimuthal equal-area projection centered on the feature preserves area, and an azimuthal equidistant projection preserves distance from the feature. Each feature can sit in the projection that matches its own location. Files that are already in a local projected CRS, including UTM, are measured in that CRS so the producer's coordinates are respected.

**Geodesic length is a check, not the implementation.** `pyproj.Geod` can measure ellipsoidal distance directly. The service still projects first, because the measurement has to be a coordinate transformation into a projected CRS. The tests compare those projected results with geodesic area and length.

**A custom KML reader.** KML's structure is small and fixed: placemarks, folders, extended data, and a handful of geometry tags. Reading it with the standard library and defusedxml keeps extended data, folder names, and mixed `MultiGeometry` under our control, and it rejects document-type declarations. Shapefiles are the opposite: the `.shp`, `.shx`, `.dbf`, and `.prj` layout is a GDAL job, so those go through GeoPandas and pyogrio.

**Trust the declared CRS, except for Web Mercator.** Guessing EPSG:4326 for a shapefile without a `.prj` would silently invent areas. Web Mercator is the other direction: the CRS is declared, and it is still a bad CRS for area and distance, so it is reprojected before measuring.

**Antimeridian policy.** An edge whose longitude changes by more than 180 degrees is treated as a crossing of the antimeridian, which is the usual way a small feature in Fiji or Alaska is stored. A single edge that truly spans more than half of the planet should be split by the data producer. Features that still span more than 180 degrees after that normalization are reported as unsupported rather than given a meaningless area.

## Learning

The useful mistakes in this kind of service are quiet ones. A polygon in EPSG:4326 has an area in square degrees, and that number is small, positive, and wrong. Swapping longitude and latitude still produces a polygon, often in a different country, with an area that can look plausible. Web Mercator metres are real metres on the projection, and they are still the wrong ground distance once you leave the equator. The implementation and the tests are arranged around those three failures: projected metres, axis order, and a Mercator file that must not be measured as Mercator.

A few format details only show up once real files are involved. KML is always WGS 84 and the coordinate order is longitude, latitude, altitude. A shapefile is a family of files, field names are short, and the `.prj` is optional even when the geometry is useless without it. ZIP archives need an explicit check for `..` and links. Geometry collections and empty placemarks are normal, so measurement has to fail one feature at a time.

## Future scope

- Move processing onto a queue and expose `PROCESSING` and `FAILED` on the file record.
- Store geometries in PostGIS and calculate area and length there for datasets past the current feature limit.
- Accept GeoJSON and GeoPackage.
- Offer geodesic area and length as a second method beside the projected measurement.
- Split features that genuinely cross the antimeridian into parts before measuring, instead of using the short-edge convention.
- Add authentication, per-user storage quotas, and object storage for the original files.
- Calculate 3D surface area where altitude is meaningful, separate from the planimetric area returned today.
