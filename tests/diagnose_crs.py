r"""Offline Phase 4C observations, separate from pytest and from proposed fixes.

Run: .venv\Scripts\python.exe tests/diagnose_crs.py
Synthetic data only. A reproduced defect is recorded as such, not as Passed.
No application file or Git state is changed; JSON evidence goes in ignored logs.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import io
import json
import math
import os
from pathlib import Path
import socket
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PROJ_NETWORK"] = "OFF"
for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(key, None)


def no_network(*args, **kwargs):
    raise AssertionError("CRS diagnostics must stay offline")


socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network

import folium
import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
import pyproj
from pyproj import Geod, Transformer
import shapely
from shapely.geometry import Point, Polygon, mapping

from agent.graph import create_agent_session
from core.contracts import Dataset, Layer
from core.session import SessionData
from gis.data_io import extract_features_to_gdf, parse_uploaded_file, prepare_map_layers
from gis.geometry import buffer_geometries, pairwise_distances
from gis.statistics import calculate_area, calculate_density, count_points_in_polygons
from maps.renderer import create_layered_map, render_map_html

TO_WGS = Transformer.from_crs(25832, 4326, always_xy=True)
TO_UTM = Transformer.from_crs(4326, 25832, always_xy=True)
X, Y = TO_UTM.transform(9, 51)
SQUARE = Polygon([(500000, 5650000), (501000, 5650000),
                  (501000, 5651000), (500000, 5651000)])
RESULTS = []


def fc(*geometries, crs=None):
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(g), "properties": {"value": i + 1}}
        for i, g in enumerate(geometries)]}
    if crs is not None:
        data["crs"] = {"type": "name", "properties": {"name": crs}}
    return data


def stream(name, contents):
    value = io.StringIO(contents if isinstance(contents, str) else json.dumps(contents))
    value.name = name
    return value


def frame(geometries, crs):
    return gpd.GeoDataFrame({"value": list(range(1, len(geometries) + 1))},
                            geometry=geometries, crs=crs)


def attempt(function):
    try:
        return {"accepted": True, "result": function()}
    except (ValueError, TypeError, NotImplementedError) as exc:
        return {"accepted": False, "error": type(exc).__name__, "message": str(exc)}


def probe(identifier, reference, function, known=None):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            met, observed = function()
            status = ("Reference Met" if met is True else
                      "Inconclusive" if met is None else
                      "Known Defect" if known else "Risk Reproduced")
        except Exception as exc:
            met, status, observed = None, "Diagnostic Error", {
                "error": type(exc).__name__, "message": str(exc)}
        record = {"id": identifier, "reference": reference, "reference_met": met,
                  "status": status, "known_defect": known, "observed": observed,
                  "warnings": [{"category": w.category.__name__, "message": str(w.message)}
                               for w in caught]}
    RESULTS.append(record)
    print(f"{identifier}: {status}")


def parsed_wgs():
    parsed = parse_uploaded_file(stream("wgs.geojson", fc(Point(9, 51))))
    result = extract_features_to_gdf(parsed["data"])
    return result.crs.to_epsg() == 4326 and result.geometry.iloc[0].equals(Point(9, 51)), {
        "crs": str(result.crs), "xy": list(result.geometry.iloc[0].coords)[0]}


def declared_projected():
    source = fc(Point(X, Y), crs="EPSG:25832")
    before = deepcopy(source)
    result = extract_features_to_gdf(parse_uploaded_file(stream("utm.geojson", source))["data"])
    actual = result.to_crs(4326).geometry.iloc[0]
    assert source == before
    return actual.distance(Point(9, 51)) < 1e-8, {
        "declared_crs": "EPSG:25832", "result_crs": str(result.crs),
        "actual_xy": list(actual.coords)[0], "independent_expected_lonlat": [9, 51],
        "source_unchanged": True}


def uncertain_declaration(kind):
    source = fc(Point(X, Y))
    if kind == "null":
        source["crs"] = None
    elif kind == "invalid":
        source["crs"] = {"type": "name", "properties": {"name": "NOT_A_CRS"}}
    else:
        source["features"][0]["crs"] = {"type": "name", "properties": {"name": "EPSG:25832"}}
    result = attempt(lambda: str(extract_features_to_gdf(source).crs))
    return not result["accepted"] or result.get("result") != "EPSG:4326", result


def csv_coordinates(kind):
    text = {"latlon": "lat,lon,crs\n51,9,EPSG:25832\n",
            "xy": "x,y\n500000,5650000\n",
            "invalid": "lat,lon\n100,9\n"}[kind]
    def convert():
        parsed = parse_uploaded_file(stream("synthetic.csv", text))
        result = extract_features_to_gdf(parsed["data"])
        return {"crs": str(result.crs), "xy": list(result.geometry.iloc[0].coords)[0],
                "properties": parsed["data"][0]["properties"]}
    result = attempt(convert)
    return (not result["accepted"] if kind != "latlon" else result["accepted"]), result


def native_conversion(crs):
    source = frame([Point(9, 51) if crs == 4326 else Point(X, Y)], crs)
    before = source.copy(deep=True)
    result = attempt(lambda: str(extract_features_to_gdf(source).crs))
    assert_geodataframe_equal(source, before)
    met = not result["accepted"] if crs is None else result.get("result") == str(source.crs)
    return met, {**result, "source_unchanged": True}


def direct_core():
    points = frame([Point(500000, 5650000), Point(500100, 5650000)], 25832)
    polys = frame([SQUARE], 25832)
    inside = frame([Point(500500, 5650500)], 25832)
    originals = [f.copy(deep=True) for f in (points, polys, inside)]
    distance = pairwise_distances(points)[0]["distance_m"]
    area = calculate_area(polys)
    density = calculate_density(area, inside)
    mixed_count = count_points_in_polygons(polys, inside.to_crs(4326)).tolist()
    buf = buffer_geometries(points, 500)
    # Reference is analytic planar distance/area/count, not the algorithm under test.
    met = distance == 100 and area.area_km2.tolist() == [1] and mixed_count == [1]
    met &= density.dichte_pro_km2.tolist() == [1] and buf.crs.to_epsg() == 4326
    for original, current in zip(originals, (points, polys, inside)):
        assert_geodataframe_equal(current, original)
    return bool(met), {"distance_m": distance, "area_km2": area.area_km2.tolist(),
                       "count_mixed_crs": mixed_count, "density_per_km2": density.dichte_pro_km2.tolist(),
                       "area_output_crs": str(area.crs), "buffer_output_crs": str(buf.crs),
                       "inputs_unchanged": True}


def naive_core():
    polys = frame([SQUARE], None)
    points = frame([Point(500500, 5650500), Point(500600, 5650500)], None)
    areas = polys.assign(area_km2=1)
    calls = {"buffer": lambda: buffer_geometries(points, 50),
             "distance": lambda: pairwise_distances(points),
             "area": lambda: calculate_area(polys),
             "count": lambda: count_points_in_polygons(polys, points),
             "density": lambda: calculate_density(areas, points)}
    observed = {name: attempt(call) for name, call in calls.items()}
    return all(not r["accepted"] for r in observed.values()), observed


def session_view(crs):
    source = frame([Point(X, Y)], crs)
    session = SessionData()
    key = session.add_upload("native", source)
    session.add_layer(Layer(key, "native"))
    dataset = session.get_dataset(key)
    view = session.map_view[0]
    decoded = attempt(lambda: str(extract_features_to_gdf(view["data"], source_crs=view.get("crs")).crs))
    met = not decoded["accepted"] if crs is None else decoded.get("result") == str(source.crs)
    return met, {
        "dataset_crs": dataset.crs, "data_crs": str(dataset.data.crs),
        "view_explicit_crs": view.get("crs"), "payload_crs": view["data"].get("crs"),
        "decode": decoded}


def contract_mismatch(legacy=False):
    source = frame([Point(X, Y)], 25832)
    if legacy:
        session = SessionData.from_legacy(shown=[{"name": "native", "type": "geojson", "data": source}])
        dataset = next(iter(session.datasets.values()))
    else:
        session = SessionData()
        try:
            key = session.add_dataset(Dataset("native", source))
        except ValueError as exc:
            return "CRS" in str(exc), {"conflicting_dataset_rejected": True, "message": str(exc)}
        dataset = session.get_dataset(key)
    return dataset.crs == str(dataset.data.crs), {
        "dataset_crs": dataset.crs, "data_crs": str(dataset.data.crs)}


def all_children(parent):
    for child in parent._children.values():
        yield child
        yield from all_children(child)


def map_projection(kind, native=False):
    source = frame([Point(X, Y)], 25832) if native else fc(Point(X, Y), crs="EPSG:25832")
    layers = [{"name": "projected", "type": kind, "data": source, "data_field": "value"}]
    if native:
        m = create_layered_map(layers, show_basemap=False)
        direct = {"accepted": True, "result": True}
    else:
        direct = attempt(lambda: bool(create_layered_map(layers, show_basemap=False)))
        # Phase 4D's real UI and export boundary explicitly prepare WGS84 copies.
        m = create_layered_map(prepare_map_layers(layers), show_basemap=False)
    children = list(all_children(m))
    geo = next((c for c in children if isinstance(c, folium.GeoJson)), None)
    if geo is not None:
        xy = geo.data["features"][0]["geometry"]["coordinates"]
    elif kind == "points":
        xy = list(reversed(next(c for c in children if isinstance(c, folium.Marker)).location))
    else:
        heat = next(c for c in children if isinstance(c, folium.plugins.HeatMap))
        xy = [heat.data[0][1], heat.data[0][0]]
    bounds = next(c.bounds for c in children if isinstance(c, folium.map.FitBounds))
    assert len(render_map_html(m)) > 1000  # Real renderer, not a fabricated HTML object.
    return abs(xy[0] - 9) < 1e-8 and abs(xy[1] - 51) < 1e-8, {
        "rendered_xy": xy, "fit_bounds": bounds, "expected_lonlat": [9, 51],
        "unprepared_direct_call": direct,
        "browser_visual_validation": "Not Run"}


def tool_path(operation, use_wgs=False, naive=False):
    session = SessionData()
    points = frame([Point(500000, 5650000), Point(500100, 5650000)], 25832)
    polygons = frame([SQUARE], 25832)
    inside = frame([Point(500500, 5650500)], 25832)
    if use_wgs:
        points, polygons, inside = [g.to_crs(4326) for g in (points, polygons, inside)]
    if naive:
        points = frame([Point(9, 51), Point(9.001, 51)], None)
    key = session.add_upload("points", points)
    polykey = session.add_upload("polygons", polygons)
    inkey = session.add_upload("inside", inside)
    agent = create_agent_session(session, model_factory=lambda: (_ for _ in ()).throw(
        AssertionError("LLM must not initialize")))
    if operation == "buffer":
        agent.calculate_buffer_analysis.invoke({"layer_name": key, "distance_meters": 500})
    elif operation == "distance":
        agent.calculate_feature_distances.invoke({"layer_name": key})
    else:
        arguments = {"polygon_layer_name": polykey}
        if operation == "density":
            arguments["point_layer_name"] = inkey
        agent.calculate_area_and_density.invoke(arguments)
    result = session.results[-1]
    observed = {"tool_success": result.success, "message": result.message,
                "metrics": result.metrics, "new_dataset_count": len(result.dataset_ids),
                "source_dataset_crs": session.get_dataset(key).crs}
    if naive:
        # Unknown input has NO independent CRS oracle. Only rejection is checked.
        return not result.success, observed
    if not result.success:
        return False, observed
    if operation == "distance":
        return result.metrics["min_distance_m"] == 100, observed
    output = session.get_dataset(result.dataset_ids[0])
    observed["derived_crs"] = output.crs
    observed["parent_count"] = len(output.parent_ids)
    if operation == "buffer":
        observed["empty_geometry"] = bool(output.data.geometry.is_empty.any())
        bounds = output.data.total_bounds.tolist()
        observed["bounds"] = bounds
        # Independent reference: a nonempty local buffer must still be near Germany.
        return bool(not observed["empty_geometry"] and all(math.isfinite(v) for v in bounds)
                    and 8 < bounds[0] < bounds[2] < 10 and 50 < bounds[1] < bounds[3] < 52), observed
    observed["area_km2"] = output.data.area_km2.tolist()
    met = observed["area_km2"] == [1]
    if operation == "density":
        observed["count"] = output.data.objekt_anzahl.tolist()
        observed["density_per_km2"] = output.data.dichte_pro_km2.tolist()
        met &= observed["count"] == [1] and observed["density_per_km2"] == [1]
    return met, observed


def buffer_ground_reference():
    output = buffer_geometries(frame([Point(9, 51)], 4326), 500)
    radii = [Geod(ellps="WGS84").inv(9, 51, x, y)[2]
             for x, y in output.geometry.iloc[0].exterior.coords]
    return all(abs(radius - 500) <= 5 for radius in radii), {
        "requested_m": 500, "geodesic_radius_min_m": min(radii),
        "geodesic_radius_max_m": max(radii), "source_crs_correct": True}


def ambiguous():
    # Same numeric tuple admits multiple coordinate systems. No auto-detection oracle.
    p = Point(9, 51)
    interpreted = TO_WGS.transform(p.x, p.y)
    return None, {"same_tuple": [9, 51], "if_4326_lonlat": [9, 51],
                  "if_25832_lonlat": interpreted,
                  "conclusion": "No reliable true CRS without provenance; ranges are only sanity checks"}


def conflicting_or_absent_geojson(declared):
    source = fc(Point(X, Y), crs="EPSG:4326" if declared else None)
    converted = attempt(lambda: list(extract_features_to_gdf(source).geometry.iloc[0].coords)[0])
    # We know the fixture's provenance, but a production validator can only say
    # the coordinates violate WGS84 latitude bounds; it cannot infer EPSG:25832.
    return not converted["accepted"], {**converted, "latitude_exceeds_90": True,
                                      "true_crs_inferable_from_input": False}


def raw_dataset():
    source = fc(Point(X, Y), crs="EPSG:25832")
    session = SessionData()
    key = session.add_upload("declared", source)
    dataset = session.get_dataset(key)
    resolved = session.resolve_in([], key)
    return dataset.crs == "EPSG:25832", {
        "dataset_crs": dataset.crs, "embedded_crs": dataset.data["crs"],
        "resolved_explicit_crs": resolved.get("crs")}


def features_transport():
    session = SessionData()
    source = frame([Point(X, Y)], 25832)
    key = session.add_dataset(Dataset("features", source, crs="EPSG:25832",
                                      metadata={"format": "features"}))
    resolved = session.resolve_in([], key)
    payload = resolved["data"]
    result = extract_features_to_gdf(payload, source_crs=resolved["crs"])
    return result.crs.to_epsg() == 25832, {
        "dataset_crs": session.get_dataset(key).crs, "payload_type": type(payload).__name__,
        "payload_can_carry_collection_crs": False, "decoded_crs": str(result.crs)}


def mixed_tool_count():
    session = SessionData()
    polys = frame([SQUARE], 25832).to_crs(4326)
    points = frame([Point(500500, 5650500)], 25832)
    polykey = session.add_upload("wgs_polygons", polys)
    pointkey = session.add_upload("utm_points", points)
    agent = create_agent_session(session, model_factory=no_network)
    agent.calculate_area_and_density.invoke({"polygon_layer_name": polykey, "point_layer_name": pointkey})
    result = session.results[-1]
    if not result.success:
        return False, {"success": False, "message": result.message}
    derived = session.get_dataset(result.dataset_ids[0])
    count = derived.data.objekt_anzahl.tolist()
    return count == [1], {"success": result.success, "area_km2": derived.data.area_km2.tolist(),
                          "count": count, "expected_count": [1],
                          "density_per_km2": derived.data.dichte_pro_km2.tolist()}


def ordinary_json():
    data = [{"lat": 51, "lon": 9, "properties": {"value": 1}}]
    parsed = parse_uploaded_file(stream("points.json", data))
    source = deepcopy(parsed["data"])
    output = extract_features_to_gdf(source)
    assert source == parsed["data"]
    return output.crs.to_epsg() == 4326 and output.geometry.iloc[0].equals(Point(9, 51)), {
        "parser_type": parsed["type"], "crs": str(output.crs), "source_unchanged": True}


def safe_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)  # Diagnostic notation only, not a proposed business serializer.
    if isinstance(value, dict):
        return {key: safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    return value


def main():
    probe("D01", "RFC7946 WGS84 point remains (9,51)", parsed_wgs)
    probe("D02", "Declared 25832 point transforms to (9,51)", declared_projected, "KD-CRS")
    for kind in ("null", "invalid", "nested"):
        probe(f"D03-{kind}", "Explicit unknown/unsupported CRS must not silently become 4326",
              lambda kind=kind: uncertain_declaration(kind))
    for kind in ("latlon", "xy", "invalid"):
        probe(f"D04-{kind}", "Current CSV lat/lon contract; reject unsupported xy / invalid latitude",
              lambda kind=kind: csv_coordinates(kind), "KD-COORDINATES" if kind == "invalid" else None)
    for crs in (4326, 25832, None):
        probe(f"D05-{crs}", "Converter preserves native source CRS, rejects unknown and never mutates",
              lambda crs=crs: native_conversion(crs))
    probe("D06", "Direct projected core: 100m, 1km2, one point, density 1; inputs unchanged", direct_core)
    probe("D07", "All five direct core paths reject missing source CRS", naive_core)
    for crs in (25832, None):
        probe(f"D08-{crs}", "Session transport must preserve known CRS or unknown status",
              lambda crs=crs: session_view(crs))
    probe("D09", "Dataset CRS agrees with native geometry CRS", contract_mismatch)
    probe("D10", "Shown-only legacy import CRS agrees with native geometry CRS",
          lambda: contract_mismatch(True))
    for kind in ("geojson", "points", "heatmap", "choropleth"):
        probe(f"D11-{kind}", "Projected dictionary geometry is converted before longitude/latitude use",
              lambda kind=kind: map_projection(kind))
    probe("D12", "Folium GeoJson native GeoDataFrame branch actually reprojects", lambda: map_projection("geojson", True))
    for operation in ("buffer", "distance", "area", "density"):
        for wgs in (False, True):
            probe(f"D13-{operation}-{'4326' if wgs else '25832'}",
                  "Session-to-tool result meets independent analytic/local reference",
                  lambda operation=operation, wgs=wgs: tool_path(operation, wgs))
    probe("D14", "Session must not convert unknown CRS into implicit WGS84 tool input",
          lambda: tool_path("distance", naive=True))
    probe("D15", "A true 500m ground radius remains separate from CRS preservation",
          buffer_ground_reference, "KD-BUFFER")
    probe("D16", "Coordinate tuple alone does not identify the true CRS", ambiguous)
    for declared in (False, True):
        probe(f"D17-{'mislabeled' if declared else 'missing'}",
              "Out-of-range WGS84 latitude is rejected without guessing another CRS",
              lambda declared=declared: conflicting_or_absent_geojson(declared))
    probe("D18", "Raw JSON Dataset CRS must agree with its supported explicit declaration", raw_dataset)
    probe("D19", "Feature-list transport retains the dataset CRS out of band", features_transport)
    probe("D20", "Mixed-CRS tool inputs count the analytically interior point once", mixed_tool_count)
    probe("D21", "Ordinary JSON lat/lon input retains current WGS84 convention", ordinary_json)
    counts = dict(Counter(record["status"] for record in RESULTS))
    evidence = {"baseline_head": "fa441f0181bac0ba70c5b85fd3a458bb08e5d699",
                "phase": "4D working-tree verification",
                "environment": {"python": sys.version.split()[0], "geopandas": gpd.__version__,
                                "folium": folium.__version__, "pyproj": pyproj.__version__,
                                "shapely": shapely.__version__, "network": "blocked"},
                "summary": counts, "diagnostics": RESULTS}
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-name", default="phase4d_crs_diagnostics.json")
    args = parser.parse_args()
    if Path(args.evidence_name).name != args.evidence_name:
        parser.error("evidence-name must be a filename inside tests/logs")
    destination = ROOT / "tests" / "logs" / args.evidence_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(safe_json(evidence), ensure_ascii=False, indent=2,
                                      allow_nan=False), encoding="utf-8")
    print(json.dumps(counts))
    print("Evidence: tests/logs/" + args.evidence_name)
    return int(bool(counts.get("Diagnostic Error")))


if __name__ == "__main__":
    raise SystemExit(main())
