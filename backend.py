# ==============================================================================
# backend.py - Fully Autonomous ReAct GIS Agent Backend
# ==============================================================================
import os
import json
import copy
import urllib.parse
import requests
from pathlib import Path
from typing import TypedDict, Sequence, Annotated, Optional, List, Dict, Any
from shapely.ops import nearest_points

from dotenv import load_dotenv
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, shape, mapping
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor

from geopy.geocoders import Nominatim

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from gis.data_io import extract_features_to_gdf, parse_uploaded_file
from gis import data_io as gis_data_io
from gis.geometry import buffer_geometries, pairwise_distances
from gis import statistics as gis_statistics
from maps.renderer import (
    DEFAULT_MAP_STYLE,
    create_layered_map,
    add_points_layer,
    add_heatmap_layer,
    add_choropleth_layer,
    add_geojson_layer,
    Titel_hinzufügen,
    render_map_html,
)

# ==========================================
# 1. 基础环境与大模型初始化
# ==========================================
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
    timeout=60,
    max_retries=2
)

# ==========================================
# 2. Agent 共享状态与数据通道 (Global State Bus)
# ==========================================
# 使用线程级别的全局上下文，让 Agent 执行的 Tool 可以精准访问和写入当前前端的图层池
_SHARED_LAYER_BUS: Dict[str, Any] = {
    "raw_layers": [],
    "show_layers": []
}

def set_agent_context(raw_layers: List[dict], show_layers: List[dict]):
    """由前端调用，将当前活跃的图层指针挂载到工具环境"""
    _SHARED_LAYER_BUS["raw_layers"] = raw_layers
    _SHARED_LAYER_BUS["show_layers"] = show_layers

def get_agent_layers():
    return _SHARED_LAYER_BUS["raw_layers"], _SHARED_LAYER_BUS["show_layers"]

class AgentState(TypedDict):
    """LangGraph 状态定义：自动利用 add_messages 维护 ReAct 循环中 Message 的历史状态"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    updated_show_layers: Optional[List[dict]]


# ==========================================
# 3. 辅助计算工具函数
# ==========================================
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


# ==========================================
# 4. Agent 核心原子工具集 (@tool)
# ==========================================

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
    data_field: Numerisches Feld für thematische Karten (z.B. für choropleth oder heatmap).
    """
    raw_layers, show_layers = get_agent_layers()
    all_available = show_layers + raw_layers
    target = next((l for l in all_available if l["name"].lower() == layer_name.lower()), None)
    if not target:
        available_names = [l["name"] for l in all_available]
        return f"Layer '{layer_name}' nicht gefunden. Verfügbar sind: {available_names}"

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
        "data": target["data"],
        "data_field": data_field,
        "folium_kwargs": folium_kwargs,
        "style": folium_kwargs
    })
    return f"Erfolg: Layer '{target['name']}' wurde als '{map_type}' unter dem Namen '{new_map_layer_name}' zur Karte hinzugefügt."


@tool
def calculate_buffer_analysis(layer_name: str, distance_meters: float = 500.0) -> str:
    """
    Berechnet eine Pufferzone (Buffer) um Geometrien eines Layers mit angegebener Distanz in Metern.
    """
    raw_layers, show_layers = get_agent_layers()
    pool = show_layers if show_layers else raw_layers
    target = next((l for l in pool if l["name"].lower() == layer_name.lower()), None)
    if not target:
        return f"Layer '{layer_name}' nicht gefunden."
    
    try:
        gdf = extract_features_to_gdf(target["data"])
        res_gdf = buffer_geometries(gdf, distance_meters)
        res_gdf = clean_gdf_for_geojson(res_gdf)
        
        new_layer_name = make_unique_layer_name(f"Puffer_{int(distance_meters)}m_{target['name']}", show_layers)
        show_layers.append({
            "name": new_layer_name,
            "type": "polygon",
            "data": json.loads(res_gdf.to_json()),
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
    poly_layer = next((l for l in pool if l["name"].lower() == polygon_layer_name.lower()), None)
    if not poly_layer:
        return f"Polygon-Layer '{polygon_layer_name}' nicht gefunden."
    
    try:
        gdf_poly = extract_features_to_gdf(poly_layer["data"])
        gdf_poly = gis_statistics.calculate_area(gdf_poly)
        
        target_field = "area_km2"
        layer_suffix = "Flaeche"
        
        if point_layer_name:
            pt_layer = next((l for l in pool if l["name"].lower() == point_layer_name.lower()), None)
            if pt_layer:
                gdf_pt = extract_features_to_gdf(pt_layer["data"])
                gdf_poly = gis_statistics.calculate_density(gdf_poly, gdf_pt)
                target_field = "dichte_pro_km2"
                layer_suffix = "Dichte"

        res_gdf = clean_gdf_for_geojson(gdf_poly)
        res_json = json.loads(res_gdf.to_json())
        new_layer_name = make_unique_layer_name(f"{layer_suffix}_{poly_layer['name']}", show_layers)
        
        show_layers.append({
            "name": new_layer_name,
            "type": "choropleth",
            "data": res_json,
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
    target = next((l for l in pool if l["name"].lower() == layer_name.lower()), None)
    if not target:
        return f"Layer '{layer_name}' nicht gefunden."
    
    try:
        gdf = extract_features_to_gdf(target["data"])
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
        
        new_layer_name = make_unique_layer_name(f"ML_Vorhersage_{target['name']}", show_layers)
        show_layers.append({
            "name": new_layer_name,
            "type": "points",
            "data": json.loads(clean_gdf.to_json())["features"],
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
    target = next((l for l in show_layers if l["name"].lower() == layer_name.lower()), None)
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
    target = next((l for l in pool if l["name"].lower() == layer_name.lower()), None)
    if not target:
        available = [l["name"] for l in pool]
        return f"Layer '{layer_name}' wurde nicht gefunden. Verfügbar sind: {available}"

    try:
        # 1. 转换为 GeoDataFrame
        gdf = extract_features_to_gdf(target["data"])
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

# 重新绑定工具（保持原有代码不变）
llm_with_tools = llm.bind_tools(TOOLS)

# ==========================================
# 5. LangGraph Agent Graph (ReAct Pattern)
# ==========================================
llm_with_tools = llm.bind_tools(TOOLS)

def agent_reasoner(state: AgentState) -> dict:
    """Reason 阶段：由 LLM 结合长上下文自主决定直接回答或调用工具"""
    raw_layers, show_layers = get_agent_layers()
    raw_summary = [f"'{l['name']}' ({l['type']})" for l in raw_layers]
    show_summary = [f"'{l['name']}' ({l['type']})" for l in show_layers]
    
    system_prompt = f"""
    Du bist GeoGPT, ein autonomer und professioneller GIS-Agent.
    Deine Aufgabe ist es, Nutzerbefehle über räumliche Analysen und Kartenerstellung selbstständig auszuführen.
    
    AKTUELLER ZUSTAND DER ANWENDUNG:
    - Hochgeladene Rohdaten-Layer: [{', '.join(raw_summary) if raw_summary else 'Keine'}]
    - Derzeit auf der Karte angezeigte Layer: [{', '.join(show_summary) if show_summary else 'Keine'}]
    
    HANDLUNGSLEITFADEN:
    1. Du darfst und sollst Werkzeuge (Tools) verwenden, um Aktionen durchzuführen.
    2. Wenn ein Befehl mehrere Schritte erfordert (z.B. 'Lade Stadt Dresden und lege einen 1000m Puffer an'), führe die Werkzeuge nacheinander aus.
    3. Gib dem Nutzer nach Beendigung aller Tool-Aufrufe eine prägnante, informative Zusammenfassung auf Deutsch.
    """
    
    # 过滤掉系统消息，在首位统一拼装最新的系统认知
    user_and_assistant_msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
    full_messages = [SystemMessage(content=system_prompt)] + list(user_and_assistant_msgs)
    
    response = llm_with_tools.invoke(full_messages)
    return {"messages": [response]}

def should_continue(state: AgentState):
    """条件路由：判断 LLM 决定继续执行工具调用，还是已经结束推理给出最终答案"""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END

# 构建状态机
workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_reasoner)
workflow.add_node("tools", ToolNode(TOOLS))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")  # ReAct 闭环：观察工具执行结果后再回到 Agent 评估

agent_app = workflow.compile()


# ==========================================
# 7. 数据解析与智能上传分析
# ==========================================
def smart_analyze_geodata(filename: str, sample_data_str: str) -> dict:
    system_prompt = """
    Analysiere diesen Geodaten-Auszug und antworte NUR mit JSON:
    {
      "detected_type": "points" | "line" | "polygon" | "geojson",
      "data_fields": ["feld1", "feld2"],
      "recommended_layer_name": "Ein schöner deutscher Name"
    }
    """
    try:
        resp = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=f"Datei: {filename}\nAuszug:\n{sample_data_str}")])
        import re
        match = re.search(r'(\{.*\})', resp.content.strip(), re.DOTALL)
        return json.loads(match.group(1)) if match else {"detected_type": "geojson", "data_fields": [], "recommended_layer_name": filename}
    except Exception:
        return {"detected_type": "geojson", "data_fields": [], "recommended_layer_name": filename}
