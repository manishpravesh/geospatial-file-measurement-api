"""Project geometries before measuring them.

Area and length are never calculated from longitude and latitude degrees.
Geographic coordinates are unwrapped across the antimeridian, densified, and
transformed into a feature-local projected CRS:

* polygons use Lambert azimuthal equal-area, which preserves area
* lines use azimuthal equidistant, which preserves distance from the feature

Coordinates that are already in a linear projected CRS are measured in that
CRS and converted to metres. World Mercator is reprojected first because its
scale changes sharply with latitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely import force_2d, get_num_coordinates, make_valid, segmentize
from shapely.affinity import translate
from shapely.errors import GEOSException, ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

WORLD_MERCATOR_EPSG = {3857, 3395, 3785, 900913}
WORLD_MERCATOR_METHODS = {
    "mercator (1sp)",
    "mercator (2sp)",
    "mercator (variant a)",
    "mercator (variant b)",
    "mercator (variant c)",
    "popular visualisation pseudo mercator",
    "equidistant cylindrical",
    "equidistant cylindrical (spherical)",
}
METRE_UNIT_NAMES = {"m", "metre", "meter", "metres", "meters"}
AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}
MAX_COORDINATES = 250_000


@dataclass(frozen=True)
class Measurement:
    supported: bool
    kind: str | None
    value: float | None
    unit: str | None
    measurement_crs: str | None
    note: str | None


def format_crs(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    try:
        epsg = crs.to_epsg()
    except CRSError:
        epsg = None
    if epsg:
        return f"EPSG:{epsg}"
    try:
        authority = crs.to_authority()
    except CRSError:
        authority = None
    if authority and authority[0] and authority[1]:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_string()


def measure_geometry(
    geometry: BaseGeometry | None,
    source_crs: CRS | None,
    *,
    segment_length_degrees: float = 0.05,
    read_error: str | None = None,
) -> Measurement:
    """Measure one feature without raising for unsupported or invalid geometry."""

    if read_error:
        return _unsupported(read_error)
    if geometry is None:
        return _unsupported("Feature has no geometry.")

    try:
        if geometry.is_empty:
            return _unsupported("Geometry is empty.")
        geometry = force_2d(geometry)
        if geometry.geom_type in POINT_TYPES:
            return Measurement(
                supported=True,
                kind=None,
                value=None,
                unit=None,
                measurement_crs=None,
                note="Point geometries do not have an area or a length.",
            )
        kind = _kind_for(geometry.geom_type)
        if kind is None:
            return _unsupported(
                f"Geometry type {geometry.geom_type} is not supported for measurement."
            )
        if source_crs is None:
            return _unsupported(
                "No coordinate reference system was found, so area and length were not "
                "calculated. Shapefiles need a .prj file."
            )

        prepared = _prepare(geometry, kind)
        if prepared is None or prepared.is_empty:
            return _unsupported("Geometry is empty after repairing invalid coordinates.")
        projected, measure_crs, label, note = _project_for_measurement(
            prepared,
            source_crs,
            kind,
            segment_length_degrees,
        )
        raw = projected.area if kind == "area" else projected.length
        if not math.isfinite(raw) or raw < 0:
            return _unsupported("Measurement result was not a finite number.")
        factor = _meters_per_unit(measure_crs)
        value = raw * (factor if kind == "length" else factor * factor)
        if not math.isfinite(value):
            return _unsupported("Measurement result was not a finite number.")
    except MemoryError:
        raise
    except (CRSError, ProjError, GEOSException, ShapelyError, ValueError, OverflowError) as exc:
        return _unsupported(f"Measurement could not be calculated: {exc}")
    except Exception as exc:
        return _unsupported(f"Measurement could not be calculated: {exc}")

    return Measurement(
        supported=True,
        kind=kind,
        value=value,
        unit="square_meters" if kind == "area" else "meters",
        measurement_crs=label,
        note=note,
    )


def _project_for_measurement(
    geometry: BaseGeometry,
    source: CRS,
    kind: str,
    segment_length_degrees: float,
) -> tuple[BaseGeometry, CRS, str, str]:
    if _measure_in_source(source):
        factor = _meters_per_unit(source)
        label = format_crs(source) or source.to_string()
        if abs(factor - 1.0) < 1e-9:
            note = "Measured in the source projected coordinate system. Units are metres."
        else:
            unit_name = source.axis_info[0].unit_name if source.axis_info else "linear units"
            note = (
                f"Measured in the source projected coordinate system ({label}, {unit_name}) "
                "and converted to metres."
            )
        return geometry, source, label, note

    geographic = _to_wgs84(geometry, source)
    geographic = unwrap_geographic(geographic)
    geographic = _shift_into_longitude_range(geographic)
    _assert_geographic_bounds(geographic)
    if get_num_coordinates(geographic) <= 100_000:
        geographic = segmentize(geographic, max_segment_length=segment_length_degrees)
    if get_num_coordinates(geographic) > MAX_COORDINATES:
        raise ValueError("The geometry has too many coordinates to measure safely.")

    longitude, latitude = _representative_lon_lat(geographic)
    measure_crs, label = _local_crs(longitude, latitude, kind)
    projected = _transform(geographic, CRS.from_epsg(4326), measure_crs)
    quantity = "Area" if kind == "area" else "Length"
    projection = "Lambert azimuthal equal-area" if kind == "area" else "azimuthal equidistant"
    article = "a" if kind == "area" else "an"
    note = (
        f"{quantity} was calculated after transforming {format_crs(source)} to {article} {projection} "
        f"projection centered at {_format_latitude(latitude)}, {_format_longitude(longitude)}. "
        "Geographic degrees were not used as distances."
    )
    return projected, measure_crs, label, note


def _measure_in_source(crs: CRS) -> bool:
    if not crs.is_projected or _is_world_mercator(crs):
        return False
    return _has_linear_units(crs)


def _is_world_mercator(crs: CRS) -> bool:
    try:
        epsg = crs.to_epsg()
    except CRSError:
        epsg = None
    if epsg in WORLD_MERCATOR_EPSG:
        return True
    operation = crs.coordinate_operation
    method = (operation.method_name or "").lower() if operation is not None else ""
    return method in WORLD_MERCATOR_METHODS


def _has_linear_units(crs: CRS) -> bool:
    axes = list(crs.axis_info)[:2]
    if len(axes) < 2:
        return False
    for axis in axes:
        unit = (axis.unit_name or "").lower()
        if "degree" in unit or "radian" in unit:
            return False
        factor = axis.unit_conversion_factor
        if factor is None or factor <= 0 or not math.isfinite(factor):
            return False
    return True


def _to_wgs84(geometry: BaseGeometry, source: CRS) -> BaseGeometry:
    target = CRS.from_epsg(4326)
    try:
        if source.to_epsg() == 4326 or source.equals(target):
            return geometry
    except CRSError:
        pass
    if not (source.is_geographic or source.is_projected):
        raise ValueError(
            f"Coordinate reference system {format_crs(source)} cannot be used for measurement."
        )
    return _transform(geometry, source, target)


def _transform(geometry: BaseGeometry, source: CRS, target: CRS) -> BaseGeometry:
    # always_xy keeps longitude in x and latitude in y. The default axis order
    # of EPSG:4326 is latitude, longitude, which would swap the coordinates.
    transformer = Transformer.from_crs(source, target, always_xy=True)

    def _xy(x: float, y: float, z: float | None = None) -> tuple[float, float]:
        del z
        return transformer.transform(x, y)

    return shapely_transform(_xy, geometry)


def _local_crs(longitude: float, latitude: float, kind: str) -> tuple[CRS, str]:
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValueError("Feature has non-finite coordinates.")
    if abs(latitude) > 90 or abs(longitude) > 180:
        raise ValueError("Feature coordinates are outside the geographic range.")
    if latitude >= 90:
        latitude = 89.999999
    elif latitude <= -90:
        latitude = -89.999999
    projection = "laea" if kind == "area" else "aeqd"
    definition = (
        f"+proj={projection} +lat_0={latitude:.8f} +lon_0={longitude:.8f} "
        "+datum=WGS84 +units=m +no_defs"
    )
    crs = CRS.from_proj4(definition)
    if not crs.is_projected:
        raise ValueError("Could not build a projected measurement CRS.")
    return crs, definition


def _representative_lon_lat(geometry: BaseGeometry) -> tuple[float, float]:
    point = geometry.representative_point()
    return float(point.x), float(point.y)


def _meters_per_unit(crs: CRS) -> float:
    if not crs.is_projected:
        raise ValueError("Measurement CRS is not projected.")
    axes = list(crs.axis_info)[:2]
    factors: list[float] = []
    for axis in axes:
        factor = axis.unit_conversion_factor
        if factor is None:
            return _fallback_metres(crs)
        factors.append(float(factor))
    if not factors:
        return _fallback_metres(crs)
    first = factors[0]
    if any(abs(factor - first) / first > 1e-6 for factor in factors[1:]):
        raise ValueError(f"{format_crs(crs)} has inconsistent horizontal units.")
    return first


def _fallback_metres(crs: CRS) -> float:
    names = {(axis.unit_name or "").lower() for axis in crs.axis_info}
    if names and names <= METRE_UNIT_NAMES:
        return 1.0
    raise ValueError(f"Could not determine a metre conversion for {format_crs(crs)}.")


def _assert_geographic_bounds(geometry: BaseGeometry) -> None:
    min_x, min_y, max_x, max_y = geometry.bounds
    if not all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y)):
        raise ValueError("Geometry has non-finite coordinates.")
    if min_y < -90.000001 or max_y > 90.000001:
        raise ValueError("Latitude is outside the range -90 to 90.")
    if max_x - min_x > 180.000001:
        raise ValueError(
            "This geometry spans more than 180 degrees of longitude, so a single "
            "projected measurement would be unreliable."
        )


def _shift_into_longitude_range(geometry: BaseGeometry) -> BaseGeometry:
    """Slide a narrow geometry into the valid longitude range without changing its shape."""

    min_x, _, max_x, _ = geometry.bounds
    shift = 0.0
    if max_x > 180:
        shift -= math.ceil((max_x - 180) / 360.0) * 360.0
    if min_x + shift < -180:
        needed = -180.0 - (min_x + shift)
        if max_x + shift + needed <= 180:
            shift += needed
    if shift == 0:
        return geometry
    return translate(geometry, xoff=shift)


def unwrap_geographic(geometry: BaseGeometry) -> BaseGeometry:
    """Walk each ring and take the short longitude step when a coordinate jumps the antimeridian.

    A survey feature stored as 179.9 to -179.9 is a few hundred metres wide, not a
    polygon that wraps the world. Edges longer than 180 degrees of longitude are
    treated as antimeridian crossings. A dataset that truly contains one edge
    spanning more than half the planet should be split by the producer.
    """

    geometry_type = geometry.geom_type
    if geometry_type in {"Point", "MultiPoint"}:
        return geometry
    if geometry_type == "LineString":
        return LineString(_unwrap_coords(list(geometry.coords)))
    if geometry_type == "MultiLineString":
        return MultiLineString([_unwrap_coords(list(part.coords)) for part in geometry.geoms])
    if geometry_type == "Polygon":
        return _unwrap_polygon(geometry)
    if geometry_type == "MultiPolygon":
        return MultiPolygon([_unwrap_polygon(part) for part in geometry.geoms])
    if geometry_type == "GeometryCollection":
        return GeometryCollection([unwrap_geographic(part) for part in geometry.geoms])
    return geometry


def _unwrap_polygon(polygon: Polygon) -> Polygon:
    shell = _unwrap_coords(list(polygon.exterior.coords), closed=True)
    reference = shell[0][0] if shell else 0.0
    holes = [
        _unwrap_coords(list(ring.coords), closed=True, reference=reference)
        for ring in polygon.interiors
    ]
    return Polygon(shell, holes)


def _unwrap_coords(
    coords: list[tuple],
    *,
    closed: bool = False,
    reference: float | None = None,
) -> list[tuple]:
    if not coords:
        return coords

    def _near(longitude: float, anchor: float) -> float:
        while longitude - anchor > 180:
            longitude -= 360
        while anchor - longitude > 180:
            longitude += 360
        return longitude

    first = coords[0]
    longitude = float(first[0])
    if reference is not None:
        longitude = _near(longitude, reference)
    output = [(longitude, float(first[1]), *tuple(first[2:]))]
    previous = longitude
    for coord in coords[1:]:
        longitude = _near(float(coord[0]), previous)
        output.append((longitude, float(coord[1]), *tuple(coord[2:])))
        previous = longitude
    if closed and len(output) > 1:
        output[-1] = output[0]
    return output


def _prepare(geometry: BaseGeometry, kind: str) -> BaseGeometry | None:
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    return _extract(geometry, kind)


def _extract(geometry: BaseGeometry, kind: str) -> BaseGeometry | None:
    wanted = AREA_TYPES if kind == "area" else LENGTH_TYPES
    if geometry.geom_type in wanted:
        return geometry
    if geometry.geom_type != "GeometryCollection":
        return None

    parts: list[BaseGeometry] = []
    for child in geometry.geoms:
        extracted = _extract(child, kind)
        if extracted is not None and not extracted.is_empty:
            parts.append(extracted)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]

    target = "Polygon" if kind == "area" else "LineString"
    multi = "MultiPolygon" if kind == "area" else "MultiLineString"
    flattened: list[BaseGeometry] = []
    for part in parts:
        if part.geom_type == target:
            flattened.append(part)
        elif part.geom_type == multi:
            flattened.extend(part.geoms)
    if not flattened:
        return None
    if kind == "area":
        return MultiPolygon(flattened)
    return MultiLineString(flattened)


def _kind_for(geometry_type: str) -> str | None:
    if geometry_type in AREA_TYPES:
        return "area"
    if geometry_type in LENGTH_TYPES:
        return "length"
    return None


def _unsupported(note: str) -> Measurement:
    return Measurement(
        supported=False,
        kind=None,
        value=None,
        unit=None,
        measurement_crs=None,
        note=note,
    )


def _format_latitude(latitude: float) -> str:
    hemisphere = "N" if latitude >= 0 else "S"
    return f"{abs(latitude):.6f}°{hemisphere}"


def _format_longitude(longitude: float) -> str:
    while longitude > 180:
        longitude -= 360
    while longitude < -180:
        longitude += 360
    if abs(abs(longitude) - 180) < 1e-8:
        return "180°"
    hemisphere = "E" if longitude >= 0 else "W"
    return f"{abs(longitude):.6f}°{hemisphere}"
