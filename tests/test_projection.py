"""Measurements use projected metres, including around the antimeridian."""

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.ops import transform as shapely_transform
from shapely.validation import make_valid

from app.services.projection import measure_geometry


def test_projected_square_keeps_its_area_and_crs():
    # 1 km by 1 km in UTM zone 43N. The area in that CRS is exactly 1_000_000 m².
    square = box(700000, 1400000, 701000, 1401000)
    result = measure_geometry(square, CRS.from_epsg(32643))

    assert result.supported, result.note
    assert result.kind == "area"
    assert result.unit == "square_meters"
    assert result.value == pytest.approx(1_000_000, rel=1e-9)
    assert result.measurement_crs == "EPSG:32643"


def test_projected_line_keeps_its_length():
    line = LineString([(700000, 1400000), (703500, 1400000)])
    result = measure_geometry(line, CRS.from_epsg(32643))

    assert result.supported, result.note
    assert result.kind == "length"
    assert result.unit == "meters"
    assert result.value == pytest.approx(3500, rel=1e-9)
    assert "+proj=aeqd" not in (result.measurement_crs or "")


def test_geographic_area_matches_the_same_square_in_utm():
    square = box(700000, 1400000, 701000, 1401000)
    to_wgs84 = Transformer.from_crs(32643, 4326, always_xy=True)
    geographic = shapely_transform(lambda x, y: to_wgs84.transform(x, y), square)

    result = measure_geometry(geographic, CRS.from_epsg(4326))

    assert result.supported, result.note
    assert result.value == pytest.approx(1_000_000, rel=0.005)
    assert result.measurement_crs.startswith("+proj=laea")
    assert "degrees were not used" in result.note


def test_geographic_length_matches_the_geodesic():
    line = LineString([(77.5, 12.9), (78.2, 13.4)])
    result = measure_geometry(line, CRS.from_epsg(4326))
    _forward, _back, distance = Geod(ellps="WGS84").inv(77.5, 12.9, 78.2, 13.4)

    assert result.supported, result.note
    assert result.kind == "length"
    assert result.value == pytest.approx(distance, rel=0.005)
    assert result.measurement_crs.startswith("+proj=aeqd")
    # The degree length of this line is about 0.86, so a degree calculation cannot pass.
    assert result.value > 1_000


def test_geographic_area_matches_the_geodesic_polygon_area():
    polygon = box(77.5, 12.9, 77.6, 13.0)
    result = measure_geometry(polygon, CRS.from_epsg(4326))
    geod = Geod(ellps="WGS84")
    if not hasattr(geod, "geometry_area_perimeter"):
        pytest.skip("This pyproj build has no geodesic polygon area")
    area, _perimeter = geod.geometry_area_perimeter(polygon)

    assert result.value == pytest.approx(abs(area), rel=0.005)
    assert result.value > 1_000_000


def test_degree_area_is_not_returned_for_a_one_degree_square():
    result = measure_geometry(box(0, 0, 1, 1), CRS.from_epsg(4326))

    assert result.supported, result.note
    assert result.value > 1_000_000_000


def test_axis_order_is_longitude_then_latitude():
    # At latitude 60, one degree of longitude is about half of one degree of latitude.
    # Swapping the axes would move the rectangle to the equator and change the area.
    wide = box(0, 60, 1, 60.1)
    result = measure_geometry(wide, CRS.from_epsg(4326))
    geod = Geod(ellps="WGS84")
    area, _perimeter = geod.geometry_area_perimeter(wide)

    assert result.value == pytest.approx(abs(area), rel=0.01)


def test_polygon_hole_is_subtracted():
    polygon = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)], [[(100, 100), (200, 100), (200, 200), (100, 200), (100, 100)]])
    result = measure_geometry(polygon, CRS.from_epsg(32643))

    assert result.value == pytest.approx(1_000_000 - 10_000, rel=1e-9)


def test_multipolygon_and_multilinestring_sum_their_parts():
    area = measure_geometry(
        MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 20)]),
        CRS.from_epsg(32643),
    )
    length = measure_geometry(
        MultiLineString([LineString([(0, 0), (5, 0)]), LineString([(0, 1), (0, 4)])]),
        CRS.from_epsg(32643),
    )

    assert area.value == pytest.approx(300, rel=1e-9)
    assert length.value == pytest.approx(8, rel=1e-9)


def test_point_has_no_measurement():
    result = measure_geometry(Point(77.5, 12.9), CRS.from_epsg(4326))

    assert result.supported is True
    assert result.kind is None
    assert result.value is None
    assert result.unit is None


def test_unsupported_geometry_does_not_raise():
    collection = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
    result = measure_geometry(collection, CRS.from_epsg(4326))

    assert result.supported is False
    assert result.value is None
    assert "GeometryCollection" in result.note


def test_missing_crs_does_not_assume_degrees():
    result = measure_geometry(box(0, 0, 1, 1), None)

    assert result.supported is False
    assert result.value is None
    assert ".prj" in result.note


def test_empty_geometry_is_reported():
    result = measure_geometry(Polygon(), CRS.from_epsg(4326))

    assert result.supported is False
    assert "empty" in result.note.lower()


def test_invalid_polygon_is_repaired():
    bowtie = Polygon([(700000, 1400000), (701000, 1401000), (701000, 1400000), (700000, 1401000), (700000, 1400000)])
    result = measure_geometry(bowtie, CRS.from_epsg(32643))

    assert result.supported, result.note
    assert result.value == pytest.approx(make_valid(bowtie).area, rel=1e-6)
    assert result.value > 0


def test_altitude_does_not_change_planimetric_area():
    flat = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)])
    raised = Polygon([(0, 0, 5), (1000, 0, 5), (1000, 1000, 20), (0, 1000, 20), (0, 0, 5)])
    crs = CRS.from_epsg(32643)

    assert measure_geometry(raised, crs).value == pytest.approx(measure_geometry(flat, crs).value)


def test_us_survey_feet_are_converted_to_metres():
    crs = CRS.from_epsg(2263)
    factor = crs.axis_info[0].unit_conversion_factor
    result = measure_geometry(box(0, 0, 1000, 1000), crs)

    assert factor == pytest.approx(0.3048006096, rel=1e-6)
    assert result.supported, result.note
    assert result.unit == "square_meters"
    assert result.value == pytest.approx(1_000_000 * factor * factor, rel=1e-9)
    assert result.measurement_crs == "EPSG:2263"


def test_world_mercator_is_not_measured_in_mercator_metres():
    geographic = box(77.5, 12.9, 77.52, 12.92)
    to_mercator = Transformer.from_crs(4326, 3857, always_xy=True)
    mercator = shapely_transform(lambda x, y: to_mercator.transform(x, y), geographic)

    direct = measure_geometry(geographic, CRS.from_epsg(4326))
    reprojected = measure_geometry(mercator, CRS.from_epsg(3857))

    assert reprojected.value == pytest.approx(direct.value, rel=0.01)
    assert abs(mercator.area - direct.value) / direct.value > 0.02


def test_antimeridian_line_uses_the_short_path():
    line = LineString([(179.9, 0.0), (-179.9, 0.0)])
    result = measure_geometry(line, CRS.from_epsg(4326))
    _forward, _back, distance = Geod(ellps="WGS84").inv(179.9, 0.0, -179.9, 0.0)

    assert result.supported, result.note
    assert result.value == pytest.approx(distance, rel=0.01)
    assert result.value < 30_000


def test_antimeridian_polygon_is_the_small_polygon():
    polygon = Polygon(
        [
            (179.9, -0.1),
            (-179.9, -0.1),
            (-179.9, 0.1),
            (179.9, 0.1),
            (179.9, -0.1),
        ]
    )
    result = measure_geometry(polygon, CRS.from_epsg(4326))

    assert result.supported, result.note
    assert 1e8 < result.value < 2e9


def test_negative_longitude_and_southern_hemisphere():
    polygon = box(-43.2, -22.9, -43.1, -22.8)
    result = measure_geometry(polygon, CRS.from_epsg(4326))

    assert result.supported, result.note
    assert 5e7 < result.value < 3e8
    assert "S" in result.note
    assert "W" in result.note


def test_polar_polygon_is_finite():
    polygon = box(-0.2, 88.8, 0.2, 89.0)
    result = measure_geometry(polygon, CRS.from_epsg(4326))

    assert result.supported, result.note
    assert 1_000 < result.value < 1e11
