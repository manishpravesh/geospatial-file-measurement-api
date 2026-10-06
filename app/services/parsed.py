"""Features read from an uploaded file, before they are stored."""

from dataclasses import dataclass

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


@dataclass
class ParsedFeature:
    geometry: BaseGeometry | None
    geometry_type: str
    properties: dict
    read_error: str | None = None
    source_fid: str | None = None


@dataclass
class ParsedDataset:
    crs: CRS | None
    crs_label: str | None
    features: list[ParsedFeature]
    file_type: str
