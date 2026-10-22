"""File-format regression and server-side Folium/HTML tests (no browser)."""

from gis import data_io
from maps import renderer
from agent import llm as agent_llm
import copy
import json

import pytest
from langchain_core.messages import AIMessage
from shapely.geometry import LineString, Point, Polygon

from tests.conftest import collection, layer, upload


def test_csv():
    result = data_io.parse_uploaded_file(upload("a.csv", "lat,lon,name,value\n51,9,A,7\n"))
    assert result == {"type": "points", "data": [
        {"lat": 51.0, "lon": 9.0, "name": "A", "properties": {"value": 7}}]}


def test_csv_default_name_and_empty():
    parsed = data_io.parse_uploaded_file(upload("a.csv", "lat,lon\n51,9\n"))
    assert parsed["data"][0]["name"] == "Point 1"
    assert data_io.parse_uploaded_file(upload("a.csv", "lat,lon\n"))["data"] == []


@pytest.mark.parametrize("filename,text,error", [
    ("a.csv", "x,y\n1,2\n", ValueError),
    ("a.csv", "lat,lon\nwrong,9\n", ValueError),
    ("a.csv", "", ValueError),
    ("a.json", "{", json.JSONDecodeError),
    ("a.txt", "anything", ValueError),
])
def test_parse_errors(filename, text, error):
    with pytest.raises(error):
        data_io.parse_uploaded_file(upload(filename, text))


@pytest.mark.parametrize("extension", ["json", "geojson", "GEOJSON"])
@pytest.mark.parametrize("geom", [Point(9, 51), LineString([(9, 51), (9.01, 51.01)]),
    Polygon([(9, 51), (9.01, 51), (9.01, 51.01), (9, 51)])])
def test_geojson_roundtrip(extension, geom):
    fc = collection(geom)
    for payload in [fc, fc["features"][0]]:
        result = data_io.parse_uploaded_file(upload("test." + extension, payload))
        assert result == {"type": "geojson", "data": payload}
        gdf = data_io.extract_features_to_gdf(result["data"])
        assert len(gdf) == 1 and gdf.crs.to_epsg() == 4326
        assert gdf.geometry.iloc[0].equals(geom)


def test_json_point_list():
    data = [{"lat": 51, "lon": 9, "properties": {"value": 2}}]
    parsed = data_io.parse_uploaded_file(upload("a.json", data))
    assert parsed == {"type": "points", "data": data}
    assert data_io.extract_features_to_gdf(parsed["data"]).geometry.iloc[0] == Point(9, 51)


@pytest.mark.parametrize("kind", ["points", "heatmap"])
def test_csv_to_tool_to_html(session_agent, bind, kind):
    parsed = data_io.parse_uploaded_file(upload("synthetic.csv", "lat,lon,name,value\n51,9,A,7\n51.01,9.01,B,3\n"))
    _, shown = bind(layer("csv", parsed["data"], parsed["type"]))
    assert "Erfolg" in session_agent.visualize_layer_on_map.invoke({
        "layer_name": "csv", "map_type": kind, "data_field": "value"})
    html = renderer.create_layered_map(shown).get_root().render()
    assert ("L.marker(" if kind == "points" else "L.heatLayer(") in html


@pytest.mark.parametrize("data", [[], {}, {"type": "FeatureCollection", "features": []}, None, "bad"])
def test_empty_or_unsupported_geometry_input(data):
    with pytest.raises(ValueError, match="Keine gültigen"):
        data_io.extract_features_to_gdf(data)


def test_json_unvalidated_shape_characterization():
    assert data_io.parse_uploaded_file(upload("a.json", {})) == {"type": "points", "data": {}}


def test_metadata_mock_and_fallback(session_agent, monkeypatch):
    monkeypatch.setattr(session_agent.llm, "invoke", lambda messages: AIMessage(content=
        '```json\n{"detected_type":"points","data_fields":["value"],"recommended_layer_name":"Demo"}\n```'))
    assert agent_llm.smart_analyze_geodata('a.csv', 'synthetic', llm=session_agent.llm)["recommended_layer_name"] == "Demo"
    def fail(messages):
        raise RuntimeError("synthetic model error")
    monkeypatch.setattr(session_agent.llm, "invoke", fail)
    assert agent_llm.smart_analyze_geodata('a.csv', 'synthetic', llm=session_agent.llm) == {
        "detected_type": "geojson", "data_fields": [], "recommended_layer_name": "a.csv"}


@pytest.mark.parametrize("kind", ["points", "line", "polygon", "heatmap", "choropleth"])
def test_map_html(points, polygons, tmp_path, kind):
    if kind == "line":
        data = collection(LineString([(9, 51), (9.01, 51.01)]))
    elif kind in ["polygon", "choropleth"]:
        data = polygons["data"]
    else:
        data = points["data"]
    entry = layer("synthetic_map", data, kind)
    entry["data_field"] = "value"
    if kind == "heatmap":
        entry["folium_kwargs"] = {"radius": 25, "blur": 15, "min_opacity": 0.3}
    before = copy.deepcopy(entry)
    m = renderer.create_layered_map([entry])
    renderer.Titel_hinzufügen(m, "Synthetic title")
    path = tmp_path / (kind + ".html")
    m.save(str(path))
    html = path.read_text(encoding="utf-8")
    assert "<html" in html and "Synthetic title" in html and "synthetic_map" in html
    assert "L.map(" in html and "L.control.layers(" in html
    if kind == "points":
        assert html.count("L.marker(") == 3
    elif kind == "heatmap":
        assert "L.heatLayer(" in html
    else:
        assert "L.geoJson(" in html
    assert entry == before


def test_map_empty_basemap_and_view(polygons):
    assert "tile.openstreetmap.org" not in renderer.create_layered_map([], show_basemap=False).get_root().render()
    assert "tile.openstreetmap.org" in renderer.create_layered_map([]).get_root().render()
    html = renderer.create_layered_map([polygons], view_updates={
        "min_lat": 50, "min_lon": 8, "max_lat": 52, "max_lon": 10}).get_root().render()
    assert "[[50, 8], [52, 10]]" in html


def test_style_reaches_html(session_agent, bind, polygons):
    bind(shown=[polygons])
    session_agent.update_map_style.invoke({"layer_name": "polygons", "color": "red", "weight": 4, "fill_opacity": 0.25})
    html = renderer.create_layered_map(session_agent.get_agent_layers()[1]).get_root().render()
    assert '"color": "red"' in html and '"fillOpacity": 0.25' in html and '"weight": 4' in html
