"""Geometry computations on explicitly georeferenced frames, without sessions.

Projection choices and rounding intentionally retain the Phase 2 baseline.
They must not be interpreted as a new global accuracy guarantee.
"""
from itertools import combinations

import geopandas as gpd

from .data_io import transform_geodata


def buffer_geometries(gdf: gpd.GeoDataFrame, distance_meters: float = 500.0) -> gpd.GeoDataFrame:
    """Return buffered features in EPSG:4326 without modifying the input.

    gdf must carry its source CRS. distance_meters is applied in EPSG:3857
    projected metres, NOT geodesic ground metres (known legacy distortion).
    """
    projected = transform_geodata(gdf, 3857)
    projected["geometry"] = projected.buffer(distance_meters)
    return transform_geodata(projected, 4326)


def pairwise_distances(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Return all feature-pair minimum distances in EPSG:25832 metres.

    Input requires a CRS. from_index/to_index are zero-based row POSITIONS,
    not index labels or application IDs. Retain rounding to 0.1m and stable
    sorting after rounding. No max_pairs truncation or UI formatting occurs.
    Input is not modified. Time and result storage remain quadratic.
    """
    projected = transform_geodata(gdf, 25832)
    results = []
    for idx1, idx2 in combinations(range(len(projected)), 2):
        distance = projected.iloc[idx1].geometry.distance(projected.iloc[idx2].geometry)
        results.append({
            "from_index": idx1,
            "to_index": idx2,
            "distance_m": round(distance, 1)
        })
    results.sort(key=lambda row: row["distance_m"])
    return results
