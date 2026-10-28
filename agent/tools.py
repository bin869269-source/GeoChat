"""Session-bound tool adapters; no current-user module global or context variable."""
import json
import urllib.parse
from functools import wraps
from inspect import signature
from typing import List, Optional

import requests
import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from langchain_core.tools import tool, StructuredTool

from core.session import AmbiguousReference, SessionData
from gis.data_io import extract_features_to_gdf
from gis import data_io as gis_data_io
from gis.geometry import buffer_geometries, pairwise_distances
from gis import statistics as gis_statistics

def make_unique_layer_name(base_name: str, existing_layers: List[dict]) -> str:
    existing_names = [l["name"] for l in existing_layers]
    if base_name not in existing_names:
        return base_name
    counter = 2
    new_name = f"{base_name} ({counter})"
    while new_name in existing_names:
        counter += 1
        new_name = f"{base_name} ({counter})"
    return new_name

def clean_gdf_for_geojson(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Compatibility entry point retaining the original in-place behavior."""
    return gis_data_io.clean_gdf_for_geojson(gdf, inplace=True)


def create_tools(session: SessionData):
    """Bind seven compatible schemas to one session, publishing only after success.

    The working view exists only during a locked tool call and belongs to this
    factory's session. It is never shared with another session or module global.
    """
    working = None
    used_dataset_ids = set()
    tool_metrics = {}

    def get_agent_layers():
        if working is None:
            raise RuntimeError("Tool execution requires its bound session")
        return working

    def resolve(pool, reference, *, require_layer=False):
        match = session.resolve_in(pool, reference, require_layer=require_layer)
        if match:
            used_dataset_ids.add(match["dataset_id"])
        return match

    def input_frame(target):
        return extract_features_to_gdf(target["data"], source_crs=target["crs"])

    @tool
    def fetch_city_boundary_osm(city_name: str) -> str:
        """
        Ruft die offiziellen realen administrativen Grenzpolygone einer Stadt über OpenStreetMap (Nominatim) ab
        und fügt sie als neuen Layer hinzu. Geeignet bei Befehlen wie 'Zeige Dresden', 'Lade Umrisse von Berlin'.
        """
        raw_layers, show_layers = get_agent_layers()
        encoded_city = urllib.parse.quote(city_name.strip())
        osm_url = f"https://nominatim.openstreetmap.org/search?q={encoded_city}&format=geojson&polygon_geojson=1&limit=1"
        headers = {"User-Agent": "GeoGPT_Autonomous_GIS_Agent"}

        try:
            resp = requests.get(osm_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return f"Fehler bei OSM-Abfrage: HTTP {resp.status_code}"

            osm_data = resp.json()
            features = osm_data.get("features", [])
            if not features:
                return f"Keine Geometrieumrisse für '{city_name}' gefunden."

            target_feat = next((f for f in features if f.get("geometry", {}).get("type") in ["Polygon", "MultiPolygon"]), features[0])
            geojson_data = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"name": city_name, "source": "OpenStreetMap"},
                    "geometry": target_feat["geometry"]
                }]
            }

            layer_name = make_unique_layer_name(f"Stadtgrenze_{city_name}", show_layers)
            new_layer = {
                "name": layer_name,
                "type": "geojson",
                "data": geojson_data,
                "folium_kwargs": {"color": "red", "weight": 2, "fillColor": "blue", "fillOpacity": 0.2}
            }
            show_layers.append(new_layer)
            return f"Erfolg: Die Stadtgrenze von '{city_name}' wurde geladen und als Layer '{layer_name}' zur Karte hinzugefügt."
        except Exception as e:
            return f"Fehler beim Laden der Stadtgrenze: {str(e)}"

    @tool
    def visualize_layer_on_map(layer_name: str, map_type: str = "geojson", data_field: Optional[str] = None,
                               color: str = "blue", weight: int = 2, fill_opacity: float = 0.4) -> str:
        """
        Bringt einen bereits hochgeladenen Rohdaten-Layer auf die Karte oder ändert dessen Darstellungsart.
        map_type unterstützt: 'points', 'geojson', 'heatmap', 'choropleth'.
        data_field: Vorhandenes numerisches Attribut für Choropleth/Heatmap. Für Choropleth niemals ein Feld aus
        dem Layernamen ableiten. Ohne Angabe wird nur bei genau einem numerischen Attribut automatisch gewählt;
        bei mehreren muss der Nutzer wählen oder die Auswahl ausdrücklich dem Agenten überlassen.
        """
        raw_layers, show_layers = get_agent_layers()
        all_available = show_layers + raw_layers
        target = resolve(all_available, layer_name)
        if not target:
            available_names = [l["name"] for l in all_available]
            return f"Layer '{layer_name}' nicht gefunden. Verfügbar sind: {available_names}"

        frame = input_frame(target)  # Unknown/conflicting CRS must not become a successful layer.

        if map_type == "choropleth":
            property_fields = [name for name in frame.columns if name != frame.geometry.name]
            numeric_values = {}
            for field in property_fields:
                try:
                    converted = pd.to_numeric(frame[field], errors="coerce")
                    values = converted.to_numpy(dtype=float, na_value=np.nan)
                    values = values[np.isfinite(values)]
                except (TypeError, ValueError):
                    continue
                if len(values):
                    numeric_values[field] = values

            available = list(numeric_values)
            requested_field = data_field
            if requested_field:
                if requested_field in property_fields:
                    selected_field = requested_field
                else:
                    matches = [name for name in property_fields
                               if name.casefold() == requested_field.casefold()]
                    if len(matches) == 1:
                        selected_field = matches[0]
                    elif len(matches) > 1:
                        return (f"Attribut '{requested_field}' ist wegen unterschiedlicher Schreibweisen mehrdeutig. "
                                f"Bitte eines dieser Attribute exakt angeben: {', '.join(matches)}.")
                    else:
                        choices = ", ".join(available) if available else "Keine"
                        return (f"Attribut '{requested_field}' nicht gefunden. Verfügbare numerische Attribute: "
                                f"{choices}.")
                if selected_field not in numeric_values:
                    choices = ", ".join(available) if available else "Keine"
                    return (f"Attribut '{selected_field}' ist nicht numerisch oder enthält keine gültigen Zahlen. "
                            f"Verfügbare numerische Attribute: {choices}.")
            elif len(available) == 1:
                selected_field = available[0]
            elif len(available) > 1:
                return ("Für die Choropleth-Karte gibt es mehrere numerische Attribute. "
                        f"Bitte eines auswählen: {', '.join(available)}.")
            else:
                return "Für diese Daten wurden keine numerischen Attribute gefunden; eine Choropleth-Karte ist nicht möglich."

            values = numeric_values[selected_field]
            if len(values) > 1 and np.unique(values).size == 1:
                return (f"Attribut '{selected_field}' enthält überall denselben Wert; "
                        "damit lässt sich keine sinnvolle Farbskala bilden. Bitte ein anderes Attribut wählen.")
            missing_count = len(frame) - len(values)
            data_field = selected_field

        folium_kwargs = {
            "color": color,
            "weight": weight,
            "fillColor": color,
            "fillOpacity": fill_opacity
        }
        if map_type == "heatmap":
            folium_kwargs = {"radius": 25, "blur": 15, "min_opacity": 0.3}

        new_map_layer_name = make_unique_layer_name(f"{target['name']}_{map_type}", show_layers)
        show_layers.append({
            "name": new_map_layer_name,
            "type": map_type,
            "dataset_id": target["dataset_id"],
            "data": target["data"],
            "data_field": data_field,
            "folium_kwargs": folium_kwargs,
            "style": folium_kwargs
        })
        if map_type == "choropleth":
            tool_metrics["choropleth_field"] = data_field
            if missing_count:
                return (f"Erfolg: Layer '{target['name']}' wurde als Choropleth unter dem Namen "
                        f"'{new_map_layer_name}' mit Attribut '{data_field}' erstellt. "
                        f"{missing_count} von {len(frame)} Objekten haben keinen gültigen Wert und werden als fehlend markiert.")
            return (f"Erfolg: Layer '{target['name']}' wurde als Choropleth unter dem Namen "
                    f"'{new_map_layer_name}' mit Attribut '{data_field}' erstellt.")
        return f"Erfolg: Layer '{target['name']}' wurde als '{map_type}' unter dem Namen '{new_map_layer_name}' zur Karte hinzugefügt."

    @tool
    def calculate_buffer_analysis(layer_name: str, distance_meters: float = 500.0) -> str:
        """
        Berechnet eine Pufferzone (Buffer) um Geometrien eines Layers mit angegebener Distanz in Metern.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers if show_layers else raw_layers
        target = resolve(pool, layer_name)
        if not target:
            return f"Layer '{layer_name}' nicht gefunden."

        try:
            gdf = input_frame(target)
            res_gdf = buffer_geometries(gdf, distance_meters)
            res_gdf = clean_gdf_for_geojson(res_gdf)
            tool_metrics["feature_count"] = len(res_gdf)

            new_layer_name = make_unique_layer_name(f"Puffer_{int(distance_meters)}m_{target['name']}", show_layers)
            show_layers.append({
                "name": new_layer_name,
                "type": "polygon",
                "data": res_gdf,
                "folium_kwargs": {"color": "red", "weight": 2, "fillColor": "orange", "fillOpacity": 0.35}
            })
            return f"Erfolg: Ein {distance_meters}m Puffer für '{target['name']}' wurde erzeugt (neuer Layer: '{new_layer_name}')."
        except Exception as e:
            return f"Fehler bei Pufferberechnung: {str(e)}"

    @tool
    def calculate_area_and_density(polygon_layer_name: str, point_layer_name: Optional[str] = None) -> str:
        """
        Berechnet Flächen (km² / m²) für Polygone. Falls ein zweiter Punkt-Layer angegeben ist,
        wird zusätzlich ein räumlicher Overlay-Join durchgeführt und die Punktdichte pro km² berechnet.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        poly_layer = resolve(pool, polygon_layer_name)
        if not poly_layer:
            return f"Polygon-Layer '{polygon_layer_name}' nicht gefunden."

        try:
            gdf_poly = input_frame(poly_layer)
            gdf_poly = gis_statistics.calculate_area(gdf_poly)

            target_field = "area_km2"
            layer_suffix = "Flaeche"

            if point_layer_name:
                pt_layer = resolve(pool, point_layer_name)
                if pt_layer:
                    gdf_pt = input_frame(pt_layer)
                    gdf_poly = gis_statistics.calculate_density(gdf_poly, gdf_pt)
                    target_field = "dichte_pro_km2"
                    layer_suffix = "Dichte"

            res_gdf = clean_gdf_for_geojson(gdf_poly)
            res_json = json.loads(res_gdf.to_json())
            tool_metrics.update(feature_count=len(res_gdf), computed_field=target_field)
            new_layer_name = make_unique_layer_name(f"{layer_suffix}_{poly_layer['name']}", show_layers)

            show_layers.append({
                "name": new_layer_name,
                "type": "choropleth",
                "data": res_gdf,
                "data_field": target_field,
                "folium_kwargs": {"fillColor": "YlOrRd", "color": "black", "weight": 1, "fillOpacity": 0.7}
            })
            return f"Erfolg: Analyse durchgeführt. Attribut '{target_field}' berechnet und als Choropleth-Layer '{new_layer_name}' visualisiert."
        except Exception as e:
            return f"Fehler bei Flächen-/Dichteberechnung: {str(e)}"

    @tool
    def run_machine_learning_spatial_prediction(layer_name: str, target_field: Optional[str] = None) -> str:
        """
        Trainiert einen Random-Forest-Regressor auf den Raumkoordinaten (lat/lon) des Ziel-Layers,
        interpoliert/prognostiziert Muster und fügt Vorhersagen sowie Residuen als Layer hinzu.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        target = resolve(pool, layer_name)
        if not target:
            return f"Layer '{layer_name}' nicht gefunden."

        try:
            gdf = gis_data_io.transform_geodata(input_frame(target), 4326)
            gdf["lon"] = gdf.geometry.x
            gdf["lat"] = gdf.geometry.y

            if not target_field:
                num_cols = [c for c in gdf.select_dtypes(include=[np.number]).columns if c not in ["lon", "lat"]]
                if not num_cols:
                    return "Keine numerische Variable für ML-Modellierung vorhanden."
                target_field = num_cols[0]

            clean_df = gdf.dropna(subset=[target_field, "lon", "lat"])
            X = clean_df[["lon", "lat"]].values
            y = clean_df[target_field].values

            rf = RandomForestRegressor(n_estimators=100, random_state=42)
            rf.fit(X, y)

            clean_df["ml_predicted"] = np.round(rf.predict(X), 2)
            clean_df["ml_residual"] = np.round(clean_df[target_field] - clean_df["ml_predicted"], 2)
            clean_gdf = clean_gdf_for_geojson(clean_df)
            tool_metrics.update(feature_count=len(clean_gdf), target_field=target_field)

            new_layer_name = make_unique_layer_name(f"ML_Vorhersage_{target['name']}", show_layers)
            show_layers.append({
                "name": new_layer_name,
                "type": "points",
                "data": clean_gdf,
                "_format": "features",
                "folium_kwargs": {"color": "purple", "icon": "eye-open"}
            })
            return f"Erfolg: Random-Forest-Modell für '{target_field}' trainiert. Vorhersagen als Layer '{new_layer_name}' eingefügt."
        except Exception as e:
            return f"Fehler bei ML-Prognose: {str(e)}"

    @tool
    def update_map_style(layer_name: str, color: Optional[str] = None, weight: Optional[int] = None,
                         fill_opacity: Optional[float] = None) -> str:
        """
        Ändert Farb- und Stilparameter (Farbe, Randstärke, Transparenz) eines bestehenden Kartenlayers.
        """
        _, show_layers = get_agent_layers()
        target = resolve(show_layers, layer_name, require_layer=True)
        if not target:
            return f"Sichtbarer Layer '{layer_name}' nicht gefunden."

        kwargs = target.get("folium_kwargs", {})
        if color:
            kwargs["color"] = color
            kwargs["fillColor"] = color
        if weight:
            kwargs["weight"] = int(weight)
        if fill_opacity is not None:
            kwargs["fillOpacity"] = float(fill_opacity)

        target["folium_kwargs"] = kwargs
        return f"Erfolg: Stil für Layer '{layer_name}' aktualisiert auf {kwargs}."

    @tool
    def calculate_feature_distances(layer_name: str, max_pairs: int = 10) -> str:
        """
        Berechnet die paarweisen realen Abstände (in Metern) zwischen allen Objekten/Gebäuden
        innerhalb eines Punkt- oder Polygon-Layers.
        Geeignet bei Befehlen wie 'Berechne den Abstand zwischen den Gebäuden',
        'Wie weit sind die Gebäude voneinander entfernt?'.
        """
        raw_layers, show_layers = get_agent_layers()
        pool = show_layers + raw_layers
        target = resolve(pool, layer_name)
        if not target:
            available = [l["name"] for l in pool]
            return f"Layer '{layer_name}' wurde nicht gefunden. Verfügbar sind: {available}"

        try:
            # 1. 转换为 GeoDataFrame
            gdf = input_frame(target)
            if len(gdf) < 2:
                return f"Der Layer '{layer_name}' enthält weniger als 2 Objekte. Abstandsberechnung nicht möglich."

            # 尝试提取能代表建筑名称的字段
            name_cols = [c for c in ["bezeichnung", "name", "einrichtung", "strasse"] if c in gdf.columns]
            label_col = name_cols[0] if name_cols else None

            results = []
            for pair in pairwise_distances(gdf):
                idx1, idx2 = pair["from_index"], pair["to_index"]
                name1 = str(gdf.iloc[idx1][label_col]) if label_col else f"Objekt {idx1 + 1}"
                name2 = str(gdf.iloc[idx2][label_col]) if label_col else f"Objekt {idx2 + 1}"

                results.append({
                    "from": name1,
                    "to": name2,
                    "distance_m": pair["distance_m"]
                })

            # 按距离从近到远排序
            results.sort(key=lambda x: x["distance_m"])

            # 3. 格式化输出 Markdown 表格
            summary = f"### 📏 Distanzanalyse für '{target['name']}'\n\n"
            summary += f"Es wurden die Abstände zwischen insgesamt **{len(gdf)} Gebäuden/Objekten** berechnet.\n\n"
            summary += "| Von | Nach | Abstand (Meter) | Abstand (km) |\n"
            summary += "| :--- | :--- | :---: | :---: |\n"

            for row in results[:max_pairs]:
                dist_km = round(row["distance_m"] / 1000, 2)
                summary += f"| {row['from']} | {row['to']} | **{row['distance_m']} m** | {dist_km} km |\n"

            if len(results) > max_pairs:
                summary += f"\n*(Tabelle auf die {max_pairs} kürzesten Distanzen begrenzt)*\n"

            min_pair = results[0]
            tool_metrics.update(feature_count=len(gdf), pair_count=len(results),
                                min_distance_m=min_pair["distance_m"])
            summary += f"\n- **Nächste Nachbarn:** `{min_pair['from']}` und `{min_pair['to']}` liegen mit nur **{min_pair['distance_m']} m** am dichtesten beieinander."

            return summary

        except Exception as e:
            return f"Fehler bei der Distanzberechnung: {str(e)}"

    TOOLS = [
        fetch_city_boundary_osm,
        visualize_layer_on_map,
        calculate_buffer_analysis,
        calculate_area_and_density,
        run_machine_learning_spatial_prediction,
        update_map_style,
        calculate_feature_distances
    ]

    def bind_atomic(legacy):
        @wraps(legacy.func)
        def execute(*args, **kwargs):
            nonlocal working
            with session.lock:
                working = session.legacy_layers()
                used_dataset_ids.clear()
                tool_metrics.clear()
                arguments = signature(legacy.func).bind(*args, **kwargs)
                arguments.apply_defaults()
                try:
                    message = legacy.func(*args, **kwargs)
                    success = message.startswith("Erfolg:") or (
                        legacy.name == "calculate_feature_distances" and message.startswith("###"))
                    if not success:
                        category = "ambiguous_reference" if "mehrdeutig" in message else (
                            "not_found" if "nicht gefunden" in message else "tool_error")
                        return session.record_failure(message, category).message
                    metrics = dict(tool_metrics)
                    for key in ("distance_meters", "max_pairs"):
                        if key in arguments.arguments:
                            metrics[key] = arguments.arguments[key]
                    result = session.commit_views(*working, message=message, tool_name=legacy.name,
                                                  parent_ids=tuple(sorted(used_dataset_ids)), metrics=metrics)
                    return result.message
                except AmbiguousReference as exc:
                    return session.record_failure(str(exc), "ambiguous_reference").message
                except Exception as exc:
                    return session.record_failure(f"Fehler bei {legacy.name}: {exc}", "commit_or_tool_error").message
                finally:
                    working = None
                    used_dataset_ids.clear()
                    tool_metrics.clear()
        return StructuredTool.from_function(execute, name=legacy.name, description=legacy.description,
                                            args_schema=legacy.args_schema)

    return [bind_atomic(registered) for registered in TOOLS]
