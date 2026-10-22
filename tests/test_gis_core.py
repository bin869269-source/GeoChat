"""Phase 3A: directly exercise the real GIS package and its dependency boundary."""

import copy
import os
import subprocess
import sys
import textwrap

import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
import pandas as pd
from pandas.testing import assert_series_equal
import pytest
from shapely.geometry import Point, Polygon

from gis import data_io, geometry, statistics
from tests.conftest import ROOT, collection, upload


def test_core_runs_in_fresh_process_without_application_imports():
    # This process is independent of conftest's dummy key, patched LLM and the
    # parent's already-imported application libraries. Do not inherit secrets.
    code = textwrap.dedent('''
        import importlib.abc
        import os
        import socket
        import sys
        sys.path.insert(0, sys.argv[1])
        assert "DEEPSEEK_API_KEY" not in os.environ
        forbidden = {"backend", "frontend", "agent", "streamlit", "langgraph",
                     "langchain", "langchain_core", "langchain_openai", "openai",
                     "folium", "branca", "dotenv"}
        attempts = []
        class NoApplicationImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in forbidden:
                    attempts.append(fullname)
                    raise AssertionError("Application dependency requested: " + fullname)
        sys.meta_path.insert(0, NoApplicationImports())
        def no_network(*args, **kwargs):
            raise AssertionError("GIS core must run offline")
        socket.socket.connect = no_network
        socket.socket.connect_ex = no_network
        socket.create_connection = no_network
        from gis.data_io import parse_uploaded_file, extract_features_to_gdf
        from gis.geometry import buffer_geometries, pairwise_distances
        from gis.statistics import calculate_area, count_points_in_polygons, calculate_density
        import io
        import geopandas as gpd
        from shapely.geometry import Point, Polygon
        stream = io.StringIO("lat,lon\\n51,9\\n51.001,9.001\\n")
        stream.name = "synthetic.csv"
        points = extract_features_to_gdf(parse_uploaded_file(stream)["data"])
        assert len(buffer_geometries(points, 50)) == 2
        assert len(pairwise_distances(points)) == 1
        square = Polygon([(500000,5650000), (501000,5650000),
                          (501000,5651000), (500000,5651000)])
        polygons = gpd.GeoDataFrame(geometry=[square], crs=25832)
        inside = gpd.GeoDataFrame(geometry=[Point(500500,5650500)], crs=25832)
        areas = calculate_area(polygons)
        assert areas.area_km2.tolist() == [1.0]
        assert count_points_in_polygons(areas, inside).tolist() == [1]
        assert calculate_density(areas, inside).dichte_pro_km2.tolist() == [1.0]
        assert not attempts
        assert not any(name.split(".")[0] in forbidden for name in sys.modules)
        print("GIS core imported and calculated without key, application dependencies or network")
    ''')
    environment = {name: os.environ[name] for name in
                   ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    environment["PROJ_NETWORK"] = "OFF"
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "without key" in result.stdout


@pytest.mark.parametrize("kind", ["collection", "feature", "list"])
def test_conversion_returns_detached_frame(kind):
    fc = collection(Point(9, 51))
    source = {"collection": fc, "feature": fc["features"][0],
              "list": [{"lat": 51, "lon": 9, "properties": {"value": 1}}]}[kind]
    before = copy.deepcopy(source)
    frame = data_io.extract_features_to_gdf(source)
    frame.loc[0, "value"] = 99
    frame.loc[0, "geometry"] = Point(0, 0)
    assert source == before


@pytest.mark.parametrize("name,contents", [
    ("synthetic.csv", "lat,lon,name,value\n51,9,A,7\n"),
    ("synthetic.json", [{"lat": 51, "lon": 9}]),
    ("synthetic.geojson", collection(Point(9, 51))),
])
def test_parser_uses_plain_stream(name, contents):
    stream = upload(name, contents)
    original_text = stream.getvalue()
    parsed = data_io.parse_uploaded_file(stream)
    assert len(data_io.extract_features_to_gdf(parsed["data"])) == 1
    assert stream.getvalue() == original_text
    assert stream.tell() > 0  # Stream position is the documented I/O side effect.


class ScalarLike:
    def item(self):
        return 7


@pytest.mark.parametrize("inplace", [False, True])
def test_scalar_cleaner_mutation_contract(inplace):
    frame = gpd.GeoDataFrame({"value": [ScalarLike()]}, geometry=[Point(9, 51)], crs=4326)
    before = frame.copy(deep=True)
    result = data_io.clean_gdf_for_geojson(frame, inplace=inplace)
    assert result.loc[0, "value"] == 7
    assert (result is frame) is inplace
    if not inplace:
        assert_geodataframe_equal(frame, before)
        assert isinstance(frame.loc[0, "value"], ScalarLike)
    else:
        assert frame.loc[0, "value"] == 7


def test_legacy_cleaner_still_mutates_explicitly():
    from agent.tools import clean_gdf_for_geojson
    frame = gpd.GeoDataFrame({"value": [ScalarLike()]}, geometry=[Point(9, 51)], crs=4326)
    assert clean_gdf_for_geojson(frame) is frame
    assert frame.loc[0, "value"] == 7


def test_formal_data_geometry_statistics_entrypoints():
    parsed = data_io.parse_uploaded_file(upload("source.csv", "lat,lon,name\n51,9,A\n51.01,9.01,B\n"))
    frame = data_io.extract_features_to_gdf(parsed["data"])
    assert len(frame) == 2 and frame.crs.to_epsg() == 4326
    distances = geometry.pairwise_distances(frame)
    assert len(distances) == 1 and distances[0]["distance_m"] > 0
    buffered = geometry.buffer_geometries(frame, 20)
    area = statistics.calculate_area(buffered)
    assert len(area) == 2 and (area["area_km2"] > 0).all()


def test_buffer_retains_projection_and_does_not_mutate():
    points = gpd.GeoDataFrame({"value": [2]}, geometry=[Point(500000, 5650000)], crs=25832)
    before = points.copy(deep=True)
    buffered = geometry.buffer_geometries(points, 50)
    assert buffered.crs.to_epsg() == 4326 and buffered.geom_type.tolist() == ["Polygon"]
    # Compatibility check, not an independent ground-distance oracle.
    expected = points.to_crs(3857).buffer(50).to_crs(4326)
    assert buffered.geometry.iloc[0].equals_exact(expected.iloc[0], 1e-10)
    buffered.loc[0, "value"] = 99
    assert_geodataframe_equal(points, before)


def test_pairwise_minimum_distances_positions_and_nonmutation():
    points = gpd.GeoDataFrame(geometry=[Point(500000, 5650000), Point(500003, 5650004),
                                      Point(500000, 5650010)], crs=25832, index=[20, 5, 8])
    before = points.copy(deep=True)
    assert geometry.pairwise_distances(points) == [
        {"from_index": 0, "to_index": 1, "distance_m": 5.0},
        {"from_index": 1, "to_index": 2, "distance_m": 6.7},
        {"from_index": 0, "to_index": 2, "distance_m": 10.0}]
    assert_geodataframe_equal(points, before)


def test_area_count_density_preserve_inputs_and_indices():
    square = Polygon([(500000, 5650000), (501000, 5650000),
                      (501000, 5651000), (500000, 5651000)])
    polygons = gpd.GeoDataFrame({"name": ["a", "b"]}, geometry=[square, square],
                                crs=25832, index=[7, 42])
    points = gpd.GeoDataFrame(geometry=[Point(500500, 5650500), Point(500000, 5650000),
                                      Point(499000, 5650000)], crs=25832)
    original_polygons, original_points = polygons.copy(deep=True), points.copy(deep=True)
    area = statistics.calculate_area(polygons)
    assert area["area_km2"].tolist() == [1.0, 1.0]
    original_area = area.copy(deep=True)
    counts = statistics.count_points_in_polygons(area, points)
    assert_series_equal(counts, pd.Series([1, 1], index=[7, 42], name="objekt_anzahl"))
    density = statistics.calculate_density(area, points)
    assert density["objekt_anzahl"].tolist() == [1, 1]
    assert density["dichte_pro_km2"].tolist() == [1.0, 1.0]
    density.loc[7, "name"] = "changed output"
    assert_geodataframe_equal(area, original_area)
    assert_geodataframe_equal(polygons, original_polygons)
    assert_geodataframe_equal(points, original_points)


@pytest.mark.parametrize("operation", [
    lambda frame: geometry.buffer_geometries(frame, 5),
    geometry.pairwise_distances,
    statistics.calculate_area,
])
def test_frame_computations_require_explicit_crs(operation):
    frame = gpd.GeoDataFrame(geometry=[Point(9, 51), Point(9.001, 51.001)])
    with pytest.raises(ValueError, match="naive geometries"):
        operation(frame)
