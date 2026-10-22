"""Regression tests: actual decorated tool schemas and .invoke paths."""
import copy
from types import SimpleNamespace

import pytest
import requests

from tests.conftest import layer

SIGNATURES = {
    "fetch_city_boundary_osm": {"city_name": ...},
    "visualize_layer_on_map": {"layer_name": ..., "map_type": "geojson",
        "data_field": None, "color": "blue", "weight": 2, "fill_opacity": 0.4},
    "calculate_buffer_analysis": {"layer_name": ..., "distance_meters": 500.0},
    "calculate_area_and_density": {"polygon_layer_name": ..., "point_layer_name": None},
    "run_machine_learning_spatial_prediction": {"layer_name": ..., "target_field": None},
    "update_map_style": {"layer_name": ..., "color": None, "weight": None, "fill_opacity": None},
    "calculate_feature_distances": {"layer_name": ..., "max_pairs": 10},
}


def test_registration(backend):
    assert [t.name for t in backend.TOOLS] == list(SIGNATURES)
    assert backend.llm.tools == backend.TOOLS


@pytest.mark.parametrize("name", SIGNATURES)
def test_tool_schema(backend, name):
    fields = getattr(backend, name).args_schema.model_fields
    assert set(fields) == set(SIGNATURES[name])
    for field, default in SIGNATURES[name].items():
        if default is ...:
            assert fields[field].is_required()
        else:
            assert fields[field].default == default
    with pytest.raises(ValueError):
        getattr(backend, name).invoke({})


def test_fetch_boundary(backend, bind, polygons, monkeypatch):
    raw, shown = bind()
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: polygons["data"])
    monkeypatch.setattr(backend.requests, "get", get)
    for _ in range(2):
        assert "Erfolg" in backend.fetch_city_boundary_osm.invoke({"city_name": "Test City"})
    assert not raw and len(shown) == 2
    assert shown[0]["name"] != shown[1]["name"]
    assert shown[0]["data"]["features"][0]["geometry"]["type"] == "Polygon"
    assert "Test%20City" in calls[0][0] and calls[0][1]["timeout"] == 10


@pytest.mark.parametrize("mode,expected", [("http", "HTTP 503"), ("empty", "Keine Geometrie"),
                                         ("timeout", "Fehler"), ("json", "Fehler")])
def test_fetch_errors(backend, bind, monkeypatch, mode, expected):
    _, shown = bind()
    def get(*args, **kwargs):
        if mode == "timeout":
            raise requests.Timeout("synthetic timeout")
        def response():
            if mode == "json":
                raise ValueError("synthetic bad JSON")
            return {"features": []}
        return SimpleNamespace(status_code=503 if mode == "http" else 200, json=response)
    monkeypatch.setattr(backend.requests, "get", get)
    assert expected in backend.fetch_city_boundary_osm.invoke({"city_name": "missing"})
    assert not shown


@pytest.mark.parametrize("kind", ["points", "geojson", "heatmap", "choropleth"])
def test_visualize(backend, bind, points, kind):
    before = copy.deepcopy(points)
    raw, shown = bind(points)
    args = {"layer_name": "POINTS", "map_type": kind, "data_field": "value"}
    assert "Erfolg" in backend.visualize_layer_on_map.invoke(args)
    assert "Erfolg" in backend.visualize_layer_on_map.invoke(args)
    assert raw == [before] and len(shown) == 2
    assert shown[0]["name"] != shown[1]["name"]
    assert shown[0]["data"] == points["data"] and shown[0]["type"] == kind
    assert shown[0]["data_field"] == "value"


@pytest.mark.parametrize("name,args", [
    ("visualize_layer_on_map", {"layer_name": "absent"}),
    ("calculate_buffer_analysis", {"layer_name": "absent"}),
    ("calculate_area_and_density", {"polygon_layer_name": "absent"}),
    ("run_machine_learning_spatial_prediction", {"layer_name": "absent"}),
    ("update_map_style", {"layer_name": "absent"}),
    ("calculate_feature_distances", {"layer_name": "absent"}),
])
def test_missing_layer_feedback(backend, bind, name, args):
    raw, shown = bind()
    assert "nicht gefunden" in getattr(backend, name).invoke(args)
    assert raw == [] and shown == []


def test_buffer_regression(backend, bind, points):
    import geopandas as gpd
    raw, shown = bind(points)
    before = copy.deepcopy(raw)
    assert "Erfolg" in backend.calculate_buffer_analysis.invoke({"layer_name": "points"})
    assert raw == before and len(shown) == 1
    actual = gpd.GeoDataFrame.from_features(shown[0]["data"]["features"], crs=4326)
    assert set(actual.geom_type) == {"Polygon"}
    # Characterization only: reproduces the old projection, NOT a correctness oracle.
    original = backend.extract_features_to_gdf(points["data"]).to_crs(3857)
    for got, expected in zip(actual.to_crs(3857).geometry, original.buffer(500)):
        assert got.hausdorff_distance(expected) < 1e-6


def test_area_density_regression(backend, bind, polygons, points):
    raw, shown = bind(polygons, points)
    before = copy.deepcopy(raw)
    assert "Erfolg" in backend.calculate_area_and_density.invoke({
        "polygon_layer_name": "polygons", "point_layer_name": "points"})
    props = shown[0]["data"]["features"][0]["properties"]
    assert props["area_km2"] > 0 and props["objekt_anzahl"] == 2
    assert props["dichte_pro_km2"] == round(2 / props["area_km2"], 2)
    assert shown[0]["type"] == "choropleth" and raw == before


def test_area_only(backend, bind, polygons):
    _, shown = bind(polygons)
    assert "Erfolg" in backend.calculate_area_and_density.invoke({"polygon_layer_name": "polygons"})
    assert shown[0]["data_field"] == "area_km2"


def test_ml_real_random_forest_deterministic(backend, bind, points):
    raw, shown = bind(points)
    before = copy.deepcopy(raw)
    tool = backend.run_machine_learning_spatial_prediction
    assert "Erfolg" in tool.invoke({"layer_name": "points", "target_field": "value"})
    first = copy.deepcopy(shown[0]["data"])
    assert "Erfolg" in tool.invoke({"layer_name": "points"})
    assert shown[1]["data"] == first and raw == before
    assert len(first) == 3 and shown[0]["type"] == "points"
    for feat in first:
        p = feat["properties"]
        assert 1 <= p["ml_predicted"] <= 3
        assert p["ml_residual"] == pytest.approx(p["value"] - p["ml_predicted"], abs=0.005)


@pytest.mark.parametrize("mode", ["no_numeric", "missing_target", "polygon"])
def test_ml_errors(backend, bind, points, polygons, mode):
    target = copy.deepcopy(polygons if mode == "polygon" else points)
    args = {"layer_name": target["name"]}
    if mode == "no_numeric":
        for f in target["data"]["features"]:
            f["properties"].pop("value")
    else:
        args["target_field"] = "absent" if mode == "missing_target" else "value"
    _, shown = bind(target)
    output = backend.run_machine_learning_spatial_prediction.invoke(args)
    assert "Keine numerische" in output if mode == "no_numeric" else "Fehler" in output
    assert shown == []


def test_style_update(backend, bind, polygons):
    _, shown = bind(shown=[copy.deepcopy(polygons)])
    original = copy.deepcopy(shown[0]["data"])
    assert "Erfolg" in backend.update_map_style.invoke({"layer_name": "POLYGONS", "color": "red",
                                                      "weight": 3, "fill_opacity": 0})
    assert shown[0]["folium_kwargs"] == {"color": "red", "fillColor": "red", "weight": 3, "fillOpacity": 0}
    assert shown[0]["data"] == original


def test_distances_regression(backend, bind, points):
    raw, shown = bind(points)
    before = copy.deepcopy(raw)
    output = backend.calculate_feature_distances.invoke({"layer_name": "points", "max_pairs": 1})
    assert "Abstand (Meter)" in output and "Nächste Nachbarn" in output
    assert len([s for s in output.splitlines() if "**" in s and s.startswith("|")]) == 1
    assert raw == before and not shown


def test_distance_single(backend, bind, points):
    points["data"]["features"] = points["data"]["features"][:1]
    bind(points)
    assert "weniger als 2" in backend.calculate_feature_distances.invoke({"layer_name": "points"})


@pytest.mark.parametrize("tool,args", [
    ("calculate_buffer_analysis", {"layer_name": "empty"}),
    ("calculate_area_and_density", {"polygon_layer_name": "empty"}),
    ("run_machine_learning_spatial_prediction", {"layer_name": "empty"}),
    ("calculate_feature_distances", {"layer_name": "empty"}),
])
def test_empty_calculation_errors(backend, bind, tool, args):
    _, shown = bind(layer("empty", {"type": "FeatureCollection", "features": []}))
    assert "Fehler" in getattr(backend, tool).invoke(args)
    assert shown == []
