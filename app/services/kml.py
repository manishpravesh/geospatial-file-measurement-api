"""Read OGC KML 2.2 placemarks into shapely geometries.

KML coordinates are longitude, latitude, and an optional altitude, in that
order. The KML 2.2 specification fixes the coordinate reference system at
WGS 84 (EPSG:4326), so a missing CRS tag is not treated as unknown.
"""

from __future__ import annotations

import math

import defusedxml.ElementTree as ET
from pyproj import CRS
from shapely.errors import GEOSException, ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.exceptions import GeospatialParseError
from app.services.parsed import ParsedDataset, ParsedFeature
from app.services.projection import format_crs
from app.services.serialize import properties_document

_GEOMETRY_TAGS = {
    "Point",
    "LineString",
    "LinearRing",
    "Polygon",
    "MultiGeometry",
    "MultiTrack",
    "Track",
    "Model",
}
_DESCRIPTION_LIMIT = 10_000


class _FeatureGeometryError(Exception):
    pass


def parse_kml_bytes(content: bytes) -> ParsedDataset:
    if b"<!ENTITY" in content.upper() or b"<!DOCTYPE" in content.upper():
        raise GeospatialParseError("The KML file contains a document type declaration and was rejected.")
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise GeospatialParseError(f"The KML file is not valid XML: {exc}") from exc
    if _local(root.tag) != "kml":
        raise GeospatialParseError("The file is XML, but the root element is not kml.")

    crs = CRS.from_epsg(4326)
    features = [_parse_placemark(element, folders) for element, folders in _iter_placemarks(root)]
    return ParsedDataset(
        crs=crs,
        crs_label=format_crs(crs),
        features=features,
        file_type="kml",
    )


def _iter_placemarks(element: ET.Element, folders: tuple[str, ...] = ()):
    for child in list(element):
        name = _local(child.tag)
        if name == "Placemark":
            yield child, folders
        elif name == "Folder":
            folder_name = _child_text(child, "name")
            nested = folders + ((folder_name,) if folder_name else ())
            yield from _iter_placemarks(child, nested)
        elif name in {"Document", "kml"}:
            yield from _iter_placemarks(child, folders)
        else:
            # Styles, schemas, and other containers can wrap a Document.
            if any(_local(grandchild.tag) in {"Placemark", "Folder", "Document"} for grandchild in child):
                yield from _iter_placemarks(child, folders)


def _parse_placemark(element: ET.Element, folders: tuple[str, ...]) -> ParsedFeature:
    properties = _extended_properties(element)
    placemark_id = (element.attrib.get("id") or "").strip()
    if placemark_id and "id" not in {key.lower() for key in properties}:
        properties["id"] = placemark_id
    name = _child_text(element, "name")
    if name and "name" not in {key.lower() for key in properties}:
        properties["name"] = name
    description = _child_text(element, "description")
    if description and "description" not in {key.lower() for key in properties}:
        properties["description"] = description[:_DESCRIPTION_LIMIT]
    if folders and "folder" not in {key.lower() for key in properties}:
        properties["folder"] = "/".join(folders)
    properties = properties_document(properties)

    try:
        geometry = _placemark_geometry(element)
    except _FeatureGeometryError as exc:
        return ParsedFeature(None, "Unknown", properties, str(exc))
    except (GEOSException, ShapelyError, ValueError) as exc:
        return ParsedFeature(None, "Unknown", properties, f"Geometry could not be read: {exc}")

    if geometry is None:
        return ParsedFeature(None, "None", properties, "Feature has no geometry.")
    return ParsedFeature(geometry, geometry.geom_type, properties, None)


def _extended_properties(placemark: ET.Element) -> dict:
    properties: dict[str, str] = {}
    for child in placemark:
        if _local(child.tag) != "ExtendedData":
            continue
        for node in child.iter():
            local = _local(node.tag)
            if local == "Data":
                key = (node.attrib.get("name") or "").strip()
                value_node = next((item for item in list(node) if _local(item.tag) == "value"), None)
                if key:
                    properties[key] = "" if value_node is None or value_node.text is None else value_node.text.strip()
            elif local == "SimpleData":
                key = (node.attrib.get("name") or "").strip()
                if key:
                    properties[key] = (node.text or "").strip()
    return properties


def _placemark_geometry(element: ET.Element) -> BaseGeometry | None:
    for child in list(element):
        if _local(child.tag) in _GEOMETRY_TAGS:
            return _parse_geometry(child)
    return None


def _parse_geometry(element: ET.Element) -> BaseGeometry:
    local = _local(element.tag)
    if local == "Point":
        coords = _coordinates_of(element)
        if not coords:
            raise _FeatureGeometryError("Point has no coordinates.")
        return Point(coords[0])
    if local in {"LineString", "LinearRing"}:
        coords = _coordinates_of(element)
        if len(coords) < 2:
            raise _FeatureGeometryError(f"{local} needs at least two coordinates.")
        return LineString(coords)
    if local == "Polygon":
        return _parse_polygon(element)
    if local == "MultiGeometry":
        parts: list[BaseGeometry] = []
        for child in list(element):
            if _local(child.tag) in _GEOMETRY_TAGS:
                parts.append(_parse_geometry(child))
        if not parts:
            raise _FeatureGeometryError("MultiGeometry does not contain a supported geometry.")
        return _combine(parts)
    if local in {"Track", "MultiTrack"}:
        return _parse_track(element)
    if local == "Model":
        raise _FeatureGeometryError("Geometry type Model is not supported.")
    raise _FeatureGeometryError(f"Geometry type {local} is not supported.")


def _parse_polygon(element: ET.Element) -> Polygon:
    shell: list[tuple] | None = None
    holes: list[list[tuple]] = []
    for child in list(element):
        local = _local(child.tag)
        if local == "outerBoundaryIs":
            shell = _ring_coords(child)
        elif local == "innerBoundaryIs":
            holes.append(_ring_coords(child))
    if shell is None:
        rings = [_ring_coords(node) for node in element.iter() if _local(node.tag) == "LinearRing"]
        if not rings:
            raise _FeatureGeometryError("Polygon has no outer boundary.")
        shell, holes = rings[0], rings[1:]
    if len(shell) < 4:
        raise _FeatureGeometryError("Polygon outer boundary does not have enough coordinates.")
    for hole in holes:
        if len(hole) < 4:
            raise _FeatureGeometryError("Polygon hole does not have enough coordinates.")
    return Polygon(shell, holes)


def _parse_track(element: ET.Element) -> LineString:
    coords: list[tuple[float, float]] = []
    for node in element.iter():
        if _local(node.tag) != "coord" or not node.text:
            continue
        parts = node.text.split()
        if len(parts) < 2:
            continue
        try:
            longitude = float(parts[0])
            latitude = float(parts[1])
        except ValueError:
            continue
        if math.isfinite(longitude) and math.isfinite(latitude):
            coords.append((longitude, latitude))
    if len(coords) < 2:
        raise _FeatureGeometryError("Track does not contain enough coordinates.")
    return LineString(coords)


def _ring_coords(container: ET.Element) -> list[tuple]:
    ring = next((node for node in container.iter() if _local(node.tag) == "LinearRing"), None)
    target = ring if ring is not None else container
    coords = _close_ring(_coordinates_of(target))
    return coords


def _coordinates_of(element: ET.Element) -> list[tuple]:
    texts = [node.text for node in element.iter() if _local(node.tag) == "coordinates" and node.text]
    coords: list[tuple] = []
    has_altitude = False
    for text in texts:
        for token in text.replace("\n", " ").split():
            parts = [part for part in token.split(",") if part != ""]
            if len(parts) < 2:
                continue
            try:
                longitude = float(parts[0])
                latitude = float(parts[1])
            except ValueError:
                continue
            if not math.isfinite(longitude) or not math.isfinite(latitude):
                continue
            if len(parts) >= 3:
                try:
                    altitude = float(parts[2])
                except ValueError:
                    altitude = None
                if altitude is not None and math.isfinite(altitude):
                    has_altitude = True
                    coords.append((longitude, latitude, altitude))
                    continue
            coords.append((longitude, latitude))
    if not has_altitude:
        return [(item[0], item[1]) for item in coords]
    normalized = []
    for item in coords:
        if len(item) == 2:
            normalized.append((item[0], item[1], 0.0))
        else:
            normalized.append(item)
    return normalized


def _close_ring(coords: list[tuple]) -> list[tuple]:
    if len(coords) >= 3 and coords[0] != coords[-1]:
        return [*coords, coords[0]]
    return coords


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    if len(parts) == 1:
        return parts[0]
    points: list[Point] = []
    lines: list[LineString] = []
    polygons: list[Polygon] = []
    other: list[BaseGeometry] = []
    for part in parts:
        if part.geom_type == "Point":
            points.append(part)
        elif part.geom_type == "MultiPoint":
            points.extend(part.geoms)
        elif part.geom_type == "LineString":
            lines.append(part)
        elif part.geom_type == "MultiLineString":
            lines.extend(part.geoms)
        elif part.geom_type == "Polygon":
            polygons.append(part)
        elif part.geom_type == "MultiPolygon":
            polygons.extend(part.geoms)
        else:
            other.append(part)
    groups = [points, lines, polygons, other]
    if other or sum(bool(group) for group in groups) > 1:
        return GeometryCollection(parts)
    if polygons:
        return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    if lines:
        return lines[0] if len(lines) == 1 else MultiLineString(lines)
    if len(points) == 1:
        return points[0]
    return MultiPoint(points)


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in list(element):
        if _local(child.tag) == name and child.text and child.text.strip():
            return " ".join(child.text.split())
    return None


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag
