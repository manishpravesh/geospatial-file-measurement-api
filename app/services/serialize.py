"""JSON-safe feature properties, identifiers, and GeoJSON."""

import math
from decimal import Decimal
from typing import Any

from shapely import to_geojson
from shapely.errors import GEOSException, ShapelyError
from shapely.geometry.base import BaseGeometry

MAX_PROPERTIES = 200
MAX_PROPERTY_CHARS = 10_000
_PREFERRED_ID_KEYS = ("id", "fid", "feature_id", "featureid", "objectid", "gml_id")


def json_safe(value: Any) -> Any:
    """Convert numpy, pandas, and geometry-adjacent values into JSON values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        return float(value)

    type_name = type(value).__name__
    module_name = type(value).__module__
    if type_name in {"NAType", "NaTType"}:
        return None
    if module_name == "numpy":
        if "float" in type_name:
            number = float(value)
            if math.isnan(number) or math.isinf(number):
                return None
            return number
        if "int" in type_name or "uint" in type_name:
            return int(value)
        if type_name.startswith("bool"):
            return bool(value)
        if type_name == "ndarray":
            return [json_safe(item) for item in value.tolist()]
    if module_name.startswith("pandas") and hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "isoformat") and hasattr(value, "year"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def properties_document(properties: dict) -> dict:
    document: dict = {}
    for index, (key, value) in enumerate(properties.items()):
        if index >= MAX_PROPERTIES:
            break
        safe_value = json_safe(value)
        if isinstance(safe_value, str) and len(safe_value) > MAX_PROPERTY_CHARS:
            safe_value = safe_value[:MAX_PROPERTY_CHARS]
        document[str(key)[:256]] = safe_value
    return document


def choose_feature_id(properties: dict, feature_index: int, source_fid: str | None = None) -> str:
    """Pick a stable identifier from attributes, then the source FID, then the index."""

    lowered = {str(key).lower(): value for key, value in properties.items()}
    for key in _PREFERRED_ID_KEYS:
        text = _identifier_text(lowered.get(key))
        if text:
            return text
    for key, value in lowered.items():
        if key.endswith("_id"):
            text = _identifier_text(value)
            if text:
                return text
    for key in ("name", "title"):
        text = _identifier_text(lowered.get(key))
        if text:
            return text
    if source_fid:
        return str(source_fid)[:256]
    return str(feature_index)


def geometry_document(geometry: BaseGeometry | None) -> dict | None:
    if geometry is None:
        return None
    try:
        import json

        return json.loads(to_geojson(geometry))
    except (TypeError, ValueError, GEOSException, ShapelyError):
        return None


def _identifier_text(value: Any) -> str | None:
    safe = json_safe(value)
    if safe is None:
        return None
    text = str(safe).strip()
    if not text:
        return None
    return text[:256]
