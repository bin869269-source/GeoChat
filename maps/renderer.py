"""Explicit layer-to-Folium rendering, independent of application state.

Preserves the existing map types, styles, layer order and last-layer bounds.
GeoJSON is copied at the Folium boundary to avoid its identifier injection
mutating caller data. Helpers intentionally add children to the supplied map.
"""
from copy import deepcopy
from typing import List, Optional

import folium
import branca.colormap as cm
from folium.plugins import HeatMap
from branca.element import Element
from pyproj import CRS


def _check_wire_crs(layer):
    """Require WGS84 wire data; callers prepare copies outside the renderer."""
    declarations = []
    if "crs" in layer:
        declarations.append(layer["crs"])
    data = layer.get("data")
    if isinstance(data, dict) and "crs" in data:
        header = data["crs"]
        if not isinstance(header, dict) or header.get("type") != "name":
            raise ValueError("Karten-CRS unbekannt; WGS84-Eingabe vorbereiten.")
        properties = header.get("properties")
        declarations.append(properties.get("name") if isinstance(properties, dict) else None)
    for value in declarations:
        try:
            valid = value is not None and CRS.from_user_input(value).equals(CRS.from_epsg(4326), ignore_axis_order=True)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("Karten benötigen eine tatsächlich nach EPSG:4326 transformierte Kopie.")

DEFAULT_MAP_STYLE = {
    "color": "blue",
    "weight": 2,
    "fillColor": "blue",
    "fillOpacity": 0.4
}


def create_layered_map(layers: List[dict], view_updates: Optional[dict] = None, show_basemap: bool = True) -> folium.Map:
    m = folium.Map(location=[51.1657, 10.4515], zoom_start=6, tiles=None)
    if show_basemap:
        folium.TileLayer(tiles="OpenStreetMap", name="OpenStreetMap", control=False).add_to(m)

    if not layers:
        return m

    latest_bounds = None
    for layer in layers:
        _check_wire_crs(layer)
        l_type = layer.get("type", "geojson")
        l_data = layer.get("data")
        l_name = layer.get("name", "Layer")
        kwargs = layer.get("folium_kwargs", DEFAULT_MAP_STYLE.copy())

        if l_type == "points":
            bounds = add_points_layer(m, l_data, l_name, folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds
        elif l_type == "heatmap":
            bounds = add_heatmap_layer(m, l_data, l_name, layer.get("data_field"), folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds
        elif l_type == "choropleth":
            bounds = add_choropleth_layer(m, l_data, l_name, layer.get("data_field"), folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds
        else:
            bounds = add_geojson_layer(m, l_data, l_name, folium_kwargs=kwargs)
            if bounds: latest_bounds = bounds

    if view_updates and "min_lat" in view_updates:
        m.fit_bounds([[view_updates["min_lat"], view_updates["min_lon"]],
                      [view_updates["max_lat"], view_updates["max_lon"]]])
    elif latest_bounds:
        m.fit_bounds(latest_bounds)
    else:
        m.fit_bounds([[50.5, 9.5], [52.5, 11.5]])

    folium.LayerControl().add_to(m)
    return m

def add_points_layer(m, points, layer_name, folium_kwargs=None):
    if folium_kwargs is None: folium_kwargs = {"color": "blue", "icon": "info-sign"}
    fg = folium.FeatureGroup(name=layer_name)
    bounds = []

    # 兼容 GeoJSON Feature 结构或者列表格式
    pts_to_iterate = points.get("features", []) if isinstance(points, dict) else points
    for pt in pts_to_iterate:
        if isinstance(pt, dict) and "geometry" in pt:
            coords = pt["geometry"].get("coordinates", [0, 0])
            lat, lon = coords[1], coords[0]
            name = pt.get("properties", {}).get("name", "Punkt")
        else:
            lat, lon = pt.get("lat", 0), pt.get("lon", 0)
            name = pt.get("name", "Punkt")
        bounds.append([lat, lon])
        folium.Marker(
            location=[lat, lon],
            popup=str(name),
            icon=folium.Icon(color=folium_kwargs.get("color", "blue"), icon=folium_kwargs.get("icon", "info-sign"))
        ).add_to(fg)
    fg.add_to(m)
    return bounds

def add_heatmap_layer(m, points, layer_name, data_field=None, folium_kwargs=None):
    if folium_kwargs is None: folium_kwargs = {"radius": 25, "blur": 15, "min_opacity": 0.3}
    heat_data = []
    pts_to_iterate = points.get("features", []) if isinstance(points, dict) else points
    for pt in pts_to_iterate:
        if isinstance(pt, dict) and "geometry" in pt:
            coords = pt["geometry"].get("coordinates", [0, 0])
            val = pt.get("properties", {}).get(data_field, 1.0) if data_field else 1.0
            heat_data.append([coords[1], coords[0], float(val)])
        else:
            val = pt.get("properties", {}).get(data_field, 1.0) if data_field else 1.0
            heat_data.append([pt["lat"], pt["lon"], float(val)])

    if heat_data:
        HeatMap(heat_data, name=layer_name, **folium_kwargs).add_to(m)
    return [[r[0], r[1]] for r in heat_data] if heat_data else []

def add_choropleth_layer(m, geojson_data, layer_name, data_field=None, folium_kwargs=None):
    if folium_kwargs is None:
        folium_kwargs = {"fillColor": "YlOrRd", "color": "black", "weight": 0.5, "fillOpacity": 0.7}

    COLORMAPS = {
        "YlOrRd": cm.linear.YlOrRd_09,
        "Blues": cm.linear.Blues_09,
        "Greens": cm.linear.Greens_09,
        "Purples": cm.linear.Purples_09
    }
    features = geojson_data.get("features", []) if isinstance(geojson_data, dict) else []
    values = []
    for feat in features:
        val = feat.get("properties", {}).get(data_field)
        if val is not None:
            try: values.append(float(val))
            except: pass

    min_val, max_val = (min(values), max(values)) if values else (0, 1)
    if min_val == max_val: max_val += 1

    scale_gen = COLORMAPS.get(folium_kwargs.get("fillColor", "YlOrRd"), cm.linear.YlOrRd_09)
    colormap = scale_gen.scale(min_val, max_val)
    colormap.caption = f"{layer_name} - {data_field}"

    def style_fn(feature):
        val = feature.get("properties", {}).get(data_field)
        try: fill_col = colormap(float(val))
        except: fill_col = "#cccccc"
        return {
            "fillColor": fill_col,
            "color": folium_kwargs.get("color", "black"),
            "weight": folium_kwargs.get("weight", 0.5),
            "fillOpacity": folium_kwargs.get("fillOpacity", 0.7)
        }

    fg = folium.FeatureGroup(name=layer_name)
    geo_l = folium.GeoJson(deepcopy(geojson_data), name=layer_name, style_function=style_fn).add_to(fg)
    fg.add_to(m)
    colormap.add_to(m)
    try: return geo_l.get_bounds()
    except: return []

def add_geojson_layer(m, geojson_data, layer_name, folium_kwargs=None):
    if folium_kwargs is None:
        folium_kwargs = {"fillColor": "blue", "color": "blue", "weight": 2, "fillOpacity": 0.4}
    fg = folium.FeatureGroup(name=layer_name)
    geo_l = folium.GeoJson(
        deepcopy(geojson_data),
        name=layer_name,
        style_function=lambda feat: {
            "fillColor": folium_kwargs.get("fillColor", "blue"),
            "color": folium_kwargs.get("color", "blue"),
            "weight": folium_kwargs.get("weight", 2),
            "fillOpacity": folium_kwargs.get("fillOpacity", 0.4)
        }
    ).add_to(fg)
    fg.add_to(m)
    try: return geo_l.get_bounds()
    except: return []

def Titel_hinzufügen(m: folium.Map, titel: str) -> None:
    title_html = f'''
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 9999;
                background-color: white; padding: 8px 15px; border-radius: 6px; font-size: 18px;
                font-weight: bold; box-shadow: 0 0 6px rgba(0,0,0,0.3);">
        {titel}
    </div>'''
    m.get_root().html.add_child(Element(title_html))


def render_map_html(m: folium.Map) -> bytes:
    """Render UTF-8 HTML bytes, equivalent to Folium save's default encoding.

    Does not write a shared file or download browser assets. As with save,
    rendering may populate Folium's generated map/figure children.
    """
    return m.get_root().render().encode("utf-8")
