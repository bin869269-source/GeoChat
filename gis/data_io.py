"""Deterministic parsing, explicit source CRS and detached rendering inputs."""
from copy import deepcopy
import json
from typing import Any

import geopandas as gpd
import pandas as pd
import numpy as np
from pyproj import CRS
from shapely import get_coordinates

_UNSET = object()


def _crs(value):
    if value is None:
        raise ValueError("CRS unbekannt; ein verlässlicher Quell-CRS ist erforderlich.")
    try:
        result = CRS.from_user_input(value)
    except Exception as exc:
        raise ValueError("CRS nicht erkannt oder nicht unterstützt.") from exc
    if not (result.is_geographic or result.is_projected):
        raise ValueError("CRS nicht unterstützt: geografischer oder projizierter CRS erforderlich.")
    return result


def resolve_source_crs(data, *, source_crs=_UNSET):
    """Resolve native/declared CRS; absent GeoJSON and lat/lon use their protocol.

    Explicit None means unknown, unlike an omitted argument. Declarations on
    features/geometries must agree; properties named 'crs' are ordinary data.
    Legacy linked CRS never triggers a network request.
    """
    candidates = []
    if source_crs is not _UNSET:
        candidates.append(_crs(source_crs))
    if isinstance(data, gpd.GeoDataFrame):
        # An explicit reliable context may reference a naive frame, but must not
        # relabel a frame that already has a different CRS.
        if data.crs is not None:
            candidates.append(_crs(data.crs))
        elif not candidates:
            raise ValueError("CRS unbekannt für GeoDataFrame.")
    else:
        supported = ((isinstance(data, dict) and (data.get("type") in ("Feature", "FeatureCollection") or "features" in data))
                     or (isinstance(data, list) and all(isinstance(item, dict) and (
                         "geometry" in item or ("lat" in item and "lon" in item)) for item in data)))
        if not supported:
            raise ValueError("CRS unbekannt für dieses Datenformat; keine Koordinatenannahme möglich.")
        if not candidates and isinstance(data, (dict, list)) and not (
                isinstance(data, dict) and "crs" in data):
            candidates.append(_crs(4326))
        def declarations(value):
            if isinstance(value, list):
                for item in value:
                    declarations(item)
            elif isinstance(value, dict):
                if "lat" in value and "lon" in value and "geometry" not in value:
                    candidates.append(_crs(4326))
                    return  # Plain lat/lon records use their WGS84 protocol.
                if "crs" in value:
                    declaration = value["crs"]
                    if not isinstance(declaration, dict) or declaration.get("type") != "name":
                        raise ValueError("CRS unbekannt oder nicht unterstützt (named CRS erforderlich).")
                    properties = declaration.get("properties")
                    name = properties.get("name") if isinstance(properties, dict) else None
                    if not isinstance(name, str) or not name.upper().startswith(("EPSG:", "URN:OGC:DEF:CRS:", "OGC:")):
                        raise ValueError("CRS nicht unterstützt.")
                    candidates.append(_crs(name))
                for key in ("features", "geometry", "geometries"):
                    if key in value:
                        declarations(value[key])
        declarations(data)
        if not candidates:
            # Only supported GeoJSON/point protocols have a documented default.
            if not isinstance(data, (dict, list)):
                raise ValueError("CRS unbekannt für diesen Eingabetyp.")
            candidates.append(_crs(4326))
    reference = candidates[0]
    if any(not reference.equals(item, ignore_axis_order=True) for item in candidates[1:]):
        raise ValueError("CRS-Konflikt zwischen Daten und Quell-CRS.")
    return reference.to_string()


def validate_crs_coordinates(frame):
    """Reject non-finite coordinates and impossible geographic latitude.

    Does not reject empty/invalid topology: those existing defects are separate.
    This guard is applied at CRS interpretation/conversion, not CSV parsing.
    """
    reference = _crs(frame.crs)
    coords = get_coordinates(frame.geometry.values)
    if not np.isfinite(coords).all():
        raise ValueError("CRS-Transformation ergab nicht-endliche Koordinaten.")
    if reference.is_geographic and len(coords):
        # Non-degree geographic CRSs must also be checked in WGS84 degrees.
        geographic = get_coordinates(frame.to_crs(4326).geometry.values)
        if not np.isfinite(geographic).all() or (np.abs(geographic[:, 1]) > 90).any():
            raise ValueError("CRS/Koordinaten-Konflikt: Breitengrad außerhalb [-90, 90].")
    if reference.is_projected and len(coords):
        # A no-op projection to the analysis CRS must not conceal coordinates
        # that cannot be mapped to a finite geographic location at all.
        geographic = get_coordinates(frame.to_crs(4326).geometry.values)
        if not np.isfinite(geographic).all():
            raise ValueError("CRS-Transformation ergab nicht-endliche geografische Koordinaten.")


def transform_geodata(frame, target_crs):
    """Actually reproject a detached frame, validating the coordinate result."""
    if frame.crs is None:
        # Keep GeoPandas' established explicit-CRS error for direct Core callers.
        return frame.to_crs(target_crs)
    validate_crs_coordinates(frame)
    result = frame.to_crs(target_crs)
    validate_crs_coordinates(result)
    return result


def extract_features_to_gdf(data_input: Any, *, source_crs=_UNSET) -> gpd.GeoDataFrame:
    """Preserve source coordinates/CRS and return an independently owned frame."""
    if isinstance(data_input, gpd.GeoDataFrame):
        crs = resolve_source_crs(data_input, source_crs=source_crs)
        result = deepcopy(data_input)
        for col in result.columns:
            if col != result.geometry.name and result[col].dtype == object:
                result[col] = result[col].map(deepcopy)
        if result.crs is None:
            result = result.set_crs(crs)  # Explicit context, not a conversion.
        validate_crs_coordinates(result)
        return result
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
    crs = resolve_source_crs(data_input, source_crs=source_crs)
    result = gpd.GeoDataFrame.from_features(deepcopy(feats), crs=crs)
    validate_crs_coordinates(result)
    return result


def prepare_map_layers(layers):
    """Create WGS84 wire copies; retain IDs, attributes, style and input shape.

    Source Dataset coordinates are never changed. Keeping the existing wire
    shapes also leaves unrelated single-Feature rendering behavior unchanged.
    """
    prepared = []
    for layer in layers:
        item = deepcopy(layer)
        data = layer["data"]
        context = {"source_crs": layer["crs"]} if "crs" in layer else {}
        frame = extract_features_to_gdf(data, **context)
        wgs = transform_geodata(frame, 4326)
        if CRS.from_user_input(frame.crs).equals(CRS.from_epsg(4326), ignore_axis_order=True) and not isinstance(data, gpd.GeoDataFrame):
            wire = deepcopy(data)
        else:
            converted = json.loads(wgs.to_json())
            if isinstance(data, list):
                wire = deepcopy(data)
                for original, feature in zip(wire, converted["features"]):
                    if "geometry" in original:
                        original["geometry"] = feature["geometry"]
                        original.pop("bbox", None)
                    else:
                        original["lon"], original["lat"] = feature["geometry"]["coordinates"][:2]
            elif isinstance(data, dict):
                wire = deepcopy(data)
                if data.get("type") == "Feature":
                    wire["geometry"] = converted["features"][0]["geometry"]
                else:
                    for original, feature in zip(wire["features"], converted["features"]):
                        original["geometry"] = feature["geometry"]
                        original.pop("bbox", None)
            else:
                wire = converted
        # All geometry-level declarations must match the converted coordinates.
        def clean(value):
            if isinstance(value, list):
                for entry in value:
                    clean(entry)
            elif isinstance(value, dict):
                if "lat" in value and "lon" in value and "geometry" not in value:
                    return  # Preserve ordinary point-record attributes.
                value.pop("crs", None)
                value.pop("bbox", None)
                for key in ("features", "geometry", "geometries"):
                    if key in value:
                        clean(value[key])
        clean(wire)
        item.update(data=wire, crs="EPSG:4326")
        prepared.append(item)
    return prepared


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
