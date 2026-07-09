# ==========================================
# BLOCK 1: ENV CONFIGURATION & INITIALIZATION
# ==========================================
# %% BLOCK 1: ENV CONFIGURATION & INITIALIZATION
import os
import json
import re
from typing import TypedDict, Optional, List
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

import urllib.parse
import requests

import geopandas as gpd
from shapely.geometry import Point, shape, mapping

from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
import numpy as np

current_dir = Path(__file__).parent
load_dotenv(dotenv_path=current_dir / ".env")

api_key = os.getenv("DEEPSEEK_API_KEY")

try:
    import streamlit as st
    api_key = api_key or st.secrets.get("DEEPSEEK_API_KEY")
except Exception:
    pass

if not api_key:
    raise ValueError("DEEPSEEK_API_KEY wurde nicht gefunden!")

llm = ChatOpenAI(
    model="deepseek-chat",
    openai_api_key=api_key,
    base_url="https://api.deepseek.com",
    timeout=20, 
    max_retries=2
)

DEFAULT_MAP_STYLE = {
    "color": "blue",
    "weight": 2,
    "fillColor": "blue",
    "fillOpacity": 0.3
}

# ==========================================
# BLOCK 2: PYDANTIC SCHEMAS FOR STRUCTURED OUTPUT
# ==========================================
# %% BLOCK 2: PYDANTIC SCHEMAS FOR STRUCTURED OUTPUT
class MapActionParser(BaseModel):
    intent_type: str = Field(
        ..., 
        description="STRENG KLASSIFIZIEREN: 'generate_data' (wenn neue Orte/Städte gesucht werden) ODER 'modify_view' (wenn Farben, Zoom, oder Stil geändert werden)."
    )
    color: Optional[str] = Field(None, description="Die Linienfarbe, z.B. 'red', 'blue', 'green'.")
    weight: Optional[int] = Field(None, description="Die Linienstärke als Ganzzahl zwischen 1 und 10.")
    fillColor: Optional[str] = Field(None, description="Die Füllfarbe für Flächen.")
    fillOpacity: Optional[float] = Field(None, description="Die Transparenz der Füllung zwischen 0.0 und 1.0.")
    focus_city: Optional[str] = Field(None, description="Der Name der Stadt, falls vom Nutzer explizit erwähnt.")
    zoom_level: Optional[int] = Field(None, description="Das gewünschte Zoom-Level als Ganzzahl.")

# ==========================================
# BLOCK 3: LANGGRAPH STATE DEFINITION
# ==========================================
# %% BLOCK 3: LANGGRAPH STATE DEFINITION
class AgentState(TypedDict):
    input_text: str                 
    chat_history: List[dict]        
    intent_type: str                
    geojson_data: Optional[dict]    
    view_updates: dict              
    map_style: dict                 
    error_message: str              

# ==========================================
# BLOCK 4: CORE AGENT NODES
# ==========================================
# %% BLOCK 4: CORE AGENT NODES
def master_intent_router(state: AgentState):
    user_input = state.get("input_text", "")
    current_style = state.get("map_style", DEFAULT_MAP_STYLE.copy())
    history = state.get("chat_history", [])
    
    history_str = ""
    for msg in history[-4:]:
        role_label = "Nutzer" if msg["role"] == "user" else "Assistent"
        history_str += f"{role_label}: {msg['content']}\n"

    intelligent_instruction = f"""
    Du bist das Gehirn eines intelligenten GIS-Systems. Analysiere den aktuellen Nutzerbefehl.

    Bisheriger Chatverlauf für Kontext:
    {history_str}
    Aktueller Kartenstil: {json.dumps(current_style)}

    REGLER FÜR INTENT_TYPE:
    - Wenn der Nutzer neue Gebiete generieren, Orte anzeigen oder neue Daten auf die Karte bringen will, MUSS intent_type = "generate_data" sein.
    - Wenn der Nutzer NUR Farben, Stärken oder das Aussehen bestehender Dinge ändern will (z.B. "Mach es rot", "Farbe blau ändern"), MUSS intent_type = "modify_view" sein.

    GIB AUSSCHLIESSLICH EIN VALIDES JSON-OBJEKT IN FOLGENDEM FORMAT ZURÜCK:
    {{
      "intent_type": "generate_data" oder "modify_view",
      "color": "Farbe oder null",
      "weight": Ganzzahl oder null,
      "fillColor": "Farbe oder null",
      "fillOpacity": Fließkommazahl oder null,
      "focus_city": "Name der erwähnten Stadt oder null",
      "zoom_level": Ganzzahl oder null
    }}
    """
    
    try: # 调用大模型解析用户意图
        response = llm.invoke(intelligent_instruction + f"\nNutzerbefehl: {user_input}")
        content = response.content.strip()
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            parsed_json = json.loads(json_match.group(1))
        else:
            raise ValueError("Kein gültiges JSON.")
            
        detected_intent = parsed_json.get("intent_type", "").strip()
        low_input = user_input.lower()
        
        # 口语识别校正
        if any(k in low_input for k in ["zeige", "erstelle", "karte", "gebiet", "zone", "zeigen", "erstellen", "neue daten"]):
            detected_intent = "generate_data"
        elif not detected_intent:
            detected_intent = "modify_view"
            
        updated_style = current_style.copy()
        for key in ["color", "weight", "fillColor", "fillOpacity"]:
            val = parsed_json.get(key)
            if val is not None and val != "null":
                if key == "weight": updated_style[key] = int(val)
                elif key == "fillOpacity": updated_style[key] = float(val)
                else: updated_style[key] = str(val)
                
        #  如果用户说“变红”，我们自动同步修改边框线颜色和填充颜色
        if "rot" in low_input or parsed_json.get("color") == "red":
            updated_style["color"] = "red"
            updated_style["fillColor"] = "red"
        elif "blau" in low_input:
            updated_style["color"] = "blue"
            updated_style["fillColor"] = "blue"
        elif "grün" in low_input or "grun" in low_input:
            updated_style["color"] = "green"
            updated_style["fillColor"] = "green"

        view_updates = {}
        focus_city = parsed_json.get("focus_city")
        if not focus_city or focus_city == "null":
            for city in ["Dresden", "Berlin", "Leipzig", "Hamburg", "München", "Köln", "Frankfurt"]:
                if city.lower() in low_input:
                    focus_city = city
                    break
                    
        if focus_city and focus_city != "null":
            view_updates["focus_city"] = focus_city
            
        zoom_level = parsed_json.get("zoom_level")
        if zoom_level is not None and zoom_level != "null":
            view_updates["zoom_level"] = int(zoom_level)
            
        return {
            "intent_type": detected_intent,
            "map_style": updated_style,
            "view_updates": view_updates,
            "error_message": ""
        }
    except Exception as e:
        return {"intent_type": "modify_view", "map_style": current_style, "view_updates": {}, "error_message": ""}



def data_generation_node(state: AgentState):

    if state.get("intent_type") == "modify_view":
        return {"geojson_data": state.get("geojson_data")}
        
    user_input = state.get("input_text", "")
    
    # 1. 第一步：让大模型变成纯粹的“实体提取专家”，帮我们抠出准确的德语/英语城市名
    ner_instruction = """
    Du bist ein Geodaten-Extraktor. Erkenne den Namen der SCHLÜSSELSTADT aus dem Nutzerbefehl.
    Antworte AUSSCHLIESSLICH mit dem reinen Stadtnamen im Nominativ (z.B. "Dresden", "Berlin", "München").
    Wenn keine Stadt gefunden wird, antworte mit "null". Gib keinen anderen Text zurück.
    """
    
    try:
        response = llm.invoke(ner_instruction + f"\nNutzerbefehl: {user_input}")
        extracted_city = response.content.strip().replace('"', '').replace("'", "")
        
        # 兜底：如果大模型犯懒漏掉了，我们用之前的本地列表强行匹配
        if not extracted_city or extracted_city.lower() == "null":
            low_input = user_input.lower()
            for city in ["Dresden", "Berlin", "Leipzig", "Hamburg", "München", "Köln", "Frankfurt"]:
                if city.lower() in low_input:
                    extracted_city = city
                    break
                    
        if not extracted_city or extracted_city.lower() == "null":
            return {"error_message": "Keine gültige Stadt im Befehl erkannt."}
            
        # 2. 第二步：高能高阶 GIS 操作！利用 OSM Nominatim API 实时下载真实的城市边界多边形
        # 对城市名进行 URL 编码（防止出现 German Umlauts 如 München 导致请求崩溃）
        encoded_city = urllib.parse.quote(extracted_city)
        osm_url = f"https://nominatim.openstreetmap.org/search?q={encoded_city}&format=geojson&polygon_geojson=1&limit=1"
        
        headers = {
            "User-Agent": "GeoGPT_University_Project_Agent_v4"  # OSM 要求必须提供清晰的 UA
        }
        
        osm_response = requests.get(osm_url, headers=headers, timeout=10)
        
        if osm_response.status_code == 200:
            osm_data = osm_response.json()
            features = osm_data.get("features", [])
            
            if features:
                # 筛选抓取出来的特征，确保它是一个有效的 Polygon 或 MultiPolygon（真实面状轮廓）
                target_feature = None
                for feat in features:
                    geom_type = feat.get("geometry", {}).get("type", "")
                    if geom_type in ["Polygon", "MultiPolygon"]:
                        target_feature = feat
                        break
                
                if not target_feature:
                    target_feature = features[0] # 兜底机制
                
                # 规范化包装为标准的 FeatureCollection，确保 Folium 完美解析
                geojson_contour = {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {
                            "name": extracted_city,
                            "source": "OpenStreetMap Real Boundary"
                        },
                        "geometry": target_feature["geometry"]
                    }]
                }
                
                return {"geojson_data": geojson_contour, "error_message": ""}
            else:
                return {"error_message": f"Die realen Umrisse für '{extracted_city}' wurden bei OSM nicht gefunden."}
        else:
            return {"error_message": f"OSM-Server-Fehler: Statuscode {osm_response.status_code}"}
            
    except Exception as e:
        return {"error_message": f"Fehler bei der Umriss-Generierung: {str(e)}"}
    
# ==========================================
# BLOCK 5: LANGGRAPH WORKFLOW COMPILATION
# ==========================================
# %% BLOCK 5: LANGGRAPH WORKFLOW COMPILATION
workflow = StateGraph(AgentState)
workflow.add_node("intent_router", master_intent_router)
workflow.add_node("data_generator", data_generation_node)
workflow.add_edge(START, "intent_router")
workflow.add_edge("intent_router", "data_generator")
workflow.add_edge("data_generator", END)
agent_app = workflow.compile()

# ==========================================
# BLOCK 6: GIS MAP RENDERING ENGINE
# ==========================================
# %% BLOCK 6: GIS MAP RENDERING ENGINE
import folium
import branca.colormap as cm
from folium.plugins import HeatMap
from geopy.geocoders import Nominatim

geolocator = Nominatim(user_agent="geogpt_dresden_v4")

def create_layered_map(layers, view_updates=None, show_basemap=True):
    import folium

    # 初始化基础地图
    m = folium.Map(
        location=[51.1657, 10.4515],
        zoom_start=6,
        tiles=None
    )

    # 是否显示 OpenStreetMap 底图
    if show_basemap:
        folium.TileLayer(
            tiles="OpenStreetMap",
            name="OpenStreetMap",
            control=False
        ).add_to(m)

    # 如果还没有任何图层，直接返回地图
    if not layers:
        return m

    latest_layer_bounds = None

    for index, layer in enumerate(layers):
        layer_name = layer["name"]
        
        # 🌟 从图层字典中取出前端存入的 LLM 动态参数包
        llm_kwargs = layer.get("folium_kwargs", None)

        if layer["type"] == "points":
            bounds = add_points_layer(m, layer["data"], layer_name, folium_kwargs=llm_kwargs)
            if index == len(layers) - 1: latest_layer_bounds = bounds

        elif layer["type"] == "heatmap":
            bounds = add_heatmap_layer(m, layer["data"], layer_name, layer.get("data_field"), folium_kwargs=llm_kwargs)
            if index == len(layers) - 1: latest_layer_bounds = bounds

        elif layer["type"] == "choropleth":
            bounds = add_choropleth_layer(m, layer["data"], layer_name, layer["data_field"], folium_kwargs=llm_kwargs)
            if index == len(layers) - 1:latest_layer_bounds = bounds

        elif layer["type"] in ["polygon", "line", "geojson"]:
            # 将 polygon、line 统一收拢到声明式的 GeoJson 渲染管道
            bounds = add_geojson_layer(m, layer["data"], layer_name, folium_kwargs=llm_kwargs)
            if index == len(layers) - 1: latest_layer_bounds = bounds

    # 动态调整地图边界和视角
    if view_updates:
        m.fit_bounds([[view_updates["min_lat"], view_updates["min_lon"]], 
                      [view_updates["max_lat"], view_updates["max_lon"]]])
    elif latest_layer_bounds:
        m.fit_bounds(latest_layer_bounds)
    else:
        m.fit_bounds([[50.5, 9.5], [52.5, 11.5]])

    folium.LayerControl().add_to(m)
    return m


def add_points_layer(m, points, layer_name, folium_kwargs=None):
    """
    执行节点：渲染普通点图层，允许 LLM 控制 Marker 的图标和颜色
    """
    import folium
    
    if folium_kwargs is None:
        folium_kwargs = {"color": "blue", "icon": "info-sign"}
        
    fg = folium.FeatureGroup(name=layer_name)
    bounds = []
    
    for pt in points:
        lat, lon = pt["lat"], pt["lon"]
        bounds.append([lat, lon])
        
        # 🌟 核心：解包 LLM 的参数来配置 Folium Icon
        popup_text = pt.get("name", "Punkt")
        folium.Marker(
            location=[lat, lon],
            popup=popup_text,
            icon=folium.Icon(
                color=folium_kwargs.get("color", "blue"),
                icon=folium_kwargs.get("icon", "info-sign")
            )
        ).add_to(fg)
        
    fg.add_to(m)
    return bounds

def add_heatmap_layer(m, points, layer_name, data_field, folium_kwargs=None):
    """
    执行节点：不参与参数决策，直接解包 LLM 传进来的配置字典
    """
    if folium_kwargs is None:
        folium_kwargs = {"radius": 25, "blur": 15, "min_opacity": 0.3}

    heat_data = []
    for point in points:
        lat, lon = point["lat"], point["lon"]
        value = point.get("properties", {}).get(data_field) if data_field else 1.0
        if value is not None:
            try:
                heat_data.append([lat, lon, float(value)])
            except:
                pass

    if heat_data:
        # 🌟 核心：利用 **folium_kwargs 动态解包大模型生成的参数
        HeatMap(
            heat_data,
            name=layer_name,
            **folium_kwargs
        ).add_to(m)

    return [[row[0], row[1]] for row in heat_data]

# 可以根据数值字段自动给 Polygon 上色
def add_choropleth_layer(m, geojson_data, layer_name, data_field, folium_kwargs=None):

    if folium_kwargs is None:
        folium_kwargs = {
            "fillColor": "YlOrRd",
            "color": "black",
            "weight": 0.5,
            "fillOpacity": 0.7
        }

    COLORMAPS = {
    "YlOrRd": cm.linear.YlOrRd_09,
    "Blues": cm.linear.Blues_09,
    "blue": cm.linear.Blues_09,
    "Greens": cm.linear.Greens_09,
    "green": cm.linear.Greens_09,
    "Purples": cm.linear.Purples_09,
    "purple": cm.linear.Purples_09,
    "OrRd": cm.linear.OrRd_09,
    "orange": cm.linear.OrRd_09,
    "GnBu": cm.linear.GnBu_09
    }
    features = geojson_data.get("features", [])

    values = []

    for feature in features:
        properties = feature.get("properties", {})
        value = properties.get(data_field)

        if value is not None:
            try:
                values.append(float(value))
            except:
                pass

    if not values:
        return []

    min_value = min(values)
    max_value = max(values)

    if min_value == max_value:
        max_value = min_value + 1

    # colormap = cm.linear.YlOrRd_09.scale(min_value, max_value)
    colormap = COLORMAPS.get(folium_kwargs.get("fillColor", "YlOrRd"), cm.linear.YlOrRd_09).scale(min_value, max_value)
    colormap.caption = f"{layer_name} - {data_field}"

    def style_function(feature):
        properties = feature.get("properties", {})
        value = properties.get(data_field)

        try:
            value = float(value)

            fill_color = colormap(value)

        except:
            fill_color = "#cccccc"

        return {
            "fillColor": fill_color,
            "color": folium_kwargs.get("color", "black"),
            "weight": folium_kwargs.get("weight", 0.5),
            "fillOpacity": folium_kwargs.get("fillOpacity", 0.7),
        }

    fg = folium.FeatureGroup(name=layer_name)

    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=style_function,
        tooltip=create_geojson_tooltip(geojson_data)
    ).add_to(fg)

    fg.add_to(m)
    colormap.add_to(m)

    try:
        return geo_layer.get_bounds()
    except Exception:
        return []
# Line Renderer
def add_line_layer(m, geojson_data, layer_name, color="blue", weight=3):
    fg = folium.FeatureGroup(name=layer_name)
    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=lambda feature: {
            "color": color,
            "weight": weight
        }
    ).add_to(fg)
    fg.add_to(m)
    try:
        return geo_layer.get_bounds()
    except:
        return []
# geojson Renderer
def add_geojson_layer(m, geojson_data, layer_name, folium_kwargs=None):
    """
    执行节点：渲染 Polygon 或 Line 矢量图层，直接应用 LLM 的样式参数计划
    """
    import folium
    
    # 如果 LLM 没有提供样式，使用合理的默认值
    if folium_kwargs is None:
        folium_kwargs = {
            "fillColor": "blue",
            "color": "blue",
            "weight": 2,
            "fillOpacity": 0.4
        }
        
    fg = folium.FeatureGroup(name=layer_name)
    
    # 🌟 核心：LLM 生成的参数直接映射到 GeoJson 的 style_function 中
    geo_layer = folium.GeoJson(
        geojson_data,
        name=layer_name,
        style_function=lambda feature: {
            "fillColor": folium_kwargs.get("fillColor", "blue"),
            "color": folium_kwargs.get("color", "blue"),
            "weight": folium_kwargs.get("weight", 2),
            "fillOpacity": folium_kwargs.get("fillOpacity", 0.4),
            "opacity": folium_kwargs.get("opacity", 1.0)
        },
        tooltip=create_geojson_tooltip(geojson_data)
        # 如果你们原先有 tooltip 逻辑，可以在这里保留
    ).add_to(fg)
    
    fg.add_to(m)
    return geo_layer.get_bounds()

def create_geojson_tooltip(geojson_data):
    fields = get_tooltip_fields(geojson_data)
    if fields: return folium.GeoJsonTooltip(fields=fields, aliases=fields)
    return None

def get_tooltip_fields(geojson_data):
    try:
        if geojson_data["type"] == "FeatureCollection":
            features = geojson_data.get("features", [])
            if features and "properties" in features[0]:
                return list(features[0]["properties"].keys())[:3]
    except Exception: pass
    return []
# 判断上传的 GeoJSON 是否适合做 Choropleth
def is_polygon_geojson(geojson_data):
    try:
        features = geojson_data.get("features", [])

        for feature in features:
            geometry_type = feature.get("geometry", {}).get("type")
            if geometry_type in ["Polygon", "MultiPolygon"]:
                return True

    except Exception:
        pass

    return False
# 判断线数据
def is_line_geojson(geojson_data):
    try:
        features = geojson_data.get("features", [])

        for feature in features:
            geometry_type = feature.get(
                "geometry",
                {}
            ).get("type")
            if geometry_type in [
                "LineString",
                "MultiLineString"
            ]:
                return True
    except:
        pass

    return False

# 自动找出可以做 Choropleth 的字段
def get_numeric_geojson_fields(geojson_data):
    numeric_fields = set()

    try:
        features = geojson_data.get("features", [])

        for feature in features:
            properties = feature.get("properties", {})

            for key, value in properties.items():
                if isinstance(value, (int, float)):
                    numeric_fields.add(key)

                else:
                    try:
                        float(value)
                        numeric_fields.add(key)
                    except:
                        pass

    except Exception:
        pass

    # 过滤掉明显不适合作为制图字段的 ID 类字段
    excluded_keywords = ["id", "objectid", "fid", "katnam"]

    filtered_fields = []

    for field in numeric_fields:
        lower_field = field.lower()

        if not any(keyword in lower_field for keyword in excluded_keywords):
            filtered_fields.append(field)

    return sorted(filtered_fields)

# ==========================================
# BLOCK 7: DATA PARSING FACTORY
# ==========================================
# %% BLOCK 7: DATA PARSING FACTORY
import pandas as pd

def parse_uploaded_file(uploaded_file):
    filename = uploaded_file.name.lower()
    if filename.endswith(".geojson"):
        geojson_data = json.load(uploaded_file)

        if is_polygon_geojson(geojson_data):
            return {
                "type": "polygon",
                "data": geojson_data
            }

        if is_line_geojson(geojson_data):
                    return {
                        "type": "geojson",  #  由 "line" 改为 "geojson"，与大模型及渲染引擎对齐
                        "data": geojson_data
                    }

        points = convert_geojson_points_to_points(geojson_data)
        if points:
            return {
                "type": "points",
                "data": points,
                "original_geojson": geojson_data
            }

        return {
            "type": "geojson",
            "data": geojson_data
        }
    
    elif filename.endswith(".json"):
        data = json.load(uploaded_file)

        if isinstance(data, dict) and data.get("type") in ["FeatureCollection", "Feature"]:

            if is_polygon_geojson(data):
                return {
                    "type": "polygon",
                    "data": data
                }

            if is_line_geojson(data):
                return {
                    "type": "line",
                    "data": data
                }

            points = convert_geojson_points_to_points(data)
            if points:
                return {
                    "type": "points",
                    "data": points,
                    "original_geojson": data
                }

            return {
                "type": "geojson",
                "data": data
            }
        return {"type": "points", "data": parse_json_points(data)}
    elif filename.endswith(".csv"): return {"type": "points", "data": parse_csv_points(pd.read_csv(uploaded_file))}
    raise ValueError("Format nicht unterstützt.")

def parse_json_points(data):
    points = []

    if isinstance(data, dict):
        data = [data]

    for i, item in enumerate(data):
        lat = item.get("lat")
        lon = item.get("lon")

        if lat is not None and lon is not None:
            properties = {}

            for key, value in item.items():
                if key not in ["lat", "lon", "name"]:
                    try:
                        properties[key] = float(value)
                    except:
                        properties[key] = value

            points.append({
                "name": item.get("name", f"Point {i+1}"),
                "lat": float(lat),
                "lon": float(lon),
                "properties": properties
            })

    return points

def convert_geojson_points_to_points(geojson_data):
    points = []

    features = geojson_data.get("features", [])

    for i, feature in enumerate(features):
        geometry = feature.get("geometry", {})
        properties = feature.get("properties", {})

        if geometry.get("type") == "Point":
            coordinates = geometry.get("coordinates", [])

            if len(coordinates) >= 2:
                lon = coordinates[0]
                lat = coordinates[1]

                points.append({
                    "name": properties.get("standbesch", f"Point {i+1}"),
                    "lat": float(lat),
                    "lon": float(lon),
                    "properties": properties
                })

    return points

def parse_csv_points(df):
    if "lat" not in df.columns or "lon" not in df.columns:
        raise ValueError("CSV muss die Spalten lat und lon enthalten.")

    points = []

    for i, row in df.iterrows():
        properties = {}

        for col in df.columns:
            if col not in ["lat", "lon", "name"]:
                value = row[col]

                try:
                    properties[col] = float(value)
                except:
                    properties[col] = value

        points.append({
            "name": row["name"] if "name" in df.columns else f"Point {i+1}",
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "properties": properties
        })

    return points

# ==========================================
# zheng BLOCK 8: Karte exportieren
# ==========================================
from branca.element import Element
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader

# 添加标题
def Titel_hinzufügen(m:folium.Map,titel:str) -> None:
    title_html = f'''
    <div style="
        position: fixed;
        top: 10px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 9999;
        background-color: white;
        padding: 8px 15px;
        border-radius: 6px;
        font-size: 18px;
        font-weight: bold;
        box-shadow: 0 0 6px rgba(0,0,0,0.3);
    ">
        {titel}
    </div>
    '''
    m.get_root().html.add_child(Element(title_html))

#根据HTML创建图片
def html_to_png(html:str,png:str) -> None:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1200,800")
    driver = webdriver.Chrome(options=options)
    driver.get("file://" + os.path.abspath(html))
    time.sleep(0.1)  # wait...
    driver.save_screenshot(png)
    driver.quit()

#根据图片创建PDF
def png_to_pdf(png:str,pdf:str) -> None:
    c = canvas.Canvas(pdf, pagesize=landscape(A4))
    page_width, page_height = landscape(A4)
    img = ImageReader(png)
    img_width, img_height = img.getSize()
    ratio = min(page_width / img_width, page_height / img_height)
    new_width = img_width * ratio
    new_height = img_height * ratio
    x = (page_width - new_width) / 2
    y = (page_height - new_height) / 2
    c.drawImage(png, x, y, width=new_width, height=new_height)
    c.save()

TOOLS = [
    {
        "name": "create_map",
        "description": "Karte erstellen"
    },
    {
        "name": "set_title",
        "description": "Titel zurückgeben"
    },
    {
        "name": "export_map",
        "description": "Karte exportieren"
    },
    {
        "name": "Kartenstil_anpassen",
        "description": "Ändern den Kartenstil in einem bestimmten Layer"
    },
    {
        "name": "Layer_Parameter_anzeigen",
        "description": "Zeigt die aktuellen und einstellbaren Darstellungsparameter eines Kartenlayers, z.B. Farbe, Transparenz, Linienstärke, Radius oder Datenfeld."
    },
    {
        "name": "Layer_verwalten",
        "description": "Entferne oder kopiere bestehende Layer."
    },
    {
        "name": "Geodatei_analysieren",
        "description": "Analysiert die Struktur und den Inhalt einer Geodatendatei und erklärt die enthaltenen Informationen."
    },
    {
        "name": "get_overview",
        "description": "Erklärt die Anwendung, ihren Zweck und den typischen Arbeitsablauf für den Nutzer."
    },
    {
        "name": "spatial_analysis",
        "description": "Führt räumliche Analysen durch: Pufferanalyse (Buffer), Nachbarschaftsanalyse (Nearest Neighbor) oder räumliche Überlagerungszählungen (Overlay Count)."
    },
    {
        "name": "generiere_daten_bericht",
        "description": "Erstellt einen ausführlichen statistischen und geografischen Bericht über die hochgeladenen Geodaten."
    },
    {
        "name": "ml_analysis",
        "description": "Führt Machine Learning Analysen durch, wie zeitliche Trendprognosen (Regression) oder räumliche Muster-Vorhersagen."
    }
]


def normalize_user_command(text: str):
    """
    Normalisiert freie, ungenaue oder umgangssprachliche Nutzereingaben
    zu einer klaren GIS-Anweisung.
    """

    system_prompt = """
    Du bist ein GIS-Sprachversteher.

    Deine Aufgabe:
    Formuliere die Nutzereingabe als klare, standardisierte GIS-Anweisung um.

    Wichtige Synonyme und Schreibvarianten:
    - mache, mach, erstelle, zeige, visualisiere, stelle dar = Karte erstellen oder Daten darstellen
    - Choropleth, Choroplethen, Choroplehten, Choroplethenkarte, choroplethische Karte = Choropleth-Karte
    - Bevölkerung, Bevoelkerung, Einwohner, Einwohnerzahl, Population = Bevölkerungsdaten
    - Fläche, Flaeche, Flächengröße, Gebietgröße = Flächenberechnung
    - Dichte, Punktdichte, pro km², pro km2 = Dichteanalyse
    - zähle, zaehle, Anzahl, wie viele = Zählanalyse

    Beispiele:
    - "mache Choroplehten Karte mit Daten Bevölkerung nach Stadtteilen"
      -> "Erstelle eine Choropleth-Karte nach dem Attribut Bevölkerung für die Stadtteile."

    - "mach heatmap mit temperatur"
      -> "Erstelle eine Heatmap nach dem Attribut Temperatur."

    - "wie groß sind die stadtteile"
      -> "Berechne die Fläche der Stadtteile."

    - "punkte pro km2"
      -> "Berechne die Punktdichte pro Quadratkilometer."

    Gib nur JSON zurück:

    {
        "normalized_command": "...",
        "recognized_meaning": "...",
        "confidence": 0.0
    }
    """

    try:
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ])

        content = response.content.strip()
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)

        if json_match:
            return json.loads(json_match.group(1))

    except Exception:
        pass

    return {
        "normalized_command": text,
        "recognized_meaning": "unknown",
        "confidence": 0.0
    }


def parse_intent_with_llm(text: str):
    """
    LLM-basierte Tool-Auswahl auf Basis einer möglichst klaren GIS-Anweisung.
    """

    tool_text = "\n".join(
        f"- {t['name']}: {t['description']}"
        for t in TOOLS
    )

    system_prompt = f"""
    Du bist ein GIS-Assistent.

    Deine Aufgabe ist es, die Nutzeranfrage semantisch zu verstehen
    und dem passenden Tool zuzuordnen.

    Folgende Tools stehen zur Verfügung:

    {tool_text}

        Beispiele:
        - "Erstelle eine Karte" -> create_map
        - "Mache eine Karte" -> create_map
        - "Visualisiere die Daten" -> create_map
        - "Erstelle eine Choropleth-Karte nach Bevölkerung" -> create_map
        - "Erstelle eine Heatmap nach Temperatur" -> create_map
        - "Was enthält diese Datei?" -> Geodatei_analysieren
        - "Welche Karten kann ich mit diesen Daten machen?" -> Geodatei_analysieren
        - "Berechne die Fläche" -> spatial_analysis
        - "Zähle Punkte in Gebieten" -> spatial_analysis
        - "Berechne die Punktdichte pro km²" -> spatial_analysis
        - "Welche Parameter hat dieser Layer?" -> Layer_Parameter_anzeigen
        - "Welche Einstellungen kann ich bei diesem Layer ändern?" -> Layer_Parameter_anzeigen
        - "Welche Darstellungsparameter gibt es?" -> Layer_Parameter_anzeigen
        - "Welche Farbe und Transparenz kann ich anpassen?" -> Layer_Parameter_anzeigen
        - "Ändere die Farbe auf blau" -> Kartenstil_anpassen
        - "Setze die Transparenz auf 0.5" -> Kartenstil_anpassen
        - "Erstelle einen Bericht" -> generiere_daten_bericht

        Wichtige Unterscheidung:
        - Wenn der Nutzer fragt, welche Parameter möglich sind, wähle Layer_Parameter_anzeigen.
        - Wenn der Nutzer konkrete Werte ändern möchte, wähle Kartenstil_anpassen.

    Gib nur JSON zurück:

    {{
        "tool": "<tool_name>",
        "confidence": 0.0,
        "reason": "kurze Begründung"
    }}

    Wenn kein Tool passt:

    {{
        "tool": "error",
        "confidence": 0.0,
        "reason": "unklar"
    }}
    """

    try:
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ])

        content = response.content.strip()
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)

        if json_match:
            return json.loads(json_match.group(1))

    except Exception:
        pass

    return {
        "tool": "error",
        "confidence": 0.0,
        "reason": "Kein gültiges JSON erkannt"
    }


def validate_intent_with_rules(original_text: str, normalized_text: str, llm_result: dict):
    """
    Prüft das LLM-Ergebnis und korrigiert nur offensichtliche Fehlklassifikationen.
    """

    combined_text = f"{original_text} {normalized_text}".lower()

    tool = llm_result.get("tool", "error")

    try:
        confidence = float(llm_result.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    parameter_signals = [
        "parameter",
        "parametern",
        "layer-parameter",
        "layer parameter",
        "darstellungsparameter",
        "kartenparameter",
        "einstellungen",
        "einstellbare",
        "einstellbare parameter",
        "welche parameter",
        "welche einstellungen",
        "welche parameter hat",
        "welche einstellungen hat",
        "was kann ich ändern",
        "was kann ich aendern",
        "was kann ich anpassen",
        "welche werte kann ich anpassen",
        "farbe und transparenz",
        "farbe oder transparenz",
        "füllfarbe",
        "fuellfarbe",
        "randfarbe",
        "transparenz",
        "deckkraft",
        "opacity",
        "fillopacity",
        "linienbreite",
        "linienstärke",
        "linienstaerke",
        "randstärke",
        "randstaerke",
        "radius",
        "blur",
        "unschärfe",
        "unschaerfe"
    ]

    style_change_signals = [
        "ändere",
        "aendere",
        "setze",
        "mach",
        "mache",
        "erhöhe",
        "erhoehe",
        "reduziere",
        "verringere"
    ]

    if any(signal in combined_text for signal in parameter_signals):
        if not any(signal in combined_text for signal in style_change_signals):
            return {
                "tool": "Layer_Parameter_anzeigen",
                "confidence": max(confidence, 0.85),
                "reason": "Validierung: Parameterabfrage erkannt."
            }


    # Spatial Analysis ist spezifischer als normale Kartenerstellung.
    spatial_signals = [
        "fläche", "flaeche", "flächengröße", "flaechengroesse",
        "dichte", "punktdichte", "pro km²", "pro km2",
        "zähle", "zaehle", "anzahl",
        "puffer", "buffer",
        "abstand", "entfernung"
    ]

    if any(signal in combined_text for signal in spatial_signals):
        return {
            "tool": "spatial_analysis",
            "confidence": max(confidence, 0.8),
            "reason": "Validierung: räumliche Analyse erkannt."
        }

    report_signals = [
        "bericht", "report", "zusammenfassung", "interpretiere"
    ]

    if any(signal in combined_text for signal in report_signals):
        return {
            "tool": "generiere_daten_bericht",
            "confidence": max(confidence, 0.8),
            "reason": "Validierung: Bericht erkannt."
        }

    map_signals = [
        "karte", "choropleth", "choroplethen", "choroplehten",
        "heatmap", "visualisiere", "darstellen", "stelle dar",
        "mache", "mach", "zeige", "erstelle"
    ]

    if tool in ["error", "get_overview"] or confidence < 0.55:
        if any(signal in combined_text for signal in map_signals):
            return {
                "tool": "create_map",
                "confidence": max(confidence, 0.75),
                "reason": "Validierung: Karten- oder Visualisierungsanfrage erkannt."
            }

    return llm_result


def parse_intent(text: str):
    """
    Hybrides Intent Parsing:
    1. Freie Nutzereingabe wird durch das LLM semantisch normalisiert.
    2. Die normalisierte GIS-Anweisung wird einem Tool zugeordnet.
    3. GIS-Regeln validieren offensichtliche Fehlklassifikationen.
    """

    normalized = normalize_user_command(text)
    normalized_text = normalized.get("normalized_command", text)

    llm_result = parse_intent_with_llm(normalized_text)

    final_result = validate_intent_with_rules(
        original_text=text,
        normalized_text=normalized_text,
        llm_result=llm_result
    )

    return {
        "tool": final_result.get("tool", "error")
    }

def get_selected_layer_name(user_text: str,layers):
    """
    Bestimmen den Ziellayer
    """

    layer_names = "\n".join([l["name"] for l in layers])
    system_prompt = f"""
        Du bist ein GIS-Assistent.

        Der Benutzer kann Kartenlayer verwalten oder auswählen.

        Layer werden vom Nutzer bereitgestellt.

        Deine Aufgabe ist es,
        zu bestimmen, auf welchen Layer sich die Benutzeranfrage eindeutig bezieht.

        Regeln:
        - Nennt der Nutzer einen Layer eindeutig, gib genau diesen zurück.
        - Nennt der Nutzer keinen Layer ausdrücklich, gib immer den letzten Layer der Liste zurück.
        - Rate niemals einen Layer anhand seines Inhalts oder seiner Bedeutung.
        - Antworte ausschließlich im JSON-Format.
        - Kein zusätzlicher Text

        Format:

        {{
        "layer_name": "<name>"
        }}

        WICHTIG:
        Falls der Nutzer keinen Layer ausdrücklich nennt
        oder die Anfrage keinem Layer eindeutig zugeordnet werden kann,
        wähle immer den letzten Layer aus der Liste.
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"user_input: {user_text}, die Liste aktueller Layer:\n\n{layer_names}"}
    ])

    result = json.loads(response.content.strip())
    return result["layer_name"]

def get_selected_layer_names(user_text: str, layers):
    """
    Bestimmt einen oder mehrere Layer.
    Wird nur für create_map verwendet.
    """

    layer_names = "\n".join([
        f"{i + 1}. {l['name']} ({l['type']})"
        for i, l in enumerate(layers)
    ])

    system_prompt = f"""
        Du bist ein GIS-Assistent.

        Bestimme alle Layer, die der Nutzer in seiner Anfrage nennt.

        Regeln:
        - Wenn der Nutzer einen Layer nennt, gib genau diesen Layer zurück.
        - Wenn der Nutzer mehrere Layer nennt, gib alle genannten Layer zurück.
        - Wenn der Nutzer "Datei 6 und 2" sagt, gib Layer 6 und Layer 2 zurück.
        - Wenn der Nutzer "Layer 1, 3 und 5" sagt, gib Layer 1, Layer 3 und Layer 5 zurück.
        - Wenn kein Layer genannt wird, gib nur den letzten Layer zurück.
        - Antworte ausschließlich im JSON-Format.
        - Keine Erklärungen.

        Format:
        {{
        "layer_names": ["<name1>", "<name2>"]
        }}

        Aktuelle Layer:
        {layer_names}
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ])

    result = json.loads(response.content.strip())
    return result["layer_names"]


def set_selected_layer_name(layer):
    """
    Geben dem Layer einen geeigneten Name
    """

    system_prompt = """
        Du bist ein kreativer GIS-Assistent.

        Du erhältst Informationen über einen geographischen Layer.

        Deine Aufgabe:
        Erzeuge einen passenden, anschaulichen und gut klingenden MAP TITEL.

        Der Titel soll:
        - beschreibend sein
        - nicht nur den Dateinamen wiederholen
        - etwas menschlicher und natürlicher klingen
        - optional den Kontext oder die Bedeutung des Layers ausdrücken

        Antworte nur im JSON-Format:

        {
        "title": "<creative title>"
        }

        Keine Erklärungen.
        """
    if "data_field" in layer:
        minimal_layer = {
        "name": layer["name"],
        "data_field": layer["data_field"],
        "type": layer["type"],
    }
    else:
        minimal_layer = {
        "name": layer["name"],
        "type": layer["type"],
    }
    content = json.dumps(minimal_layer, ensure_ascii=False, indent=2)

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content}
    ])

    result = json.loads(response.content.strip())
    return result["title"]

SUPPORTED_MAP_TYPES = [
    "points, geeignet für Punktgeometrien",
    "line, geeignet für Liniengeometrien",
    "polygon, geeignet für Polygongeometrien",
    "heatmap, geeignet für Punktgeometrien mit numerischen Attributen",
    "choropleth, geeignet für Polygongeometrien mit numerischen Attributen"
]
# SUPPORTED_MAP_TYPES = {
#     "points": "für Punktgeometrien",
#     "line": "für Liniengeometrien",
#     "polygon": "für Polygongeometrien",
#     "heatmap": "nur für Punktdaten mit hoher Punktdichte",
#     "choropleth": "für Polygondaten mit numerischen Attributen"
# }

def bestimmen_Kartentyp(user_text: str, layer) -> dict:
    system_prompt = """
    Du bist ein Senior GIS-Kartograph und Code-Generator.

    Deine Aufgabe ist es, den passenden Kartentyp für einen Folium-Layer zu bestimmen.

    Mögliche type-Werte:
    - "points": Standard für Punktdaten
    - "heatmap": nur für Punktdaten, wenn der Nutzer ausdrücklich Dichte, Heatmap oder Hotspots möchte
    - "geojson": Standard für Polygon- und Liniengeometrien
    - "choropleth": nur für Polygongeometrien, wenn der Nutzer ausdrücklich eine thematische Karte nach einem numerischen Attribut möchte

    WICHTIGE REGELN:
    1. Wenn der Layer ein Polygon-Layer ist und der Nutzer nur allgemein eine Karte erstellen möchte,
       dann MUSS type = "geojson" sein.
    2. Wenn der Nutzer sagt:
       - "normale Karte"
       - "normale Flächenkarte"
       - "Polygon anzeigen"
       - "Flächen anzeigen"
       - "Stadtteile anzeigen"
       dann MUSS type = "geojson" sein.
    3. Wähle "choropleth" NUR, wenn der Nutzer ausdrücklich sagt:
       - "Choropleth"
       - "thematische Karte"
       - "nach Bevölkerung"
       - "nach PM10"
       - "nach area_km2"
       - "farblich nach Wert"
       - "nach einem numerischen Attribut einfärben"
    4. Nur weil numerische Felder vorhanden sind, darf NICHT automatisch Choropleth gewählt werden.
    5. Für Linien und Polygone ohne ausdrückliche Statistik immer "geojson" wählen.

    GIB AUSSCHLIESSLICH EIN VALIDES JSON-OBJEKT IN FOLGENDEM FORMAT ZURÜCK:
    {
        "type": "heatmap" | "choropleth" | "geojson" | "points",
        "layer_name": "Ein schöner deutscher Name",
        "data_field": "Feldname_oder_null",
        "rationale_de": "Kurze kartographische Begründung auf Deutsch.",
        "critical_attributes_notice": "Hinweis an den Nutzer auf Deutsch.",
        "folium_kwargs": {
            "fillColor": "blue",
            "color": "blue",
            "weight": 2,
            "fillOpacity": 0.4
        }
    }
    """

    minimal_layer = {
        "name": layer["name"],
        "type": layer["type"],
        "data_fields": layer.get("data_fields", []),
    }

    content = json.dumps(minimal_layer, ensure_ascii=False, indent=2)

    try:
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Nutzerbefehl: {user_text}\n\nLayer-Metadaten:\n{content}"}
        ])

        json_match = re.search(r'(\{.*\})', response.content.strip(), re.DOTALL)

        if json_match:
            return json.loads(json_match.group(1))

        raise ValueError()

    except Exception:
        if layer["type"] in ["polygon", "line", "geojson"]:
            fallback_type = "geojson"
        else:
            fallback_type = layer["type"]

        return {
            "type": fallback_type,
            "layer_name": layer["name"],
            "data_field": None,
            "rationale_de": "Standard-Visualisierung ohne thematische Klassifizierung.",
            "critical_attributes_notice": "Es wurde eine normale Geometriekarte erstellt.",
            "folium_kwargs": {
                "fillColor": "blue",
                "color": "blue",
                "weight": 2,
                "fillOpacity": 0.4
            }
        }

def get_map_style(user_text: str,layer):

    aktuell_style=json.dumps(layer["folium_kwargs"], ensure_ascii=False, indent=2)
    system_prompt = f"""
        Du bist ein GIS-Styling-Assistent für Folium (Leaflet).

        Du erhältst den aktuellen Kartenstil :

        DEFAULT_MAP_STYLE:
        {aktuell_style}

        Deine Aufgabe:
        Passe diesen Style an, basierend auf der Benutzeranfrage.

        REGELN (SEHR WICHTIG):
        1. Ändere nur die Werte, nicht die Keys
        2. KEINE neuen Felder hinzufügen
        3. KEINE Felder entfernen
        4. Alle Werte müssen aus den erlaubten Bereichen stammen
        5. Wenn die Anfrage unklar ist → behalte den alten Wert

        ERLAUBTE WERTE:

        color / fillColor:
        - "blue"
        - "red"
        - "green"
        - "black"
        - "orange"
        - "purple"
        - "gray"
        - "yellow"

        weight:
        - ganze Zahlen von 1 bis 10

        fillOpacity:
        - Werte zwischen 0.0 und 1.0

        Keine Erklärungen, nur JSON.
        """
        # OUTPUT FORMAT (streng):
        # {{
        # "color": "...",
        # "weight": ...,
        # "fillColor": "...",
        # "fillOpacity": ...
        # }}
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ])

    result = json.loads(response.content.strip())
    return result

def get_multi_layer_styles(user_text: str, layers):
    """
    Bestimmt Stiländerungen für mehrere Layer.
    Wird nur für Kartenstil_anpassen verwendet.
    """

    layer_infos = "\n".join([
        f"{i + 1}. {l['name']} ({l['type']}), aktueller Stil: {json.dumps(l.get('folium_kwargs', {}), ensure_ascii=False)}"
        for i, l in enumerate(layers)
    ])

    system_prompt = f"""
        Du bist ein GIS-Styling-Assistent für Folium.

        Der Nutzer kann den Stil eines oder mehrerer Layer ändern.

        Regeln:
        - Wenn der Nutzer nur einen Layer nennt, ändere nur diesen Layer.
        - Wenn der Nutzer mehrere Layer nennt, erstelle für jeden genannten Layer ein eigenes Update.
        - Wenn der Nutzer sagt "Layer 1 blau und Layer 2 rot", dann ändere Layer 1 und Layer 2 unterschiedlich.
        - Wenn kein Layer genannt wird, ändere nur den letzten Layer.
        - Gib nur Werte zurück, die wirklich geändert werden sollen.
        - Antworte ausschließlich im JSON-Format.
        - Keine Erklärungen.

        Erlaubte Style-Keys:
        - color
        - fillColor
        - weight
        - fillOpacity
        - opacity
        - radius
        - blur
        - min_opacity

        Erlaubte Farben:
        - blue
        - red
        - green
        - black
        - orange
        - purple
        - gray
        - yellow

        Format:
        {{
        "updates": [
            {{
            "layer_number": 1,
            "style": {{
                "fillColor": "blue"
            }}
            }},
            {{
            "layer_number": 2,
            "style": {{
                "color": "red"
            }}
            }}
        ]
        }}

        Aktuelle Layer:
        {layer_infos}
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ])

    content = response.content.strip()
    json_match = re.search(r'(\{.*\})', content, re.DOTALL)

    if json_match:
        return json.loads(json_match.group(1))

    return {"updates": []}

def describe_layer_parameters(layer: dict) -> str:
    """
    Gibt aktuelle und einstellbare Parameter eines Kartenlayers zurück.
    """

    layer_name = layer.get("name", "Unbenannter Layer")
    layer_type = layer.get("type", "unbekannt")
    current_kwargs = layer.get("folium_kwargs", {})
    data_field = layer.get("data_field", None)

    parameter_catalog = {
        "points": {
            "color": "Markerfarbe. Beispiele: blue, red, green, orange, purple, black, gray.",
            "icon": "Markersymbol. Beispiele: info-sign, cloud, home, star, eye-open.",
        },
        "heatmap": {
            "radius": "Radius der Wärmepunkte. Größerer Wert = weichere, größere Hotspots.",
            "blur": "Weichzeichnung der Heatmap. Größerer Wert = stärker verschwommene Übergänge.",
            "min_opacity": "Minimale Sichtbarkeit der Heatmap. Werte zwischen 0.0 und 1.0.",
            "max_zoom": "Zoomstufe, bis zu der die Intensität berechnet wird.",
        },
        "choropleth": {
            "data_field": "Numerisches Attribut, nach dem die Flächen eingefärbt werden.",
            "fillColor": "Farbskala für die thematische Einfärbung. Beispiele: YlOrRd, Blues, Greens, Purples, OrRd, GnBu.",
            "color": "Randfarbe der Polygone. Beispiele: black, blue, red, gray.",
            "weight": "Randstärke der Polygone. Werte z.B. 0.5 bis 10.",
            "fillOpacity": "Transparenz der Flächenfüllung. Werte zwischen 0.0 und 1.0.",
            "opacity": "Transparenz der Umrandung. Werte zwischen 0.0 und 1.0.",
        },
        "polygon": {
            "fillColor": "Füllfarbe der Fläche. Beispiele: blue, red, green, orange, purple, gray.",
            "color": "Randfarbe der Fläche. Beispiele: black, blue, red, gray.",
            "weight": "Randstärke der Fläche. Werte z.B. 1 bis 10.",
            "fillOpacity": "Transparenz der Flächenfüllung. Werte zwischen 0.0 und 1.0.",
            "opacity": "Transparenz der Umrandung. Werte zwischen 0.0 und 1.0.",
        },
        "geojson": {
            "fillColor": "Füllfarbe für Polygon-Geometrien. Beispiele: blue, red, green, orange, purple, gray.",
            "color": "Linien- oder Randfarbe. Beispiele: black, blue, red, gray.",
            "weight": "Linien- oder Randstärke. Werte z.B. 1 bis 10.",
            "fillOpacity": "Transparenz der Flächenfüllung. Werte zwischen 0.0 und 1.0.",
            "opacity": "Transparenz der Linie oder Umrandung. Werte zwischen 0.0 und 1.0.",
        },
        "line": {
            "color": "Linienfarbe. Beispiele: blue, red, green, orange, purple, black.",
            "weight": "Linienstärke. Werte z.B. 1 bis 10.",
            "opacity": "Linientransparenz. Werte zwischen 0.0 und 1.0.",
            "dashArray": "Gestrichelte Linie. Beispiel: '5, 5'.",
        },
    }

    # Falls ein Choropleth-Layer ohne vollständige kwargs erstellt wurde
    default_values = {
        "points": {"color": "blue", "icon": "info-sign"},
        "heatmap": {"radius": 25, "blur": 15, "min_opacity": 0.3},
        "choropleth": {"fillColor": "YlOrRd", "color": "black", "weight": 0.5, "fillOpacity": 0.7},
        "polygon": {"fillColor": "blue", "color": "blue", "weight": 2, "fillOpacity": 0.4, "opacity": 1.0},
        "geojson": {"fillColor": "blue", "color": "blue", "weight": 2, "fillOpacity": 0.4, "opacity": 1.0},
        "line": {"color": "blue", "weight": 3, "opacity": 1.0},
    }

    current_parameters = default_values.get(layer_type, {}).copy()
    current_parameters.update(current_kwargs)

    if data_field:
        current_parameters["data_field"] = data_field

    available_parameters = parameter_catalog.get(layer_type, parameter_catalog.get("geojson", {}))

    text = f"## Layer-Parameter\n\n"
    text += f"**Layer:** {layer_name}\n\n"
    text += f"**Typ:** `{layer_type}`\n\n"

    text += "### Aktuelle Parameter\n\n"
    for key, value in current_parameters.items():
        text += f"- `{key}`: `{value}`\n"

    text += "\n### Einstellbare Parameter\n\n"
    for key, description in available_parameters.items():
        text += f"- `{key}`: {description}\n"

    text += "\n### Beispielbefehle\n\n"

    if layer_type in ["polygon", "geojson", "choropleth"]:
        text += "- `Ändere die Füllfarbe auf blau`\n"
        text += "- `Setze die Transparenz auf 0.5`\n"
        text += "- `Mach die Randlinien dicker`\n"

    elif layer_type == "points":
        text += "- `Mach die Marker rot`\n"
        text += "- `Ändere das Symbol der Punkte`\n"

    elif layer_type == "heatmap":
        text += "- `Erhöhe den Radius der Heatmap`\n"
        text += "- `Reduziere die Unschärfe der Heatmap`\n"

    elif layer_type == "line":
        text += "- `Mach die Linien rot`\n"
        text += "- `Mach die Linien dicker`\n"

    return text

def verwalten_layers(user_text: str, layers):
    import re  # Ensure re is available inside the function or file top

    layer_names = "\n".join([l["name"] for l in layers])
    system_prompt = """
        Du bist ein GIS Layer Management Assistent.

        Du erhältst:
        - eine Liste aktueller Layer
        - eine Benutzeranweisung

        Deine Aufgabe:
        Erzeuge die neue finale Layer-Liste nach der Benutzeranweisung.

        REGELN:
        1. Antworte NUR im JSON-Format.
        2. Nutze KEINE Markdown-Formatierung wie ```json ... ```.
        3. Gib eine vollständige Liste der finalen Layer zurück.
        4. Jeder Layer muss eindeutig benannt sein.
        5. Wenn ein Layer gelöscht werden soll -> einfach NICHT in der Liste enthalten.
        6. Wenn alle Layer gelöscht werden sollen: gib eine leere Liste zurück UND setze "state": "empty".

        OUTPUT FORMAT:
        {
        "state": "normal | empty",
        "layers": [
            {
            "name": "<layer_name>",
            "source": "<original_name>"
            }
        ]
        }
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}, die Liste aktueller Layer:\n\n{layer_names}"}
    ])

    content = response.content.strip()
    
    try:
        # 🌟 核心修复：使用正则表达式强行抠出字符串中的 JSON 部分，过滤掉外层的 ```json 或废话
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            # 如果正则表达式没匹配到，尝试死马当活马医直接解析
            result = json.loads(content)
            
        if result["state"] == "empty":
            new_layers = []
        else:
            new_layers = result["layers"]
        return new_layers

    except Exception as e:
        # 🛡️ 极端情况下的降级策略：如果大模型彻底胡言乱语，返回原本的图层列表，防止程序卡死崩溃
        print(f"Error parsing layer management JSON: {e}. Raw content was: {content}")
        return [{"name": l["name"], "source": l["name"]} for l in layers]

def verstehen_Geodatei(user_text: str,layer):

    data = layer["data"]
    limit=10
    if isinstance(data, list):
        layer_data_sample = data[:limit]
    elif isinstance(data, dict):
        layer_data_sample = {}
        for i, (key, value) in enumerate(data.items()):
            if i >= limit:
                break
            layer_data_sample[key] = value
    else:
        layer_data_sample = data
    sample=json.dumps(layer_data_sample, ensure_ascii=False, indent=2)
    system_prompt = f"""
Du bist ein GIS-Datenanalyse-Assistent.

Du erhältst einen Ausschnitt einer Geodatendatei.

Deine Aufgaben:

1. Analysiere die Datenstruktur.
2. Erkläre die wichtigsten Attribute verständlich.
3. Fasse zusammen, welche Informationen der Datensatz enthält.
4. Empfiehl eine oder mehrere geeignete Kartentypen.

Die Anwendung unterstützt ausschließlich folgende Kartentypen:

{SUPPORTED_MAP_TYPES}

WICHTIGE REGELN FÜR EMPFEHLUNGEN:
- Wähle nur Kartentypen, die wirklich zur Geometrie und zu den Attributen passen
- heatmap nur bei Punktdaten mit sinnvollen numerischen Intensitätswerten
- choropleth nur bei Polygonen mit geeigneten numerischen Attributen pro Fläche
- Keine erfundenen oder angenommenen Felder verwenden
- Wenn kein passender Kartentyp existiert, empfehle keinen

Für jede Empfehlung erkläre kurz, warum dieser Kartentyp geeignet ist.

Falls mehrere Kartentypen sinnvoll sind, sortiere sie nach ihrer Eignung.
"""


    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}, die Beispieldatei:\n\n{sample}"}
    ])
    
    return response.content.strip()
# %%

# ==========================================
# 新增: 智能数据结构分析专家
# ==========================================
def smart_analyze_geodata(filename: str, sample_data_str: str) -> dict:  # 新增函数：智能分析地理数据结构
    """
    通过大模型盲审地理数据样本，自动识别最适合的图层类型、可用于制图的统计数值字段以及推荐的可读图层名称。
    """ # 新增函数：智能分析地理数据结构
    system_prompt = """
    Du bist ein erstklassiger GIS-Datenanalyst. Dir wird ein Auszug (Stichprobe) einer vom Nutzer hochgeladenen Geodatei bereitgestellt.
    Da die Nutzer Laien sind, ist es deine Aufgabe, diese Daten zu analysieren und zu entscheiden, wie sie am besten auf einer Karte visualisiert werden sollten.

    Analysiere folgendes:
    1. Geometrietyp: Befinden sich darin Punkte (mit lat/lon), Linien oder Polygone/MultiPolygone?
    2. Numerische Wertfelder (data_fields): Welche Felder eignen sich für thematische Karten wie Choropleth oder Heatmaps (z.B. Einwohnerzahlen, Preise, Schadstoffbelastung)? Ignoriere rein technische IDs, Indizes oder Koordinaten-Felder.
    3. Kreativer Layer-Name: Generiere einen kurzen, sprechenden, fehlerfreien deutschen Namen für diesen Layer, der beschreibt, was die Daten darstellen (nicht nur den Dateinamen wiederholen).

    GIB AUSSCHLIESSLICH EIN VALIDES JSON-OBJEKT IN FOLGENDEM FORMAT ZURÜCK (Kein Markdown, keine Erklärungen):
    {
      "detected_type": "points" | "line" | "polygon" | "geojson",
      "data_fields": ["feldname1", "feldname2"],
      "recommended_layer_name": "Ein schöner deutscher Layer-Name"
    }
    
    Regeln für die Klassifizierung:
    - Wenn Point-Geometrien oder 'lat'/'lon'-Schlüssel dominieren -> "points".
    - Wenn Polygon/MultiPolygon-Strukturen vorhanden sind -> "polygon".
    - Wenn LineString/MultiLineString vorhanden ist -> "line".
    - Wenn keine geeigneten numerischen Felder vorhanden sind, gib ein leeres Array [] zurück.
    """ 

    user_content = f"Dateiname: {filename}\nDaten-Stichprobe:\n{sample_data_str}" 

    try: 
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ])
        
        content = response.content.strip()
        # 稳健性处理：使用正则表达式剥离可能存在的 markdown 代码块 (```json ... ```)
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        else:
            raise ValueError("Ungültiges JSON vom LLM generiert.")
    except Exception as e:
        # 发生意外或超时时的降级兜底方案
        return {
            "detected_type": "geojson",
            "data_fields": [],
            "recommended_layer_name": filename
        }
# 🌟 核心修复：把原本在这里的 ｝ 删掉！Python 靠缩进结束函数，不需要花括号。

def output_response(State):

    tool_text = "\n".join(
    f"- {t['name']}: {t['description']}"
    for t in TOOLS)
    State_beschreibung="\n".join(
    f"- {s}"
    for s in State)

    letztbefehl=""
    if len(State)>0:
        letztbefehl=State[-1]

    system_prompt = f"""
Ein vollständiger Workflow zur Kartenerstellung ist wie folgt:

STEP 1: Der Nutzer lädt eine Datei hoch
STEP 2: Analyse der Dateistruktur (optional)
STEP 3: Erstellung der Karte
STEP 4: Anpassung des Kartenstils (optional)
STEP 5: Hinzufügen eines Kartentitels (optional)
STEP 6: Export der Karte (optional)
STEP 7: Verwaltung von Kartenlayern (optional)

--------------------------------
STATE-DEFINITION:
--------------------------------
- DATEI HOCHGELADEN = in HISTORY erscheint STEP 1 oder Dateiupload-Erfolg
- DATEI ANALYSIERT = STEP 2 wurde bereits ausgeführt
- KARTE ERSTELLT = STEP 3 wurde bereits ausgeführt

--------------------------------
STATE-BASED LOGIK:
--------------------------------

1. FALL: KEINE DATEI HOCHGELADEN
- Der Nutzer muss zuerst eine Datei hochladen
- Kein anderer Schritt darf empfohlen werden
- Keine weiteren Funktionen anzeigen

2. FALL: DATEI HOCHGELADEN, ABER KEINE KARTE ERSTELLT

- Ziel: Karte erstellen (STEP 3)

- Erlaubte Empfehlungen:

  a) STEP 3: Karte erstellen
     - immer empfehlen, wenn noch nicht ausgeführt

  b) STEP 2: Datei analysieren
     - nur empfehlen, wenn noch NICHT ausgeführt
     - optional zur besseren Datenverständnis

- Keine anderen Funktionen erlauben:
  (kein Kartenstil, kein Export, kein Titel, kein Layer)

3. FALL: KARTE BEREITS ERSTELLT

- Alle Funktionen sind verfügbar:

  - Kartenstil anpassen (STEP 4)
  - Kartentitel hinzufügen (STEP 5)
  - Karte exportieren (STEP 6)
  - Layer verwalten (STEP 7)

- Keine Begrenzung der Anzahl der Empfehlungen

--------------------------------
REGELN (SEHR WICHTIG):
--------------------------------

1. Entscheide zuerst den aktuellen STATE anhand von HISTORY und letzter Eingabe
2. Empfehlungen müssen strikt dem STATE folgen
3. Keine Funktionen aus zukünftigen Schritten vorzeitig empfehlen
4. Keine bereits ausgeführten Schritte erneut empfehlen
5. Keine erfundenen Funktionen oder Schritte
6. Antworte kurz, klar und nutzerfreundlich
7. Maximal relevante, nicht unnötig eingeschränkte Empfehlungen

--------------------------------
ERFOLGSFALL:
--------------------------------
Wenn ein Befehl erfolgreich ausgeführt wurde:

- Bestätigung der erfolgreichen Ausführung
- Danach nächste mögliche Schritte basierend auf STATE

Beispiel:
„<Befehl> wurde erfolgreich ausgeführt. Als Nächstes können Sie:
1. Funktion A
2. Funktion B“

--------------------------------
KEINE DATEI HOCHGELADEN:
--------------------------------
„Bitte laden Sie zuerst mindestens eine Datei hoch, damit eine Karte erstellt werden kann.“

--------------------------------
UNSUPPORTED FUNCTION:
--------------------------------
„Ihre Anfrage wird derzeit nicht unterstützt.
Aktuell verfügbare Funktionen sind:
{tool_text}“

--------------------------------
UNCLEAR INPUT:
--------------------------------
„Ihre Eingabe konnte nicht eindeutig verstanden werden. Meinten Sie möglicherweise:

1. Karte erstellen
2. Datei analysieren
3. Karte exportieren

Bitte wählen Sie eine Option oder formulieren Sie Ihre Anfrage genauer.“

--------------------------------
VERFÜGBARE FUNKTIONEN:
--------------------------------
{tool_text}

--------------------------------
HISTORY:
--------------------------------
{State_beschreibung}

--------------------------------
LETZTE BENUTZEREINGABE:
--------------------------------
{letztbefehl}

--------------------------------
AUFGABE:
--------------------------------
Erstelle eine kurze, natürliche und klare Antwort, die den Nutzer durch den Kartenworkflow führt.
"""
    
    response = llm.invoke([
        {"role": "system", "content": system_prompt},
    ])
    
    return response.content.strip()

def get_overview(user_text: str):
    """
    Erklärt die Anwendung, ihren Zweck und den typischen Arbeitsablauf für den Nutzer.
    """

    system_prompt = f"""
        Du bist ein GIS-Anwendungs-Assistent.

        Der Nutzer fragt nach den Fähigkeiten der Anwendung (z. B. "Was kannst du machen?").

        Deine Aufgabe:

        - Erkläre kurz und verständlich, was die Anwendung macht
        - Beschreibe den Ablauf der Anwendung in einer nutzerfreundlichen Struktur
        - Zeige nur aus Nutzerperspektive, keine internen Regeln oder States
        - Keine technischen Implementierungsdetails

        --------------------------------
        ERKLÄRUNG DER ANWENDUNG:
        --------------------------------

        Diese Anwendung hilft dabei, aus Geodatendateien Karten zu erstellen und zu bearbeiten.

        --------------------------------
        TYPISCHER ABLAUF:
        --------------------------------

        1. Daten einlesen
        - Der Nutzer lädt eine Geodatendatei hoch

        2. Analyse & Karten-Erstellung
        - Die Struktur der Datei kann analysiert werden (optional)
        - Eine Karte wird automatisch erstellt

        3. Anpassung & Nutzung
        - Kartenstil anpassen (optional)
        - Kartentitel hinzufügen (optional)
        - Karte exportieren (optional)
        - Kartenlayer verwalten (optional)

        --------------------------------
        WAS DER NUTZER DAMIT MACHEN KANN:
        --------------------------------

        - Geodaten visuell darstellen
        - Verschiedene Kartentypen nutzen
        - Karten individuell anpassen
        - Ergebnisse exportieren und weiterverwenden

        --------------------------------
        REGELN:
        --------------------------------

        - Antworte kurz, klar und nutzerfreundlich (max. 5–7 Sätze)
        - Keine internen Workflow-States erwähnen
        - Keine technischen oder systeminternen Details
        - Keine Auflistung von Tools oder Funktionen aus dem Backend
        - Fokus auf Nutzen für den Anwender
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_text}"}
    ])

    result = response.content.strip()
    return result


# ==========================================
#  BLOCK 6: perform_spatial_analysis 
# ==========================================
def perform_spatial_analysis(analysis_type: str, geo_input: any, params: dict, secondary_geo_input: any = None) -> dict:
    """
    Führt räumliche Analysen durch:
    - buffer
    - nearest
    - overlay_count
    - area
    - density_by_area
    """

    def extract_features(data_input):
        if not data_input:
            return []

        if isinstance(data_input, list):
            feats = []

            for p in data_input:
                if isinstance(p, dict) and "lat" in p and "lon" in p:
                    feats.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [float(p["lon"]), float(p["lat"])]
                        },
                        "properties": p.get("properties", p)
                    })

                elif isinstance(p, dict) and "geometry" in p:
                    feats.append(p)

            return feats

        elif isinstance(data_input, dict):
            if data_input.get("type") == "FeatureCollection":
                return data_input.get("features", [])

            elif data_input.get("type") == "Feature":
                return [data_input]

            elif "features" in data_input:
                return data_input["features"]

        return []

    def clean_for_geojson(gdf):
        """
        防止 pandas / numpy 类型导致 GeoJSON 序列化问题。
        """
        for col in gdf.columns:
            if col != "geometry":
                gdf[col] = gdf[col].apply(
                    lambda x: x.item() if hasattr(x, "item") else x
                )
        return gdf

    prim_features = extract_features(geo_input)

    if not prim_features:
        raise ValueError("Keine gültigen primären Geodaten gefunden.")

    gdf = gpd.GeoDataFrame.from_features(prim_features, crs="EPSG:4326")

    # =========================================================
    # 1. 面积计算：area_m2 / area_km2
    # =========================================================
    if analysis_type == "area":
        if not all(gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])):
            raise ValueError("Die Flächenberechnung ist nur für Polygon- oder MultiPolygon-Daten möglich.")

        # 德国建议用 EPSG:25832，单位是米，比 EPSG:4326 更适合算面积
        gdf_projected = gdf.to_crs(epsg=25832)

        gdf["area_m2"] = gdf_projected.geometry.area.round(2)
        gdf["area_km2"] = (gdf_projected.geometry.area / 1_000_000).round(4)

        gdf = clean_for_geojson(gdf)
        return json.loads(gdf.to_json())

    # =========================================================
    # 2. 面内点数量统计：objekt_anzahl
    # =========================================================
    elif analysis_type == "overlay_count" and secondary_geo_input is not None:
        sec_features = extract_features(secondary_geo_input)

        if not sec_features:
            raise ValueError("Keine gültigen sekundären Geodaten für die Überlagerung gefunden.")

        gdf_sec = gpd.GeoDataFrame.from_features(sec_features, crs="EPSG:4326")

        # primary 应该是面，secondary 通常是点
        if not all(gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])):
            raise ValueError("Overlay Count braucht als primären Layer Polygon-Daten.")

        gdf_3857 = gdf.to_crs(epsg=3857)
        gdf_sec_3857 = gdf_sec.to_crs(epsg=3857)

        joined = gpd.sjoin(
            gdf_sec_3857,
            gdf_3857,
            predicate="within",
            how="inner"
        )

        counts = joined.groupby("index_right").size()

        gdf["objekt_anzahl"] = gdf.index.map(counts).fillna(0).astype(int)

        gdf = clean_for_geojson(gdf)
        return json.loads(gdf.to_json())

    # =========================================================
    # 3. 点密度统计：objekt_anzahl / area_km2
    # =========================================================
    elif analysis_type == "density_by_area" and secondary_geo_input is not None:
        sec_features = extract_features(secondary_geo_input)

        if not sec_features:
            raise ValueError("Keine gültigen sekundären Geodaten für die Dichteanalyse gefunden.")

        gdf_sec = gpd.GeoDataFrame.from_features(sec_features, crs="EPSG:4326")

        if not all(gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])):
            raise ValueError("Dichteanalyse braucht als primären Layer Polygon-Daten.")

        # 面积
        gdf_projected = gdf.to_crs(epsg=25832)
        gdf["area_m2"] = gdf_projected.geometry.area.round(2)
        gdf["area_km2"] = (gdf_projected.geometry.area / 1_000_000).round(4)

        # 点数
        gdf_3857 = gdf.to_crs(epsg=3857)
        gdf_sec_3857 = gdf_sec.to_crs(epsg=3857)

        joined = gpd.sjoin(
            gdf_sec_3857,
            gdf_3857,
            predicate="within",
            how="inner"
        )

        counts = joined.groupby("index_right").size()
        gdf["objekt_anzahl"] = gdf.index.map(counts).fillna(0).astype(int)

        # 密度：每平方公里多少个点
        gdf["dichte_pro_km2"] = (
            gdf["objekt_anzahl"] / gdf["area_km2"].replace(0, np.nan)
        ).round(2)

        gdf["dichte_pro_km2"] = gdf["dichte_pro_km2"].fillna(0)

        gdf = clean_for_geojson(gdf)
        return json.loads(gdf.to_json())

    # =========================================================
    # 4. 最近邻距离
    # =========================================================
    elif analysis_type == "nearest" and secondary_geo_input is not None:
        sec_features = extract_features(secondary_geo_input)

        if not sec_features:
            raise ValueError("Keine gültigen sekundären Geodaten für den Abstandsvergleich gefunden.")

        gdf_sec = gpd.GeoDataFrame.from_features(sec_features, crs="EPSG:4326")

        gdf_3857 = gdf.to_crs(epsg=3857)
        gdf_sec_3857 = gdf_sec.to_crs(epsg=3857)

        distances = []

        for geom in gdf_3857.geometry:
            min_dist = gdf_sec_3857.distance(geom).min()
            distances.append(round(min_dist, 2))

        gdf["abstand_m"] = distances

        gdf = clean_for_geojson(gdf)
        return json.loads(gdf.to_json())

    # =========================================================
    # 5. Buffer
    # =========================================================
    elif analysis_type == "buffer":
        gdf_projected = gdf.to_crs(epsg=3857)
        distance = params.get("distance", 500)

        gdf_projected["geometry"] = gdf_projected.buffer(distance)

        gdf_result = gdf_projected.to_crs(epsg=4326)

        gdf_result = clean_for_geojson(gdf_result)
        return json.loads(gdf_result.to_json())

    return json.loads(gdf.to_json())

#让大模型理解用户具体想要“多大缓冲区”或“哪种分析”的解析函数
# ==========================================
# 升级版 BLOCK 6: parse_spatial_analysis_intent
# ==========================================
def parse_spatial_analysis_intent(user_text: str) -> dict:
    system_prompt = """
        Du bist ein KI-GIS-Experte. Klassifiziere den Analysewunsch des Nutzers.
        
        Mögliche analysis_type:
        - "buffer": Wenn der Nutzer einen Radius, Puffer, Umkreis oder Sicherheitszone erstellen will.
        - "nearest": Wenn der Nutzer den Abstand, die Entfernung oder das nächste Objekt zu einem anderen Layer wissen will.
        - "overlay_count": Wenn der Nutzer zählen möchte, wie viele Objekte/Punkte innerhalb von Polygonen liegen.
        - "area": Wenn der Nutzer die Fläche von Polygonen berechnen möchte.
        - "density_by_area": Wenn der Nutzer Punktdichte, Objekte pro km² oder Dichte pro Fläche berechnen möchte.

        Beispiele:
        - "Berechne die Fläche der Stadtteile" -> area
        - "Wie groß sind die Gebiete?" -> area
        - "Zähle die Punkte in jedem Stadtteil" -> overlay_count
        - "Wie viele Haltestellen liegen in jedem Bezirk?" -> overlay_count
        - "Berechne die Punktdichte pro km²" -> density_by_area
        - "Objekte pro Quadratkilometer" -> density_by_area

        Antworte REIN im folgenden JSON-Format:
        {
            "analysis_type": "buffer" | "nearest" | "overlay_count" | "area" | "density_by_area",
            "params": {"distance": 500}
        }
        """

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ])

    content = response.content.strip()
    json_match = re.search(r'(\{.*\})', content, re.DOTALL)

    if json_match:
        return json.loads(json_match.group(1))

    return {"analysis_type": "overlay_count", "params": {}}



# ==========================================
# ERWEITERUNG: AUTOMATISIERTE BERICHTSGENERIERUNG
# ==========================================
def generiere_daten_bericht(user_text: str, layer: dict) -> str: 
    """
    Experte für die intelligente, domänenübergreifende Erstellung von Datenberichten:
    Identifiziert automatisch geodatenbezogene Fachbereiche (Stadtplanung, Verkehrswesen, Meteorologie und Statistik) 
    und erstellt maßgeschneiderte, detaillierte Berichte mit dreiteiligem Aufbau.
    """
    import pandas as pd
    import json

    layer_name = layer.get("name", "Unbenannter Layer") 
    layer_type = layer.get("type", "geojson")
    data = layer.get("data", [])

    rows = []
    if layer_type == "points" and isinstance(data, list):
        for pt in data:
            row_dict = {"punkt_name": pt.get("name"), "lat": pt.get("lat"), "lon": pt.get("lon")}
            if "properties" in pt and isinstance(pt["properties"], dict): 
                row_dict.update(pt["properties"])
            rows.append(row_dict)
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        for feat in data.get("features", []):
            row_dict = feat.get("properties", {}).copy()
            geom = feat.get("geometry", {})
            if geom and geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2:
                    row_dict["lon"], row_dict["lat"] = coords[0], coords[1]
            rows.append(row_dict)
    
    if not rows:
        return f"### Datenbericht: {layer_name}\n\nKeine analysierbaren Daten gefunden."

    df = pd.DataFrame(rows)

    for col in df.columns: 
        if df[col].dtype == "object":
            try:
                converted = df[col].astype(str).str.replace(",", ".", regex=False)
                numeric_series = pd.to_numeric(converted, errors='coerce')
                if numeric_series.notna().sum() > (len(df) / 2):
                    df[col] = numeric_series
            except:
                pass

    # 修改后：把 stationsidentifikationsnummer 和 station 相关的词全踢掉
    exclude_cols = ["lat", "lon", "id", "objectid", "fid", "geometry", "stationsidentifikationsnummer", "station", "qn_3", "qn_4", "tag", "monat"]
    numeric_cols = [col for col in df.select_dtypes(include=["number"]).columns if col.lower() not in exclude_cols]
    text_cols = [col for col in df.select_dtypes(include=["object", "category"]).columns if col.lower() not in exclude_cols and col.lower() != "quartal"]

    stats_summary = {}
    if numeric_cols:
        desc = df[numeric_cols].describe()
        for col in numeric_cols:
            if desc.loc["count", col] > 0:
                stats_summary[col] = {
                    "Anzahl_Werte": int(desc.loc["count", col]),
                    "Mittelwert": round(float(desc.loc["mean", col]), 2),
                    "Min": round(float(desc.loc["min", col]), 2),
                    "Max": round(float(desc.loc["max", col]), 2)
                }

    classification_summary = {}
    for col in text_cols[:3]:
        top_values = df[col].value_counts().head(3).to_dict()
        classification_summary[col] = top_values

    date_info = "Keine explizite Zeitreihe"
    for date_col in ["Datum", "datum", "Jahr", "jahr", "zeit"]:
        if date_col in df.columns:
            date_info = f"Zeitraum: {df[date_col].min()} bis {df[date_col].max()}"
            break

    meta_info = {
        "layer_name": layer_name,
        "gesamt_zeilen": len(df),
        "zeitraum_info": date_info,
        "numerische_statistiken": stats_summary,
        "kategorische_klassifikation": classification_summary,
        "alle_verfuegbaren_spalten": list(df.columns)
    }

    # ====================================================================
    # 优化扩展后的 SYSTEM PROMPT：深度强化第三部分的AI智能分析
    # ====================================================================
    system_prompt = """
    Du bist ein präziser Geodaten-Analyst und Fachgutachter. Deine Aufgabe ist es, einen kompakten, tiefgründigen und fehlerfreien Datenbericht auf DEUTSCH zu erstellen. Vermeide jegliche Floskeln, Einleitungen ("Hier ist der Bericht...") oder Ratschläge zur Kartendarstellung.

    Der Bericht MUSS exakt dieser dreiteiligen Struktur folgen (Nutze Markdown):

    ## Datenbericht: [Layer-Name]

    ### 1. Geografische Lage & Kurzbeschreibung
    - Beschreibe in 1-2 Sätzen den geografischen Raum (z. B. Dresden) und den Kernbereich der Daten (z. B. Flächennutzungsplanung, Verkehrsunfallstatistik, Hochwasservorsorge).
    - Nenne die Gesamtzahl der enthaltenen Datensätze (Zeilen).

    ### 2. Kernstatistiken & Zeitraum
    - Nenne den zeitlichen Rahmen der Daten (falls vorhanden).
    - Liste die wichtigsten numerischen Kennzahlen (Mittelwert, Maximum, Minimum) übersichtlich auf. Falls Variablen (wie 'korr_nr', 'id') administrative Kennungen oder Planungsnummern statt physikalischer Messwerte sind, weise explizit darauf hin.
    - Falls vorhanden, nenne die dominanten Kategorien (z. B. die häufigsten Flächennutzungsarten oder Schadensklassen).

    ### 3. Themeneinzelfall-Zusammenfassung 
    Leite eine fundierte, 1-2 Absätze lange fachspezifische Schlussfolgerung ab. **WICHTIG:** Du darfst die reinen Zahlen aus Teil 2 nicht nur stumpf wiederholen, sondern musst sie **interpretieren**. Setze die statistischen Extremwerte (Min/Max) und den Mittelwert in einen logischen Ursache-Wirkungs-Zusammenhang basierend auf dem jeweiligen Fachthema:

    - **Bei Raum- und Flächennutzungsplanung:** Diskutiere, was das Verhältnis von minimalen zu maximalen Flächengrößen für die administrative Struktur bedeutet. Deuten extrem große Zonen (Max-Wert) auf großflächige Industrie- oder Landschaftsschutzgebiete hin, während der Mittelwert eine kleinteilige urbane Parzellierung zeigt? Analysiere die raumplanerische Logik dieser Verteilung.
    - **Bei Verkehrs- und Unfalldaten:** Setze die statistischen Kennzahlen in Relation zur Verkehrssicherheit. Wenn beispielsweise der Maximalwert an Verletzten pro Unfall hoch ist oder die Schadensklassen ein starkes Ungleichgewicht zeigen, analysiere, ob es sich um punktuelle Gefahrenstellen (Hotspots) handelt oder ob ein generelles systemisches Risiko im Straßennetz vorliegt. Interpretiere saisonale Peaks oder quartalsweise Ausreißer im Hinblick auf städtische Mobilitätsmuster.
    - **Bei Umwelt-, Wetter- und Hochwasservorsorge:** Setze physikalische Messwerte (z.B. Niederschlag, Pegelstände, Schadstoffwerte) in einen ökologischen Kontext. Was bedeutet das Überschreiten eines bestimmten Schwellenwerts (Max-Wert) im Vergleich zum Mittelwert für das Gefahrenpotenzial? Diskutiere kritische Defizite im Schutzgrad und die resultierende Verwundbarkeit städtischer oder natürlicher Infrastrukturen.
    - **Bei allgemeinen sozioökonomischen Statistiken:** Analysiere die datenstrukturelle Homogenität. Zeigt die Standardabweichung (implizit durch die Spanne von Min zu Max) eine extreme Schere bzw. Disparität zwischen verschiedenen Stadtteilen oder Messpunkten? Identifiziere administrative oder gesellschaftliche Ausreißer und deren potenzielle Ursachen.
    """

    user_content = f"Zusätzliche Nutzer-Anforderung: {user_text}\n\nHier sind die Statistik-Metadaten:\n{json.dumps(meta_info, ensure_ascii=False, indent=2)}"

    try:
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ])
        return response.content.strip()
    except Exception as e:
        return f"## Kompakter Datenbericht: {layer_name}\n\n- **Gesamtzeilen:** {len(df)}"
    
    

# ====================================================================
# 新增 BLOCK 9: MACHINE LEARNING SPATIAL PREDICTION & TREND ANALYSIS
# ====================================================================
# type: ignore
from sklearn.linear_model import LinearRegression # type: ignore
from sklearn.ensemble import RandomForestRegressor # type: ignore
import numpy as np

def perform_ml_prediction(prediction_type: str, layer: dict, params: dict) -> dict:
    """
    Ausführungsknoten für fortgeschrittenes maschinelles Lernen: Unterstützt prädiktive Prognosen mittels Zeittrend-Extrapolation („trend“) 
    sowie räumliche Interpolation unter Verwendung koordinatenbasierter Random-Forest-Modelle („spatial_prediction“).
    """
    import pandas as pd
    
    layer_type = layer.get("type", "geojson")
    data = layer.get("data", [])
    
    # 1. 统一提取要素属性到 Pandas DataFrame
    rows = []
    if layer_type == "points" and isinstance(data, list):
        for pt in data:
            row_dict = {"lat": pt.get("lat"), "lon": pt.get("lon")}
            if "properties" in pt: row_dict.update(pt["properties"])
            rows.append(row_dict)
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        for feat in data.get("features", []):
            row_dict = feat.get("properties", {}).copy()
            geom = feat.get("geometry", {})
            if geom and geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2: 
                    row_dict["lon"], row_dict["lat"] = coords[0], coords[1]
            rows.append(row_dict)

    if not rows:
        raise ValueError("Keine analysierbaren Daten für Machine Learning gefunden.")
        
    df = pd.DataFrame(rows)
    
    # 清洗德语系统常见的逗号小数点
    for col in df.columns:
        if df[col].dtype == "object":
            try: 
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "."), errors='coerce')
            except: 
                pass

    # 智能锁定目标因变量（Target Field）
    target_field = params.get("target_field")
    if not target_field or target_field not in df.columns:
        exclude_cols = ["lat", "lon", "id", "objectid", "fid", "geometry"]
        num_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c.lower() not in exclude_cols]
        if not num_cols: 
            raise ValueError("Keine geeignete numerische Zielvariable für ML-Modellierung gefunden.")
        target_field = num_cols[0]

    df_clean = df.dropna(subset=[target_field])

    # ======= 路由一: 机器学习时间趋势分析 =======
    if prediction_type == "trend":
        time_col = next((col for col in df_clean.columns if col.lower() in ["jahr", "jahr", "datum", "zeit", "quartal"]), None)
        if not time_col:
            raise ValueError("Für eine Trendanalyse wird eine zeitliche Spalte (z.B. 'jahr' oder 'datum') benötigt.")
        
        X = df_clean[[time_col]].values
        y = df_clean[target_field].values
        
        model = LinearRegression()
        model.fit(X, y)
        
        future_steps = int(params.get("future_steps", 2))
        max_time = int(X.max())
        future_X = np.array([[max_time + i] for i in range(1, future_steps + 1)])
        future_preds = model.predict(future_X)
        
        trend_direction = "steigend" if model.coef_[0] > 0 else "sinkend"
        
        return {
            "type": "trend_result",
            "target_field": target_field,
            "time_field": time_col,
            "coef": float(model.coef_[0]),
            "trend_direction": trend_direction,
            "future_years": future_X.flatten().tolist(),
            "predictions": np.round(future_preds, 2).tolist()
        }

    # ======= 路由二: 空间格局预测 =======
    elif prediction_type == "spatial_prediction":
        if "lat" not in df_clean.columns or "lon" not in df_clean.columns:
            raise ValueError("Für räumliche Vorhersagen werden Geokoordinaten (lat/lon) benötigt.")
            
        X = df_clean[["lon", "lat"]].values
        y = df_clean[target_field].values
        
        model = RandomForestRegressor(n_estimators=100, random_state=42)
        model.fit(X, y)
        
        df_clean["ml_predicted"] = np.round(model.predict(X), 2)
        df_clean["ml_residual"] = np.round(df_clean[target_field] - df_clean["ml_predicted"], 2)
        
        # 重新打包为标准 GeoJSON，并在属性里做防御性清洗
        features = []
        for _, row in df_clean.iterrows():
            clean_properties = {}
            raw_props = {
                "original_wert": float(row[target_field]),
                "vorhersage_wert": float(row["ml_predicted"]),
                "residuat_abweichung": float(row["ml_residual"]),
                "ziel_variable": target_field
            }
            
            for k, v in row.to_dict().items():  # 显式转为字典遍历
                if k not in ["lon", "lat"]:
                    # 关键补丁：处理各种千奇百怪的 set 变种
                    if isinstance(v, (set, frozenset)):
                        clean_properties[k] = list(v)
                    elif pd.isna(v):  # 处理空值
                        clean_properties[k] = None
                    elif hasattr(v, 'item'):  # 处理 numpy 类型
                        clean_properties[k] = v.item()
                    else:
                        clean_properties[k] = v
                        
            clean_properties.update(raw_props)

            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                "properties": clean_properties
            })
            
        return {
            "type": "FeatureCollection",
            "features": features
        }

def parse_ml_intent(user_text: str) -> dict:
    system_prompt = """
        Du bist ein Machine Learning GIS-Experte. Klassifiziere den Prädiktionswunsch des Nutzers.
        Mögliche prediction_type:
        - "trend": Wenn der Nutzer zukünftige Entwicklungen, Trends oder zeitliche Verläufe schätzen will.
        - "spatial_prediction": Wenn der Nutzer Werte im Raum interpolieren oder vorhersagen möchte.
        Antworte REIN im folgenden JSON-Format:
        {
            "prediction_type": "trend" | "spatial_prediction",
            "params": {"target_field": null, "future_steps": 2}
        }
        """
    response = llm.invoke([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}])
    import re
    content = response.content.strip()
    json_match = re.search(r'(\{.*\})', content, re.DOTALL)
    if json_match: 
        return json.loads(json_match.group(1))
    return {"prediction_type": "trend", "params": {}}


