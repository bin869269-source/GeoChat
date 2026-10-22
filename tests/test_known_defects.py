"""Correct expected behavior for confirmed legacy defects; strict XPASS fails CI."""
import folium
import pytest

from tests.conftest import collection, layer, upload


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-LOOKUP: shown layers hide raw layers in buffer lookup")
def test_buffer_can_read_raw_when_other_layer_visible(backend, bind, points, polygons):
    _, shown = bind(points, shown=[polygons])
    result = backend.calculate_buffer_analysis.invoke({"layer_name": "points"})
    assert "Erfolg" in result and len(shown) == 2


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-MISSING-POINTS: missing requested point layer silently becomes area-only success")
def test_missing_density_input_is_error(backend, bind, polygons):
    _, shown = bind(polygons)
    output = backend.calculate_area_and_density.invoke({"polygon_layer_name": "polygons", "point_layer_name": "missing"})
    assert "nicht gefunden" in output and shown == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-WEIGHT: if weight rejects zero")
def test_zero_line_weight(backend, bind, polygons):
    polygons["folium_kwargs"] = {"weight": 2}
    bind(shown=[polygons])
    backend.update_map_style.invoke({"layer_name": "polygons", "weight": 0})
    assert polygons["folium_kwargs"]["weight"] == 0


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-FEATURE: single Feature renders no points")
def test_single_feature_point_rendering(backend, points):
    m = folium.Map()
    bounds = backend.add_points_layer(m, points["data"]["features"][0], "single")
    assert bounds == [[51.0, 9.0]]


def test_styled_heatmap_still_exports(backend, bind, points):
    # Folium 0.20 accepts extra style kwargs. HTML generation works; actual
    # browser interpretation of vector keys is outside this server-side test.
    _, shown = bind(points)
    backend.visualize_layer_on_map.invoke({"layer_name": "points", "map_type": "heatmap"})
    backend.update_map_style.invoke({"layer_name": shown[0]["name"], "color": "red"})
    assert "L.heatLayer(" in backend.create_layered_map(shown).get_root().render()


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-COORDINATES: CSV latitude outside valid range accepted")
def test_invalid_coordinate_rejected(backend):
    try:
        backend.parse_uploaded_file(upload("invalid.csv", "lat,lon\n100,9\n"))
    except ValueError:
        return
    assert False, "Latitude 100 must not be accepted as valid WGS84 input"
