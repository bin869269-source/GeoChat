"""Independent source-CRS, transport and application regression references."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import re

import folium
import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import numpy as np
from pyproj import Transformer
import pytest
from shapely.geometry import Point, Polygon, LineString, MultiPolygon

from agent.graph import create_agent_session
from core.contracts import Dataset, Layer
from core.session import SessionData
from gis.data_io import (extract_features_to_gdf, resolve_source_crs,
                         prepare_map_layers, transform_geodata)
from gis.geometry import pairwise_distances, buffer_geometries
from gis.statistics import calculate_area, calculate_density
from maps.renderer import create_layered_map, render_map_html
from tests.conftest import collection, upload
from tests.test_session_contracts import Model, call
from tests.test_agent_session import make_app

SQUARE = Polygon([(500000, 5650000), (501000, 5650000),
                  (501000, 5651000), (500000, 5651000)])


def frame(geometries, crs=25832):
    return gpd.GeoDataFrame({"value": list(range(1, len(geometries) + 1))}, geometry=geometries, crs=crs)


def legacy(*geometries, name="EPSG:25832"):
    data = collection(*geometries)
    data["crs"] = {"type": "name", "properties": {"name": name}}
    return data


def children(parent):
    for child in parent._children.values():
        yield child
        yield from children(child)


@pytest.mark.parametrize("kind", ["frame", "collection", "feature", "list"])
def test_known_utm_source_and_actual_wgs_transform(kind):
    data = legacy(Point(500000, 5649824.888))
    if kind == "frame":
        source = frame([Point(500000, 5649824.888)])
    elif kind == "feature":
        source = data["features"][0]
        source["crs"] = data["crs"]
    elif kind == "list":
        source = data["features"]
    else:
        source = data
    context = {"source_crs": "EPSG:25832"} if kind == "list" else {}
    actual = extract_features_to_gdf(source, **context)
    assert actual.crs.to_epsg() == 25832
    assert actual.geometry.iloc[0].x == 500000
    point = transform_geodata(actual, 4326).geometry.iloc[0]
    assert point.x == pytest.approx(9, abs=1e-5)
    assert point.y == pytest.approx(51, abs=1e-5)


@pytest.mark.parametrize("name", ["EPSG:25832", "urn:ogc:def:crs:EPSG::25832", "EPSG:4326", "OGC:CRS84"])
def test_supported_named_headers(name):
    data = legacy(Point(9, 51) if name in ("EPSG:4326", "OGC:CRS84") else Point(500000, 5650000), name=name)
    assert extract_features_to_gdf(data).crs is not None


@pytest.mark.parametrize("header", [None, {"type": "link", "properties": {"href": "https://invalid.example/crs"}},
                                  {"type": "name", "properties": {"name": "EPSG:999999"}},
                                  {"type": "name", "properties": {"name": "EPSG:4978"}},
                                  {"type": "name", "properties": None},
                                  {"type": "name", "properties": []}])
def test_unsupported_or_unknown_declaration_never_falls_back(header):
    data = collection(Point(9, 51))
    data["crs"] = header
    with pytest.raises(ValueError, match="CRS"):
        extract_features_to_gdf(data)
    session = SessionData()
    if header is not None:
        with pytest.raises(ValueError, match="CRS"):
            session.add_upload("bad", data)
        assert not session.datasets


def test_conflicting_headers_and_context_rejected_without_mutation():
    data = legacy(Point(500000, 5650000))
    data["features"][0]["crs"] = {"type": "name", "properties": {"name": "EPSG:4326"}}
    before = deepcopy(data)
    for action in (lambda: extract_features_to_gdf(data), lambda: SessionData().add_upload("bad", data)):
        with pytest.raises(ValueError, match="CRS-Konflikt"):
            action()
    assert data == before
    with pytest.raises(ValueError, match="CRS-Konflikt"):
        extract_features_to_gdf(frame([Point(500000, 5650000)]), source_crs=4326)


def test_child_declaration_cannot_silently_relabel_wgs_sibling():
    data = collection(Point(500000, 5650000), Point(9, 51))
    data["features"][0]["crs"] = {"type": "name", "properties": {"name": "EPSG:25832"}}
    for action in (lambda: extract_features_to_gdf(data), lambda: SessionData().add_upload("mixed", data)):
        with pytest.raises(ValueError, match="CRS-Konflikt"):
            action()


def test_unknown_native_frame_and_reliable_explicit_context():
    source = frame([Point(9, 51)], None)
    with pytest.raises(ValueError, match="CRS"):
        extract_features_to_gdf(source)
    actual = extract_features_to_gdf(source, source_crs=4326)
    assert actual.crs.to_epsg() == 4326 and source.crs is None
    with pytest.raises(ValueError, match="CRS"):
        extract_features_to_gdf(collection(Point(9, 51)), source_crs=None)


@pytest.mark.parametrize("data", [{"x": 500000, "y": 5650000}, [{"x": 500000, "y": 5650000}]])
def test_arbitrary_xy_payload_is_not_assigned_wgs84(data):
    with pytest.raises(ValueError, match="CRS"):
        resolve_source_crs(data)
    session = SessionData()
    key = session.add_upload("unknown", data)
    assert session.get_dataset(key).crs is None


@pytest.mark.parametrize("kind", ["frame", "json", "features"])
def test_session_crs_transport_and_analytic_tools(kind):
    source = frame([Point(500000, 5650000), Point(500100, 5650000)])
    session = SessionData()
    if kind == "frame":
        key = session.add_upload("source", source)
    elif kind == "json":
        key = session.add_upload("source", json.loads(source.to_json()))
    else:
        key = session.add_dataset(Dataset("source", source, crs="EPSG:25832", metadata={"format": "features"}), uploaded=True)
    before = session.get_dataset(key)
    agent = create_agent_session(session)
    assert session.raw_view[0]["crs"] == "EPSG:25832"
    assert session.resolve_in([], key)["crs"] == "EPSG:25832"
    agent.calculate_feature_distances.invoke({"layer_name": key})
    assert session.results[-1].success and session.results[-1].metrics["min_distance_m"] == 100
    assert not re.search(r"\bnan\b", session.results[-1].message, re.IGNORECASE)
    agent.visualize_layer_on_map.invoke({"layer_name": key})
    assert session.map_view[0]["crs"] == "EPSG:25832"
    agent.calculate_buffer_analysis.invoke({"layer_name": key, "distance_meters": 50})
    derived = session.get_dataset(session.results[-1].dataset_ids[0])
    assert derived.crs == "EPSG:4326" and not derived.data.geometry.is_empty.any()
    assert np.isfinite(derived.data.total_bounds).all() and derived.parent_ids == (key,)
    after = session.get_dataset(key)
    assert after.crs == before.crs
    if kind == "json":
        assert after.data == before.data
    else:
        assert_geodataframe_equal(after.data, before.data)


@pytest.mark.parametrize("poly_crs,point_crs", [(25832, 25832), (4326, 25832), (25832, 4326)])
def test_area_density_mixed_crs_matches_independent_square(poly_crs, point_crs):
    source = frame([SQUARE]).to_crs(poly_crs)
    points = frame([Point(500500, 5650500)]).to_crs(point_crs)
    session = SessionData()
    polygon_id = session.add_upload("square", source)
    point_id = session.add_upload("inside", points)
    agent = create_agent_session(session)
    agent.calculate_area_and_density.invoke({"polygon_layer_name": polygon_id, "point_layer_name": point_id})
    result = session.results[-1]
    assert result.success
    data = session.get_dataset(result.dataset_ids[0])
    assert data.crs == source.crs.to_string()
    assert data.data.area_km2.tolist() == [1]
    assert data.data.objekt_anzahl.tolist() == [1]
    assert data.data.dichte_pro_km2.tolist() == [1]
    assert set(data.parent_ids) == {polygon_id, point_id}


@pytest.mark.parametrize("kind", ["points", "heatmap", "geojson", "choropleth"])
def test_wgs_map_copy_uses_correct_position_and_retains_source(kind):
    data = legacy(Point(500000, 5649824.888))
    original = deepcopy(data)
    layer = {"name": "projected", "type": kind, "data": data, "crs": "EPSG:25832",
             "layer_id": "stable-layer", "dataset_id": "stable-data", "data_field": "value"}
    with pytest.raises(ValueError, match="4326"):
        create_layered_map([layer], show_basemap=False)
    prepared = prepare_map_layers([layer])
    coords = prepared[0]["data"]["features"][0]["geometry"]["coordinates"]
    assert coords[0] == pytest.approx(9, abs=1e-5) and coords[1] == pytest.approx(51, abs=1e-5)
    assert prepared[0]["layer_id"] == "stable-layer" and prepared[0]["dataset_id"] == "stable-data"
    assert data == original and layer["crs"] == "EPSG:25832"
    rendered = create_layered_map(prepared, show_basemap=False)
    bounds = next(child.bounds for child in children(rendered) if isinstance(child, folium.map.FitBounds))
    assert bounds[0][0] == pytest.approx(51, abs=1e-5) and bounds[0][1] == pytest.approx(9, abs=1e-5)
    assert b"L.map(" in render_map_html(rendered)


@pytest.mark.parametrize("kind", ["collection", "features", "frame"])
def test_repeat_map_preparation_never_projects_wgs_coordinates_twice(kind):
    source = legacy(Point(500000, 5649824.888))
    data = source if kind == "collection" else source["features"] if kind == "features" else frame([Point(500000, 5649824.888)])
    original = deepcopy(data)
    view = [{"name": "source", "type": "geojson", "data": data, "crs": "EPSG:25832",
             "dataset_id": "fixed-data", "layer_id": "fixed-layer", "folium_kwargs": {"color": "red"}}]
    once = prepare_map_layers(view)
    twice = prepare_map_layers(once)
    assert once == twice
    wire = twice[0]["data"]
    point = wire[0]["geometry"] if isinstance(wire, list) else wire["features"][0]["geometry"]
    assert point["coordinates"][0] == pytest.approx(9, abs=1e-5)
    assert point["coordinates"][1] == pytest.approx(51, abs=1e-5)
    assert twice[0]["layer_id"] == "fixed-layer" and twice[0]["folium_kwargs"] == {"color": "red"}
    if kind == "frame":
        assert_geodataframe_equal(data, original)
    else:
        assert data == original


@pytest.mark.parametrize("geometry", [LineString([(500000, 5650000), (500100, 5650100)]),
                                    SQUARE, MultiPolygon([SQUARE])])
def test_projected_line_polygon_multipolygon_map(geometry):
    source = legacy(geometry)
    result = prepare_map_layers([{"name": "shape", "type": "geojson", "data": source}])
    rendered = create_layered_map(result, show_basemap=False)
    geo = next(child for child in children(rendered) if isinstance(child, folium.GeoJson))
    assert geo.data["features"][0]["geometry"]["type"] == geometry.geom_type
    bounds = next(child.bounds for child in children(rendered) if isinstance(child, folium.map.FitBounds))
    assert 50 < bounds[0][0] < 52 and 8 < bounds[0][1] < 10


def test_native_frame_nested_attributes_not_shared_after_conversion():
    source = frame([Point(500000, 5650000)])
    source["nested"] = [{"tags": [1]}]
    output = extract_features_to_gdf(source)
    output.loc[0, "nested"]["tags"].append(2)
    output.loc[0, "geometry"] = Point(0, 0)
    assert source.loc[0, "nested"] == {"tags": [1]}
    assert source.geometry.iloc[0].x == 500000 and source.crs.to_epsg() == 25832


def test_point_protocol_properties_and_projected_feature_attributes_survive():
    points = [{"lat": 51, "lon": 9, "crs": "ordinary attribute", "bbox": "attribute",
               "properties": {"Beschriftung": [1]}}]
    layers = [{"name": "csv", "type": "points", "data": points}]
    result = prepare_map_layers(layers)
    assert result[0]["data"] == points
    result[0]["data"][0]["properties"]["Beschriftung"].append(2)
    assert points[0]["properties"]["Beschriftung"] == [1]
    source = legacy(Point(500000, 5650000))
    source["bbox"] = [500000, 5650000, 500000, 5650000]
    source["features"][0].update(id="stable-feature", bbox=source["bbox"])
    source["features"][0]["properties"]["crs"] = "ordinary feature attribute"
    styled = {"name": "utm", "type": "geojson", "data": source, "folium_kwargs": {"color": "red", "weight": 4}}
    output = prepare_map_layers([styled])[0]
    assert output["folium_kwargs"] == styled["folium_kwargs"]
    assert output["data"]["features"][0]["id"] == "stable-feature"
    assert output["data"]["features"][0]["properties"] == source["features"][0]["properties"]
    assert "bbox" not in output["data"] and "bbox" not in output["data"]["features"][0]
    assert "bbox" in source


def test_dataset_conflicts_rejected_and_legacy_display_infers_real_crs():
    source = frame([Point(500000, 5650000)])
    session = SessionData()
    with pytest.raises(ValueError, match="CRS-Konflikt"):
        session.add_dataset(Dataset("wrong", source))
    with pytest.raises(ValueError, match="CRS-Konflikt"):
        session.add_upload("wrong", source, crs=4326)
    assert not session.datasets
    imported = SessionData.from_legacy(shown=[{"name": "native", "type": "geojson", "data": source}])
    assert next(iter(imported.datasets.values())).crs == "EPSG:25832"


def test_pending_view_crs_conflict_does_not_partially_publish():
    session = SessionData()
    source = frame([SQUARE])
    candidate = {"name": "wrong", "type": "geojson", "data": source, "crs": "EPSG:4326"}
    with pytest.raises(ValueError, match="CRS-Konflikt"):
        session.commit_views([], [candidate], message="Erfolg: pending", tool_name="synthetic")
    assert not session.datasets and not session.layers and not session.results
    native = {**candidate, "crs": "EPSG:25832"}
    session.replace_from_legacy([], [native])
    assert next(iter(session.datasets.values())).crs == "EPSG:25832"


@pytest.mark.parametrize("tool,args", [
    ("calculate_buffer_analysis", {"layer_name": "unknown"}),
    ("calculate_feature_distances", {"layer_name": "unknown"}),
    ("calculate_area_and_density", {"polygon_layer_name": "unknown"}),
    ("visualize_layer_on_map", {"layer_name": "unknown"}),
    ("run_machine_learning_spatial_prediction", {"layer_name": "unknown", "target_field": "value"}),
])
def test_unknown_crs_tool_failure_does_not_publish(tool, args):
    session = SessionData()
    key = session.add_upload("unknown", frame([Point(9, 51), Point(9.001, 51)], None))
    agent = create_agent_session(session)
    before = session.get_dataset(key)
    output = getattr(agent, tool).invoke(args)
    assert "CRS" in output and not session.results[-1].success
    assert not session.layers and len(session.datasets) == 1
    assert session.raw_view[0]["crs"] is None
    assert_geodataframe_equal(session.get_dataset(key).data, before.data)
    with pytest.raises(ValueError, match="CRS"):
        prepare_map_layers([{"name": "bad", "type": "geojson", "data": collection(Point(9, 51)), "crs": None}])


@pytest.mark.parametrize("crs,point", [(4326, Point(500000, 5650000)), (25832, Point(1e100, 1e100))])
def test_wrong_coordinate_interpretation_cannot_succeed(crs, point):
    source = frame([point, Point(point.x + 1, point.y + 1)], crs)
    session = SessionData()
    session.add_upload("bad", source)
    agent = create_agent_session(session)
    for tool, args in [(agent.calculate_buffer_analysis, {"layer_name": "bad"}),
                       (agent.calculate_feature_distances, {"layer_name": "bad"}),
                       (agent.calculate_area_and_density, {"polygon_layer_name": "bad"})]:
        assert "Fehler" in tool.invoke(args)
        assert not session.results[-1].success
    assert len(session.datasets) == 1 and not session.layers


@pytest.mark.parametrize("async_mode", [False, True])
def test_multistep_graph_consumes_derived_crs(async_mode):
    session = SessionData()
    key = session.add_upload("utm", frame([Point(500000, 5650000)]))
    model = Model([AIMessage(content="", tool_calls=[call("calculate_buffer_analysis",
                   {"layer_name": key, "distance_meters": 50}, "a")]),
                   AIMessage(content="", tool_calls=[call("calculate_area_and_density",
                   {"polygon_layer_name": "Puffer_50m_utm"}, "b")]), AIMessage(content="done")])
    agent = create_agent_session(session, model_factory=lambda: model)
    state = {"messages": [HumanMessage(content="buffer then area")]}
    result = asyncio.run(agent.agent_app.ainvoke(state)) if async_mode else agent.agent_app.invoke(state)
    assert result["messages"][-1].content == "done"
    assert len([m for m in result["messages"] if isinstance(m, ToolMessage)]) == 2
    assert all(r.success for r in session.results) and len(session.datasets) == 3
    assert all(d.crs == "EPSG:4326" for d in session.datasets.values() if d.dataset_id != key)
    assert session.get_dataset(key).crs == "EPSG:25832"


def test_projected_and_geographic_sessions_remain_isolated():
    agents = [create_agent_session() for _ in range(2)]
    source = frame([Point(500000, 5650000), Point(500100, 5650000)])
    keys = [a.session.add_upload("same", source if i == 0 else source.to_crs(4326)) for i, a in enumerate(agents)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: pair[0].calculate_feature_distances.invoke({"layer_name": pair[1]}), zip(agents, keys)))
    assert all("100.0 m" in r for r in results)
    assert keys[0] not in agents[1].session.datasets
    agents[0].session.reset()
    assert agents[1].session.get_dataset(keys[1]).crs == "EPSG:4326"


def test_random_forest_uses_wgs_features_and_preserves_projected_source():
    source = frame([Point(500000, 5650000), Point(500100, 5650000), Point(500200, 5650000)])
    agent = create_agent_session()
    key = agent.session.add_upload("utm", source)
    agent.run_machine_learning_spatial_prediction.invoke({"layer_name": key, "target_field": "value"})
    assert agent.session.results[-1].success
    result = agent.session.get_dataset(agent.session.results[-1].dataset_ids[0])
    assert result.crs == "EPSG:4326" and result.data["lon"].between(8, 10).all()
    assert result.data["lat"].between(50, 52).all()
    assert_geodataframe_equal(agent.session.get_dataset(key).data, source)


def test_real_frontend_projected_upload_analysis_and_render(session_agent, monkeypatch):
    import streamlit
    data = legacy(Point(500000, 5650000), Point(500100, 5650000))
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [upload("utm.geojson", data)])
    monkeypatch.setattr(session_agent.llm, "invoke", lambda messages: AIMessage(content=
                        '{"detected_type":"geojson","recommended_layer_name":"UTM","data_fields":["value"]}'))
    app = make_app(session_agent)
    assert not app.exception
    key = next(iter(session_agent.session.datasets))
    assert session_agent.session.get_dataset(key).crs == "EPSG:25832"
    session_agent.visualize_layer_on_map.invoke({"layer_name": key, "map_type": "points"})
    app.run()
    assert not app.exception
    session_agent.calculate_feature_distances.invoke({"layer_name": key})
    assert session_agent.session.results[-1].metrics["min_distance_m"] == 100


@pytest.mark.parametrize("data", [legacy(Point(9, 51), name="EPSG:999999"),
                                 {**collection(Point(9, 51)), "crs": None}])
def test_frontend_bad_crs_is_visible_error_not_dataset(session_agent, monkeypatch, data):
    import streamlit
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [upload("bad.geojson", data)])
    app = make_app(session_agent)
    assert not app.exception and not session_agent.session.datasets
    assert any("CRS" in error.value for error in app.error)
