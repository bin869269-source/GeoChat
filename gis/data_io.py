"""Deterministic legacy file parsing and GeoDataFrame conversion.

No model-generated metadata is handled here. Existing format/CRS assumptions
are deliberately preserved for Phase 3A; this is not input validation repair.
"""
import json
from typing import Any

import geopandas as gpd
import pandas as pd


def extract_features_to_gdf(data_input: Any) -> gpd.GeoDataFrame:
    """Return a new frame from point dictionaries or GeoJSON features.

    Legacy assumption: coordinates are longitude/latitude EPSG:4326. An incoming
    GeoJSON CRS declaration is not interpreted (known defect, unchanged).
    """
    feats = []
    if isinstance(data_input, list):
        for p in data_input:
            if isinstance(p, dict) and "lat" in p and "lon" in p:
                feats.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(p["lon"]), float(p["lat"])]},
                    "properties": p.get("properties", p)
                })
            elif isinstance(p, dict) and "geometry" in p:
                feats.append(p)
    elif isinstance(data_input, dict):
        if data_input.get("type") == "FeatureCollection":
            feats = data_input.get("features", [])
        elif data_input.get("type") == "Feature":
            feats = [data_input]
        elif "features" in data_input:
            feats = data_input["features"]

    if not feats:
        raise ValueError("Keine gültigen Geodaten extrahierbar.")
    return gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")


def clean_gdf_for_geojson(gdf: gpd.GeoDataFrame, *, inplace: bool = False) -> gpd.GeoDataFrame:
    """Convert scalar-like attribute values with .item(), retaining legacy rules.

    Return a copy by default. Explicit inplace=True preserves the old backend
    helper's mutation/identity contract. Nested attribute objects are not edited.
    This is scalar preparation, not a guarantee that all values serialize.
    """
    result = gdf if inplace else gdf.copy()
    for col in result.columns:
        if col != "geometry":
            result[col] = result[col].apply(lambda x: x.item() if hasattr(x, "item") else x)
    return result


def parse_uploaded_file(uploaded_file) -> dict:
    """Parse a readable file-like object with .name; no Streamlit type required.

    Consumes the stream from its current position. CSV lat/lon are assumed to be
    WGS84 degrees. Output shapes and errors match the existing parser.
    """
    filename = uploaded_file.name.lower()
    if filename.endswith(".geojson") or filename.endswith(".json"):
        data = json.load(uploaded_file)
        if isinstance(data, dict) and data.get("type") in ["FeatureCollection", "Feature"]:
            return {"type": "geojson", "data": data}
        return {"type": "points", "data": data}
    elif filename.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
        if "lat" not in df.columns or "lon" not in df.columns:
            raise ValueError("CSV muss die Spalten 'lat' und 'lon' enthalten.")
        points = []
        for i, row in df.iterrows():
            props = {col: row[col] for col in df.columns if col not in ["lat", "lon", "name"]}
            points.append({
                "name": row.get("name", f"Point {i+1}"),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "properties": props
            })
        return {"type": "points", "data": points}
    raise ValueError("Format nicht unterstützt.")
