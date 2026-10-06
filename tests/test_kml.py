"""KML placemarks become features without requiring a CRS tag."""

import pytest
from shapely import force_2d
from shapely.geometry import LineString, Point, Polygon

from app.exceptions import GeospatialParseError
from app.services.kml import parse_kml_bytes
from tests.helpers import SURVEY_KML


def _kml(body: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">{body}</kml>
""".encode()


def test_survey_kml_reads_attributes_folder_and_geometries():
    dataset = parse_kml_bytes(SURVEY_KML.encode())

    assert dataset.crs_label == "EPSG:4326"
    assert dataset.file_type == "kml"
    assert len(dataset.features) == 4

    plot, path, mark, mixed = dataset.features
    assert plot.geometry_type == "Polygon"
    assert plot.properties["parcel_id"] == "A-1"
    assert plot.properties["name"] == "Plot A"
    assert plot.properties["id"] == "plot-a"
    assert plot.properties["folder"] == "Survey"
    assert force_2d(plot.geometry).equals(
        Polygon([(77.50, 12.90), (77.51, 12.90), (77.51, 12.91), (77.50, 12.91), (77.50, 12.90)])
    )

    assert path.geometry_type == "LineString"
    assert force_2d(path.geometry).equals(LineString([(77.50, 12.90), (77.51, 12.91)]))
    assert mark.geometry_type == "Point"
    assert force_2d(mark.geometry).equals(Point(77.505, 12.905))
    assert mixed.geometry_type == "GeometryCollection"
    assert mixed.read_error is None


def test_schema_data_and_track_are_read():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <Placemark>
      <ExtendedData>
        <SchemaData schemaUrl="#parcels">
          <SimpleData name="parcel_id">B-2</SimpleData>
        </SchemaData>
      </ExtendedData>
      <Point><coordinates>10,20</coordinates></Point>
    </Placemark>
    <Placemark>
      <name>Walk</name>
      <gx:Track>
        <gx:coord>77.5 12.9 0</gx:coord>
        <gx:coord>77.51 12.91 5</gx:coord>
      </gx:Track>
    </Placemark>
  </Document>
</kml>
""".encode()
    dataset = parse_kml_bytes(content)

    assert dataset.features[0].properties["parcel_id"] == "B-2"
    assert dataset.features[0].geometry.equals(Point(10, 20))
    assert dataset.features[1].geometry_type == "LineString"
    assert dataset.features[1].geometry.equals(LineString([(77.5, 12.9), (77.51, 12.91)]))


def test_polygon_hole_and_unclosed_ring():
    content = _kml(
        """
        <Placemark>
          <Polygon>
            <outerBoundaryIs>
              <LinearRing>
                <coordinates>0,0 1,0 1,1 0,1</coordinates>
              </LinearRing>
            </outerBoundaryIs>
            <innerBoundaryIs>
              <LinearRing>
                <coordinates>0.2,0.2 0.4,0.2 0.4,0.4 0.2,0.4 0.2,0.2</coordinates>
              </LinearRing>
            </innerBoundaryIs>
          </Polygon>
        </Placemark>
        """
    )
    feature = parse_kml_bytes(content).features[0]

    assert feature.geometry_type == "Polygon"
    assert len(list(feature.geometry.interiors)) == 1
    assert feature.geometry.exterior.is_closed


def test_model_geometry_is_kept_as_a_feature_error():
    content = _kml(
        """
        <Placemark>
          <name>Building</name>
          <Model><altitudeMode>relativeToGround</altitudeMode></Model>
        </Placemark>
        """
    )
    feature = parse_kml_bytes(content).features[0]

    assert feature.geometry is None
    assert feature.read_error
    assert "Model" in feature.read_error


def test_invalid_xml_and_non_kml_root_are_rejected():
    with pytest.raises(GeospatialParseError):
        parse_kml_bytes(b"<kml><unclosed>")
    with pytest.raises(GeospatialParseError):
        parse_kml_bytes(b"<gpx></gpx>")


def test_doctype_is_rejected():
    content = b"""<?xml version="1.0"?>
    <!DOCTYPE kml [<!ENTITY xxe "nope">]>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>
    """
    with pytest.raises(GeospatialParseError):
        parse_kml_bytes(content)
