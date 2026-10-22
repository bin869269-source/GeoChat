"""Independent numeric oracles: analytic projected fixtures and WGS84 geodesics.

Regression expectations live separately. These tests never replace a correct
reference value with the existing algorithm's output just to obtain green tests.
"""
import re

import pytest
from pyproj import Geod, Transformer
from shapely import transform
from shapely.geometry import LineString, Point, Polygon, shape

from tests.conftest import collection, layer

GEOD = Geod(ellps="WGS84")
TO_WGS = Transformer.from_crs(25832, 4326, always_xy=True).transform


def projected_layer(name, *geometries):
    # Exact metre-space fixtures are converted to ordinary WGS84 input.
    return layer(name, collection(*(transform(g, TO_WGS, interleaved=False) for g in geometries)))


def properties(shown, index=0):
    if not shown:
        pytest.fail("Tool produced no analysis output; independent numeric assertion was not reached")
    return shown[-1]["data"]["features"][index]["properties"]


def first_distance(text):
    match = re.search(r"\*\*([\d.]+) m\*\*", text)
    assert match, text
    return float(match.group(1))


def test_crs_assignment_and_real_transform(backend):
    gdf = backend.extract_features_to_gdf(collection(Point(9, 51)))
    assert gdf.crs.to_epsg() == 4326
    projected = gdf.to_crs(25832)
    assert projected.geometry.iloc[0].x == pytest.approx(500000, abs=0.001)
    assert projected.geometry.iloc[0].y > 5_000_000
    roundtrip = projected.to_crs(4326).geometry.iloc[0]
    assert roundtrip.x == pytest.approx(9, abs=1e-9)
    assert roundtrip.y == pytest.approx(51, abs=1e-9)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-CRS: incoming CRS is overwritten with EPSG:4326")
def test_declared_projected_crs_must_be_transformed(backend):
    data = collection(Point(500000, 5649824.888))
    data["crs"] = {"type": "name", "properties": {"name": "EPSG:25832"}}
    result = backend.extract_features_to_gdf(data).to_crs(4326).geometry.iloc[0]
    assert result.x == pytest.approx(9, abs=0.00001)
    assert result.y == pytest.approx(51, abs=0.00001)


@pytest.mark.parametrize("latitude", [0, 51])
def test_distance_geodesic_reference(backend, bind, latitude):
    lon2, lat2, _ = GEOD.fwd(9, latitude, 90, 100)
    bind(layer("geodesic", collection(Point(9, latitude), Point(lon2, lat2))))
    text = backend.calculate_feature_distances.invoke({"layer_name": "geodesic"})
    assert first_distance(text) == pytest.approx(100, abs=0.1)
    assert "Meter" in text and "km" in text


def test_polygon_shortest_distance_not_centroid(backend, bind):
    a = Polygon([(500000, 5650000), (500010, 5650000), (500010, 5650010), (500000, 5650010)])
    b = Polygon([(500040, 5650000), (500050, 5650000), (500050, 5650010), (500040, 5650010)])
    bind(projected_layer("buildings", a, b))
    result = backend.calculate_feature_distances.invoke({"layer_name": "buildings"})
    assert first_distance(result) == pytest.approx(30, abs=0.1)


def test_intersecting_geometry_distance_zero(backend, bind):
    a = LineString([(500000, 5650000), (500010, 5650010)])
    b = LineString([(500000, 5650010), (500010, 5650000)])
    bind(projected_layer("lines", a, b))
    assert first_distance(backend.calculate_feature_distances.invoke({"layer_name": "lines"})) == 0


@pytest.mark.parametrize("latitude", [0, pytest.param(51, marks=pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="KD-BUFFER: Web Mercator radius is not ground metres at 51N"))])
def test_buffer_ground_radius(backend, bind, latitude):
    _, shown = bind(layer("origin", collection(Point(9, latitude))))
    output = backend.calculate_buffer_analysis.invoke({"layer_name": "origin", "distance_meters": 500})
    if "Erfolg" not in output:
        pytest.fail("Buffer failed before radius check: " + output)
    polygon = shape(shown[-1]["data"]["features"][0]["geometry"])
    radii = [GEOD.inv(9, latitude, lon, lat)[2] for lon, lat in polygon.exterior.coords]
    # 1% tolerance includes Web Mercator ellipsoid mismatch at the equator.
    assert min(radii) == pytest.approx(500, rel=0.01)
    assert max(radii) == pytest.approx(500, rel=0.01)


def test_area_analytic_square(backend, bind):
    square = Polygon([(500000, 5650000), (501000, 5650000), (501000, 5651000), (500000, 5651000)])
    _, shown = bind(projected_layer("square", square))
    assert "Erfolg" in backend.calculate_area_and_density.invoke({"polygon_layer_name": "square"})
    assert properties(shown)["area_km2"] == pytest.approx(1, abs=0.00005)


def test_area_geodesic_reference(backend, bind, polygons):
    geom = shape(polygons["data"]["features"][0]["geometry"])
    expected = abs(GEOD.geometry_area_perimeter(geom)[0]) / 1_000_000
    _, shown = bind(polygons)
    backend.calculate_area_and_density.invoke({"polygon_layer_name": "polygons"})
    assert properties(shown)["area_km2"] == pytest.approx(expected, rel=0.002)


def test_density_interior_boundary_exterior_and_overlap(backend, bind):
    # Identical squares deliberately overlap: strict interior point counts once
    # in EACH square; exact vertex is excluded under current `within` semantics.
    square = Polygon([(9, 51), (9.01, 51), (9.01, 51.01), (9, 51.01)])
    polygons = layer("regions", collection(square, square))
    points = layer("samples", collection(Point(9.005, 51.005), Point(9, 51), Point(8, 50)))
    _, shown = bind(polygons, points)
    backend.calculate_area_and_density.invoke({"polygon_layer_name": "regions", "point_layer_name": "samples"})
    reference_area = abs(GEOD.geometry_area_perimeter(square)[0]) / 1_000_000
    for index in [0, 1]:
        props = properties(shown, index)
        assert props["objekt_anzahl"] == 1
        assert props["dichte_pro_km2"] == pytest.approx(1 / reference_area, rel=0.01)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-TINY: area rounded to zero before density calculation")
def test_tiny_area_retains_nonzero_area_and_density(backend, bind):
    square = Polygon([(500000, 5650000), (500001, 5650000), (500001, 5650001), (500000, 5650001)])
    _, shown = bind(projected_layer("tiny", square), projected_layer("inside", Point(500000.5, 5650000.5)))
    backend.calculate_area_and_density.invoke({"polygon_layer_name": "tiny", "point_layer_name": "inside"})
    props = properties(shown)
    assert props["area_km2"] == pytest.approx(0.000001, rel=0.01)
    assert props["objekt_anzahl"] == 1
    assert props["dichte_pro_km2"] == pytest.approx(1_000_000, rel=0.01)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-DENSITY: premature area rounding biases density")
def test_density_uses_unrounded_area(backend, bind):
    # Analytic 123.4 m2, deliberately not representable by four km2 decimals.
    square = Polygon([(500000, 5650000), (500010, 5650000), (500010, 5650012.34), (500000, 5650012.34)])
    _, shown = bind(projected_layer("small", square), projected_layer("inside", Point(500005, 5650005)))
    backend.calculate_area_and_density.invoke({"polygon_layer_name": "small", "point_layer_name": "inside"})
    assert properties(shown)["dichte_pro_km2"] == pytest.approx(1_000_000 / 123.4, rel=0.001)


@pytest.mark.parametrize("geometry", [
    pytest.param(Polygon(), id="empty"),
    pytest.param(Polygon([(9, 51), (9.01, 51), (9.02, 51), (9, 51)]), id="zero_area"),
    pytest.param(Polygon([(9, 51), (9.01, 51.01), (9, 51.01), (9.01, 51), (9, 51)]), id="self_intersection"),
])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-GEOMETRY: invalid/empty/zero-area polygon accepted as successful area result")
def test_invalid_area_input_requires_error(backend, bind, geometry):
    _, shown = bind(layer("bad", collection(geometry)))
    output = backend.calculate_area_and_density.invoke({"polygon_layer_name": "bad"})
    assert "Fehler" in output and shown == []


def test_null_geometry_does_not_produce_distance(backend, bind):
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {}},
        collection(Point(9, 51))["features"][0]]}
    _, shown = bind(layer("null", data))
    result = backend.calculate_feature_distances.invoke({"layer_name": "null"})
    assert "Fehler" in result and not shown
