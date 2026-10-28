"""Phase 3B renderer tests: real Folium/HTML, without browser validation."""
import copy
import io
import os
import subprocess
import sys
import textwrap

import folium
from folium.plugins import HeatMap
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from maps import renderer
from tests.conftest import ROOT, collection, layer


def configured_layer(kind):
    square = Polygon([(9, 51), (9.01, 51), (9.01, 51.01), (9, 51)])
    geometries = {
        "points": Point(9, 51),
        "line": LineString([(9, 51), (9.01, 51.01)]),
        "polygon": square,
        "multipolygon": MultiPolygon([square]),
        "heatmap": Point(9, 51),
        "choropleth": square,
    }
    entry = layer("test_" + kind, collection(geometries[kind]), kind)
    entry["data_field"] = "value"
    if kind == "heatmap":
        entry["folium_kwargs"] = {"radius": 25, "blur": 15, "min_opacity": 0.3}
    return entry


def test_renderer_independent_in_fresh_process():
    code = textwrap.dedent('''
        import importlib.abc
        import os
        import socket
        import sys
        sys.path.insert(0, sys.argv[1])
        assert "DEEPSEEK_API_KEY" not in os.environ
        forbidden = {"backend", "frontend", "agent", "streamlit", "langgraph",
                     "langchain", "langchain_core", "langchain_openai", "openai", "dotenv", "gis"}
        attempts = []
        class BlockApplicationImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in forbidden:
                    attempts.append(fullname)
                    raise AssertionError("Application import forbidden: " + fullname)
        sys.meta_path.insert(0, BlockApplicationImports())
        def no_network(*args, **kwargs):
            attempts.append("network")
            raise AssertionError("Renderer must not fetch external assets")
        socket.socket.connect = no_network
        socket.socket.connect_ex = no_network
        socket.create_connection = no_network
        from maps.renderer import create_layered_map, render_map_html, Titel_hinzuf\u00fcgen
        data = {"type":"FeatureCollection", "features":[{
            "type":"Feature", "geometry":{"type":"Point", "coordinates":[9,51]},
            "properties":{"name":"independent"}}]}
        m = create_layered_map([{"name":"independent", "type":"points", "data":data}])
        Titel_hinzuf\u00fcgen(m, "standalone title")
        html = render_map_html(m)
        assert b"L.marker(" in html and b"standalone title" in html
        assert not attempts
        assert not any(n.split(".")[0] in forbidden for n in sys.modules)
        print("renderer independent OK")
    ''')
    environment = {name: os.environ[name] for name in
                   ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "renderer independent OK" in result.stdout


@pytest.mark.parametrize("kind", ["points", "line", "polygon", "multipolygon", "heatmap", "choropleth"])
def test_map_types_render_without_mutating_layers(kind):
    entry = configured_layer(kind)
    before = copy.deepcopy(entry)
    m = renderer.create_layered_map([entry])
    html = renderer.render_map_html(m).decode("utf-8")
    assert entry == before
    assert entry["name"] in html and "L.control.layers(" in html
    expected = {"points": "L.marker(", "heatmap": "L.heatLayer("}.get(kind, "L.geoJson(")
    assert expected in html
    if kind == "multipolygon":
        assert '"MultiPolygon"' in html


@pytest.mark.parametrize("kind", ["polygon", "choropleth"])
def test_folium_identifier_injection_stays_in_render_copy(kind):
    entry = configured_layer(kind)
    feature = entry["data"]["features"][0]
    entry["data"]["features"] = [copy.deepcopy(feature), copy.deepcopy(feature)]
    # Duplicate properties force Folium to assign feature.id instead of using
    # a unique property. Old code could inject these IDs into the caller dict.
    before = copy.deepcopy(entry)
    m = renderer.create_layered_map([entry])
    html = renderer.render_map_html(m)
    assert html and entry == before
    group = next(c for c in m._children.values() if isinstance(c, folium.FeatureGroup))
    geojson = next(c for c in group._children.values() if isinstance(c, folium.GeoJson))
    assert [f["id"] for f in geojson.data["features"]] == ["0", "1"]
    assert all("id" not in f for f in entry["data"]["features"])


def test_formal_renderer_entries_and_default_style():
    names = ["create_layered_map", "add_points_layer", "add_heatmap_layer",
             "add_choropleth_layer", "add_geojson_layer", "Titel_hinzufügen", "render_map_html"]
    for name in names:
        assert callable(getattr(renderer, name))
        assert getattr(renderer, name).__module__ == "maps.renderer"
    assert renderer.DEFAULT_MAP_STYLE == {"color": "blue", "weight": 2, "fillColor": "blue", "fillOpacity": 0.4}


def test_layer_order_and_control():
    kinds = ["points", "line", "polygon", "multipolygon", "heatmap", "choropleth"]
    entries = [configured_layer(k) for k in kinds]
    m = renderer.create_layered_map(entries)
    overlays = [c.layer_name for c in m._children.values() if isinstance(c, (folium.FeatureGroup, HeatMap))]
    assert overlays == [e["name"] for e in entries]
    assert len([c for c in m._children.values() if isinstance(c, folium.LayerControl)]) == 1
    basemap = next(c for c in m._children.values() if isinstance(c, folium.TileLayer))
    assert basemap.layer_name == "OpenStreetMap" and basemap.control is False


def test_default_vector_style_and_custom_style():
    entry = configured_layer("polygon")
    for style in [renderer.DEFAULT_MAP_STYLE.copy(),
                  {"color": "red", "weight": 4, "fillColor": "orange", "fillOpacity": 0.25}]:
        entry["folium_kwargs"] = style
        m = renderer.create_layered_map([entry])
        group = next(c for c in m._children.values() if isinstance(c, folium.FeatureGroup))
        geojson = next(c for c in group._children.values() if isinstance(c, folium.GeoJson))
        assert geojson.style_function(entry["data"]["features"][0]) == style


def test_choropleth_palette_and_missing_value():
    entry = configured_layer("choropleth")
    first = entry["data"]["features"][0]
    second = copy.deepcopy(first)
    second["properties"]["value"] = 2
    third = copy.deepcopy(first)
    third["properties"] = {"name": "missing"}
    entry["data"]["features"] = [first, second, third]
    entry["folium_kwargs"] = {"fillColor": "Blues", "weight": 3}
    m = renderer.create_layered_map([entry])
    group = next(c for c in m._children.values() if isinstance(c, folium.FeatureGroup))
    geojson = next(c for c in group._children.values() if isinstance(c, folium.GeoJson))
    tooltip = next(c for c in geojson._children.values() if isinstance(c, folium.GeoJsonTooltip))
    assert tooltip.fields == ["name", "value"]
    assert tooltip.aliases == ["Name:", "value:"]
    assert geojson.style_function(first)["fillColor"].startswith("#f7fbff")
    assert geojson.style_function(second)["fillColor"].startswith("#08306b")
    assert geojson.style_function(third)["fillColor"] == "#cccccc"
    html = renderer.render_map_html(m).decode("utf-8")
    assert "test_choropleth - value" in html
    assert "bindTooltip" in html and '"value"' in html


@pytest.mark.parametrize("field,match", [("missing", "nicht gefunden"),
                                          ("name", "keine gültigen numerischen Werte")])
def test_choropleth_rejects_unusable_field(field, match):
    entry = configured_layer("choropleth")
    entry["data_field"] = field
    with pytest.raises(ValueError, match=match):
        renderer.create_layered_map([entry])


def test_choropleth_matches_field_name_case_insensitively():
    entry = configured_layer("choropleth")
    entry["data_field"] = "VALUE"
    m = renderer.create_layered_map([entry])
    group = next(c for c in m._children.values() if isinstance(c, folium.FeatureGroup))
    geojson = next(c for c in group._children.values() if isinstance(c, folium.GeoJson))
    assert geojson.style_function(entry["data"]["features"][0])["fillColor"] != "#cccccc"


def test_choropleth_tooltip_uses_only_name_and_mapped_value():
    entry = configured_layer("choropleth")
    for feature in entry["data"]["features"]:
        feature["properties"].pop("name")
        feature["properties"]["stadtteilname"] = "Altstadt"
    m = renderer.create_layered_map([entry])
    group = next(c for c in m._children.values() if isinstance(c, folium.FeatureGroup))
    geojson = next(c for c in group._children.values() if isinstance(c, folium.GeoJson))
    tooltip = next(c for c in geojson._children.values() if isinstance(c, folium.GeoJsonTooltip))
    assert tooltip.fields == ["stadtteilname", "value"]
    assert tooltip.aliases == ["Name:", "value:"]


def test_latest_layer_bounds_and_explicit_override():
    entries = [layer("far", collection(Point(2, 20)), "points"),
               layer("last", collection(Point(9, 51), Point(10, 52)), "points")]
    m = renderer.create_layered_map(entries)
    bounds = next(c for c in m._children.values() if isinstance(c, folium.map.FitBounds))
    assert bounds.bounds == [[51.0, 9.0], [52.0, 10.0]]
    override = {"min_lat": 1, "min_lon": 2, "max_lat": 3, "max_lon": 4}
    m = renderer.create_layered_map(entries, view_updates=override)
    bounds = next(c for c in m._children.values() if isinstance(c, folium.map.FitBounds))
    assert bounds.bounds == [[1, 2], [3, 4]]


def test_empty_map_and_empty_layer_keep_original_view_rules():
    m = renderer.create_layered_map([], view_updates={"min_lat": 1}, show_basemap=False)
    assert m.location == [51.1657, 10.4515] and m.options["zoom"] == 6
    assert not any(isinstance(c, (folium.LayerControl, folium.map.FitBounds, folium.TileLayer)) for c in m._children.values())
    m = renderer.create_layered_map([layer("empty", [], "points")])
    bounds = next(c for c in m._children.values() if isinstance(c, folium.map.FitBounds))
    assert bounds.bounds == [[50.5, 9.5], [52.5, 11.5]]
    assert "L.control.layers(" in renderer.render_map_html(m).decode("utf-8")


@pytest.mark.parametrize("kind", ["points", "line", "polygon", "multipolygon", "heatmap", "choropleth"])
def test_memory_html_matches_folium_save_bytes(kind, tmp_path):
    m = renderer.create_layered_map([configured_layer(kind)])
    renderer.Titel_hinzufügen(m, "地图 · Dresden")
    # Folium render populates generated children; compare the first render of
    # identical map states, retaining IDs, rather than rendering one map thrice.
    buffer_map, disk_map = copy.deepcopy(m), copy.deepcopy(m)
    payload = renderer.render_map_html(m)
    buffer = io.BytesIO()
    buffer_map.save(buffer, close_file=False)
    path = tmp_path / "reference.html"
    disk_map.save(str(path))
    assert payload == buffer.getvalue() == path.read_bytes()
    assert "地图 · Dresden" in payload.decode("utf-8")


def test_separate_maps_export_separate_bytes():
    first = renderer.create_layered_map([])
    second = renderer.create_layered_map([])
    renderer.Titel_hinzufügen(first, "first_export_only")
    renderer.Titel_hinzufügen(second, "second_export_only")
    a, b = renderer.render_map_html(first), renderer.render_map_html(second)
    assert b"first_export_only" in a and b"second_export_only" not in a
    assert b"second_export_only" in b and b"first_export_only" not in b


@pytest.mark.parametrize("entry,error", [
    (layer("bad", None, "points"), TypeError),
    (dict(layer("bad", [{"lat": 51, "lon": 9, "properties": {"value": "bad"}}], "heatmap"),
          data_field="value", folium_kwargs={}), ValueError),
    (layer("bad", '{"type": ', "geojson"), ValueError),
])
def test_malformed_input_is_not_silently_reported_as_render_success(entry, error):
    before = copy.deepcopy(entry)
    with pytest.raises(error):
        renderer.render_map_html(renderer.create_layered_map([entry]))
    assert entry == before
