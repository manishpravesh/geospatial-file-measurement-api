"""Write samples/parcels.zip, a projected shapefile with a known area.

The KML sample is maintained by hand in samples/survey.kml. A shapefile is
binary, so this script rebuilds the zip from the same coordinates the README
describes.
"""

import io
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    frame = gpd.GeoDataFrame(
        {"parcel_id": ["P-100", "P-200"], "name": ["North plot", "South plot"]},
        geometry=[
            box(700000, 1400000, 701000, 1401000),
            box(701200, 1400000, 701600, 1400250),
        ],
        crs="EPSG:32643",
    )
    build = SAMPLES / "_build"
    build.mkdir(exist_ok=True)
    frame.to_file(build / "parcels.shp")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(build.glob("parcels.*")):
            archive.write(path, arcname=path.name)
    (SAMPLES / "parcels.zip").write_bytes(buffer.getvalue())

    for path in build.glob("parcels.*"):
        path.unlink()
    build.rmdir()
    print(SAMPLES / "parcels.zip")


if __name__ == "__main__":
    main()
