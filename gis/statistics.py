"""Area, point count and density, retaining existing numeric semantics.

All inputs require a CRS. Functions return new frames/series and never submit
application state. Fixed projections, early rounding, invalid-input handling
and within boundary/overlap rules are deliberately unchanged in Phase 3A.
"""
import geopandas as gpd
import numpy as np
import pandas as pd


def calculate_area(polygons: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with area_km2, using EPSG:25832 and four decimal places.

    Geometry and source CRS are retained. Extremely small areas may round to
    zero; rejecting invalid geometries and improving precision are later work.
    """
    result = polygons.copy()
    projected = polygons.to_crs(epsg=25832)
    result["area_km2"] = (projected.geometry.area / 1_000_000).round(4)
    return result


def count_points_in_polygons(polygons: gpd.GeoDataFrame, points: gpd.GeoDataFrame) -> pd.Series:
    """Return integer counts indexed like polygons, without modifying inputs.

    Join uses EPSG:3857 and strict within: boundary points excluded; a point in
    overlapping polygons counts in each. Retain the legacy index_right logic.
    """
    projected_polygons = polygons.to_crs(epsg=3857)
    projected_points = points.to_crs(epsg=3857)
    joined = gpd.sjoin(projected_points, projected_polygons, predicate="within", how="inner")
    counts = joined.groupby("index_right").size()
    values = polygons.index.map(counts).fillna(0).astype(int)
    return pd.Series(values, index=polygons.index, name="objekt_anzahl")


def calculate_density(polygons_with_area: gpd.GeoDataFrame, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with objekt_anzahl and dichte_pro_km2 (points per km²).

    Requires area_km2, normally supplied by calculate_area. Intentionally uses
    this already-rounded area, rounds density to two places and fills undefined
    zero-area density with 0, preserving the known precision defect.
    """
    result = polygons_with_area.copy()
    result["objekt_anzahl"] = count_points_in_polygons(result, points)
    result["dichte_pro_km2"] = (
        result["objekt_anzahl"] / result["area_km2"].replace(0, np.nan)
    ).round(2).fillna(0)
    return result
