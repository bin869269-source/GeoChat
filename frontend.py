# %%
# ==========================================
# BLOCK 1: FRONTEND INITIALIZATION & SESSION MANAGEMENT
# ==========================================
import streamlit as st
import folium
import json
import copy
from streamlit_folium import st_folium
from backend import agent_app
from backend import parse_uploaded_file
from backend import create_layered_map
from backend import DEFAULT_MAP_STYLE
from backend import is_polygon_geojson
from backend import is_line_geojson
from backend import get_numeric_geojson_fields
from backend import Titel_hinzufügen
from backend import html_to_png
from backend import png_to_pdf
from backend import smart_analyze_geodata
from backend import parse_spatial_analysis_intent, perform_spatial_analysis
from backend import verstehen_Geodatei, generiere_daten_bericht
from backend import parse_ml_intent, perform_ml_prediction, convert_geojson_points_to_points

st.set_page_config(layout="wide", page_title="GeoGPT Intelligent Agent")
st.markdown("##  Intelligenter Chatbot für Kartenerstellung")

if "messages" not in st.session_state: st.session_state.messages = []
if "current_geojson" not in st.session_state: st.session_state.current_geojson = None
if "layers" not in st.session_state: st.session_state.layers = []
if "processed_files" not in st.session_state: st.session_state.processed_files = set()
if "map_style" not in st.session_state: st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
if "view_updates" not in st.session_state: st.session_state.view_updates = {}
#  新增地图渲染键，用于物理强制刷新视图组件
if "map_key" not in st.session_state: st.session_state.map_key = 0
if "available_choropleth_fields" not in st.session_state:st.session_state.available_choropleth_fields = []
#  控制导出地图
if "show_export_buttons" not in st.session_state: st.session_state.show_export_buttons = False
if "export_files" not in st.session_state: st.session_state.export_files = {}
#  控制标题
if "Karte_Titel" not in st.session_state: st.session_state.Karte_Titel = ""

#  控制要展示的图层
if "show_layers" not in st.session_state: st.session_state.show_layers = []

#  控制要展示的图层
if "workflow_state" not in st.session_state: st.session_state.workflow_state = []

#  底图展示
if "show_basemap" not in st.session_state: st.session_state.show_basemap = True

# %%
# ==========================================
# BLOCK 2: SIDEBAR CONTROL PANEL
# ==========================================
with st.sidebar:
    st.markdown("### Kartenverwaltung")
    st.markdown("---")
    with st.expander("Hochgeladene Rohdaten", expanded=True):
        if st.session_state.layers:
            for i, layer in enumerate(st.session_state.layers):
                col_name, col_delete = st.columns([4, 1])

                with col_name:
                    st.write(f"{i + 1}. {layer['name']} ({layer['type']})")

                with col_delete:
                    if st.button("✕", key=f"delete_raw_layer_{i}"):
                        deleted_file_id = layer.get("file_id", layer["name"])
                        st.session_state.layers.pop(i)
                        st.session_state.processed_files.discard(deleted_file_id)
                        st.session_state.map_key += 1
                        st.rerun()
        else:
            st.write("Noch keine Datei hochgeladen.")


    with st.expander("Erzeugte Kartenlayer", expanded=True):
        if st.session_state.show_layers:
            for i, layer in enumerate(st.session_state.show_layers):
                col_name, col_delete = st.columns([4, 1])

                with col_name:
                    st.write(f"{i + 1}. {layer['name']} ({layer['type']})")

                with col_delete:
                    if st.button("✕", key=f"delete_map_layer_{i}"):
                        st.session_state.show_layers.pop(i)
                        st.session_state.map_key += 1
                        st.rerun()
        else:
            st.write("Noch keine Kartenlayer erzeugt.")

    st.markdown("---")

    export_type = st.selectbox(
    "Dateityp auswählen:",
    ["HTML", "PNG", "PDF"]
)

    if "export_ready" not in st.session_state:
        st.session_state.export_ready = False
    if "export_file_path" not in st.session_state:
        st.session_state.export_file_path = None
    if "export_mime" not in st.session_state:
        st.session_state.export_mime = None
    if "export_filename" not in st.session_state:
        st.session_state.export_filename = None

    if st.button("Karte exportieren"):

        m_export = create_layered_map(
            st.session_state.show_layers,
            st.session_state.view_updates,
            st.session_state.show_basemap
        )

        if st.session_state.Karte_Titel:
            Titel_hinzufügen(m_export, st.session_state.Karte_Titel)

        if export_type == "HTML":
            html_path = "map.html"
            m_export.save(html_path)

            st.session_state.export_file_path = html_path
            st.session_state.export_mime = "text/html"
            st.session_state.export_filename = "map.html"

        elif export_type == "PNG":
            html_path = "map_temp.html"
            png_path = "map.png"

            m_export.save(html_path)
            html_to_png(html_path, png_path)

            st.session_state.export_file_path = png_path
            st.session_state.export_mime = "image/png"
            st.session_state.export_filename = "map.png"

        elif export_type == "PDF":
            html_path = "map_temp.html"
            png_path = "map_temp.png"
            pdf_path = "map.pdf"

            m_export.save(html_path)
            html_to_png(html_path, png_path)
            png_to_pdf(png_path, pdf_path)

            st.session_state.export_file_path = pdf_path
            st.session_state.export_mime = "application/pdf"
            st.session_state.export_filename = "map.pdf"

        st.session_state.export_ready = True

    if st.session_state.export_ready:
        with open(st.session_state.export_file_path, "rb") as f:
            st.download_button(
                f"{st.session_state.export_filename} herunterladen",
                f.read(),
                st.session_state.export_filename,
                st.session_state.export_mime
            )

    st.markdown("---")

    if st.button("Chat & Layer löschen"):
        st.session_state.messages = []
        st.session_state.current_geojson = None
        st.session_state.layers = []
        st.session_state.processed_files = set()
        st.session_state.map_style = DEFAULT_MAP_STYLE.copy()
        st.session_state.view_updates = {}
        st.session_state.available_choropleth_fields = []
        st.session_state.map_key += 1
        st.session_state.workflow_state = []
        st.session_state.show_layers = []
        st.session_state.Karte_Titel = ""
        st.session_state.show_export_buttons = False
        st.session_state.export_files = {}
        st.rerun()

    st.session_state.show_basemap = st.checkbox(
    "OpenStreetMap anzeigen",
    value=st.session_state.show_basemap
    )

# %%
# ==========================================
# BLOCK 3: LAYOUT SYSTEM (COLUMNS)
# ==========================================
col1, col2 = st.columns([1, 1.5])

with col1:
    st.subheader("Daten & Chat")
    
    # 🌟 核心改进 1：添加 accept_multiple_files=True，允许用户多选文件
    uploaded_files = st.file_uploader(
        "Bitte laden Sie Ihre Daten hoch",
        type=["json", "csv", "geojson"],
        accept_multiple_files=True  # 允许选择多个文件
    )

    # 🌟 核心改进 2：通过循环逐个处理用户上传的所有文件
    if uploaded_files:  # 如果列表不为空
        for uploaded_file in uploaded_files:
            file_id = uploaded_file.name

            # 检查当前文件是否已经处理过，避免重复触发大模型
            if file_id not in st.session_state.processed_files:
                # 为每个文件单独展示独立的加载状态
                with st.spinner(f" GeoGPT analysiert: {file_id}..."):
                    try:
                        # 1. 基础解析
                        parsed_data = parse_uploaded_file(uploaded_file)
                        raw_data = parsed_data["data"]

                        # 2. 智能化数据切片
                        if isinstance(raw_data, list):
                            sample_str = json.dumps(raw_data[:3], ensure_ascii=False)
                        elif isinstance(raw_data, dict) and "features" in raw_data:
                            sample_features = raw_data.get("features", [])[:2]
                            sample_str = json.dumps({"type": "FeatureCollection", "features": sample_features}, ensure_ascii=False)
                        else:
                            sample_str = json.dumps(raw_data, ensure_ascii=False)[:1000]

                        # 3. 调用 LLM 进行智能化盲审分析
                        llm_analysis = smart_analyze_geodata(uploaded_file.name, sample_str)
                        
                        detected_type = llm_analysis.get("detected_type", parsed_data["type"])
                        detected_fields = llm_analysis.get("data_fields", [])
                        recommended_name = llm_analysis.get("recommended_layer_name", uploaded_file.name)

                        # 4. 组装图层对象
                        current_layer = {
                            "name": recommended_name,
                            "type": detected_type,
                            "data": raw_data,
                            "style": copy.deepcopy(st.session_state.map_style),
                            "data_fields": detected_fields
                        }

                        # 5. 更新全局状态
                        st.session_state.layers.append(current_layer)
                        
                        if detected_type == "polygon" and detected_fields:
                            st.session_state.available_choropleth_fields = detected_fields
                            st.session_state.latest_choropleth_source_layer = len(st.session_state.layers) - 1
                        elif detected_type == "points" and detected_fields:
                            st.session_state.available_heatmap_fields = detected_fields
                            st.session_state.latest_heatmap_source_layer = len(st.session_state.layers) - 1

                        st.session_state.processed_files.add(file_id)
                        st.session_state.map_key += 1  # 物理刷新地图组件
                        
                        st.success(f" Erfolgreich! Layer hinzugefügt: **{recommended_name}** ({detected_type})")
                        st.session_state.workflow_state.append(f"Dateiname: {file_id}, Datei hochladen, success")

                    except Exception as e:
                        st.error(f"Fehler bei der intelligenten Analyse von {file_id}: {e}")



    st.markdown("---")
    st.markdown("### Chatverlauf")

    chat_container = st.container(height=375)
    with chat_container:
        for msg in st.session_state.messages:
            st.chat_message(msg["role"]).write(msg["content"])

with col2:
    st.subheader("Interaktive Karte")
    m = create_layered_map(
    st.session_state.show_layers,
    st.session_state.view_updates,
    st.session_state.show_basemap
    )


    #zheng模拟添加标题
    if st.session_state.Karte_Titel:
        Titel_hinzufügen(m,st.session_state.Karte_Titel)

    st_folium(m, width="100%", height=610, key=f"map_instance_{st.session_state.map_key}")

    # %%
    # ==========================================
    # zheng保存地图
    # ==========================================

# %%
# ==========================================
# BLOCK 4: UNIFIED INTELLIGENT INPUT GATEWAY
# ==========================================
from backend import parse_intent
from backend import get_selected_layer_name
from backend import get_selected_layer_names
from backend import bestimmen_Kartentyp
from backend import set_selected_layer_name
from backend import get_multi_layer_styles
from backend import describe_layer_parameters
from backend import get_map_style
from backend import verwalten_layers
from backend import verstehen_Geodatei
from backend import smart_analyze_geodata
from backend import output_response
from backend import get_overview

#Sorgt dafür, dass jeder Kartenlayer einen eindeutigen Namen hat.
def make_unique_layer_name(base_name, existing_layers):
    existing_names = [layer["name"] for layer in existing_layers]

    if base_name not in existing_names:
        return base_name

    counter = 2
    new_name = f"{base_name} ({counter})"

    while new_name in existing_names:
        counter += 1
        new_name = f"{base_name} ({counter})"

    return new_name

user_query = st.chat_input("Was möchtest du mit der Karte machen?")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})

    with st.spinner("GeoGPT analysiert und führt Befehl aus..."):
        #initiieren
        st.session_state.show_export_buttons = False
        st.session_state.export_files = {}
        output="" 
        
        result = parse_intent(user_query)

        layers=st.session_state.layers
        show_layers=st.session_state.show_layers
        
           
        if result["tool"] == "create_map":
                if len(layers)>0:
                    selected_layer_names = get_selected_layer_names(user_query, layers)

                for selected_layer_name in selected_layer_names:

                    layer = next(
                        (l for l in layers if l["name"] == selected_layer_name),
                        None
                    )

                    if layer is None:
                        continue

                    kartentype = bestimmen_Kartentyp(user_query, layer)

                    new_layer_name = kartentype.get(
                        "layer_name",
                        f"{layer['name']}_{kartentype['type']}"
                    )

                    new_layer_name = make_unique_layer_name(
                        new_layer_name,
                        st.session_state.show_layers
                    )

                    llm_kwargs = kartentype.get("folium_kwargs", {})

                    st.session_state.show_layers.append({
                        "name": new_layer_name,
                        "type": kartentype["type"],
                        "data": layer["data"],
                        "data_field": kartentype.get("data_field"),
                        "folium_kwargs": llm_kwargs,
                        "style": {
                            "color": llm_kwargs.get("color", "blue"),
                            "weight": llm_kwargs.get("weight", 2),
                            "fillColor": llm_kwargs.get("fillColor", "blue"),
                            "fillOpacity": llm_kwargs.get("fillOpacity", 0.4)
                        }
                    })
                    
                    # 3. 🌟 完美的交互报告：在聊天框里向导师直观展示“LLM 生成的代码参数计划”
                    output_text = f" **LLM Deklarativer Ausführungsplan**\n\n"
                    output_text += f"**Entscheidung:** `{kartentype['type'].upper()}`-Layer für **{new_layer_name}**\n"
                    output_text += f"*Begründung:* {kartentype.get('rationale_de', '')}\n\n"
                    
                    output_text += f" **Vom LLM dynamisch generierte Folium-Parameter:**\n"
                    output_text += f"```json\n{json.dumps(llm_kwargs, indent=2, ensure_ascii=False)}\n```\n"
                    
                    if kartentype.get("critical_attributes_notice"):
                        output_text += f" **Datenhinweis:** *{kartentype['critical_attributes_notice']}*\n"

                    st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")
                    output=output_text

        elif result["tool"]=="export_map":
            html_path = "map.html"
            png_path = "map.png"
            pdf_path = "map.pdf"
            st.session_state.show_export_buttons = True
            st.session_state.export_files = {
                "html": html_path,
                "png": png_path,
                "pdf": pdf_path
            }

            m.save(html_path)
            html_to_png(html_path,png_path)
            png_to_pdf(png_path,pdf_path)

            st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")
            
        elif result["tool"]=="set_title":
            selected_layer_name = get_selected_layer_name(user_query,show_layers)
            layer = next((l for l in show_layers if l["name"] == selected_layer_name), show_layers[-1])
            Karte_Titel=set_selected_layer_name(layer)
            st.session_state.Karte_Titel=Karte_Titel
            st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")

        elif result["tool"] == "Layer_Parameter_anzeigen" or any(k in user_query.lower() for k in [
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
            "farbe",
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
        ]):
            target_layers = show_layers if show_layers else layers

            if not target_layers:
                output = "Bitte erstellen Sie zuerst einen Kartenlayer."
            else:
                selected_layer_name = get_selected_layer_name(user_query, target_layers)
                layer = next((l for l in target_layers if l["name"] == selected_layer_name), target_layers[-1])

                output = describe_layer_parameters(layer)
                st.session_state.workflow_state.append(
                    f"input: {user_query}, goal: Layer_Parameter_anzeigen, result: success"
                )    

        elif result["tool"]=="Kartenstil_anpassen": 
            style_plan = get_multi_layer_styles(user_query, show_layers)

            for update in style_plan.get("updates", []):

                layer_number = update.get("layer_number")
                style_update = update.get("style", {})

                if layer_number is None:
                    continue

                index = int(layer_number) - 1

                if 0 <= index < len(show_layers):

                    layer = show_layers[index]

                    if "folium_kwargs" not in layer:
                        layer["folium_kwargs"] = {}

                    layer["folium_kwargs"].update(style_update)

                    if "style" not in layer:
                        layer["style"] = {}

                    layer["style"].update(style_update)

            st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")

        # ====================================================================
        # UNIFIED SPATIAL ANALYSIS GATEWAY (BUFFER, NEAREST, OVERLAY COUNT)
        # ====================================================================
        elif result["tool"] == "spatial_analysis" or any(k in user_query.lower() for k in [
            "puffer", "buffer", "abstand", "zähle", "zaehle", "entfernung", "schneiden",
            "fläche", "flaeche", "flächengröße", "flaechengroesse", "km²", "km2", "quadratmeter",
            "dichte", "punktdichte", "pro km", "pro km²", "pro km2"
        ]):
            target_layers = show_layers if show_layers else layers
            if not target_layers:
                st.warning("Bitte laden Sie zuerst Geodaten hoch!")
            else:
                # 1. 自动识别主操作图层
                selected_layer_name = get_selected_layer_name(user_query, target_layers)
                layer = next((l for l in target_layers if l["name"] == selected_layer_name), target_layers[-1])
                
                # 2. 调用大模型解析具体的空间分析算法路由计划 (buffer / nearest / overlay_count)
                analysis_plan = parse_spatial_analysis_intent(user_query)
                analysis_type = analysis_plan.get("analysis_type", "buffer")
                params = analysis_plan.get("params", {"distance": 500})
                
                secondary_layer = None
                
                # 3. 如果需要双图层操作（邻近性或叠加统计），智能化提取第二个图层
                if analysis_type in ["nearest", "overlay_count", "density_by_area"]:
                    # 寻找非主操作图层的另一个图层作为分析配对物
                    other_layers = [l for l in st.session_state.layers if l["name"] != layer["name"]]
                    if other_layers:
                        secondary_layer = other_layers[-1] # 默认抓取最新上传的另一个相异图层
                    else:
                        secondary_layer = layer # 兜底自相交计算
                
                try:
                    # 4. 压入底层的物理拓扑交叉计算管道
                    sec_data = secondary_layer["data"] if secondary_layer else None
                    analyzed_geojson = perform_spatial_analysis(analysis_type, layer["data"], params, secondary_geo_input=sec_data)

                    # 5. 根据不同的空间分析路由，执行自适应前端渲染与数据报告输出
                    if analysis_type == "buffer":
                        dist = params.get('distance', 500)
                        new_layer_name = f"Buffer_{dist}m_{layer['name']}"
                        new_layer_name = make_unique_layer_name(new_layer_name, st.session_state.show_layers)
                        st.session_state.show_layers.append({
                            "name": new_layer_name, 
                            "type": "polygon", 
                            "data": analyzed_geojson,
                            "data_field": None, 
                            "folium_kwargs": {
                                "fillColor": "#ff7800", 
                                "color": "#ff0000", 
                                "weight": 2, 
                                "fillOpacity": 0.35
                            }
                        })
                        output = f" **Pufferanalyse abgeschlossen!**\n\nEin **{dist} Meter Puffer** wurde für den Layer **{layer['name']}** erfolgreich berechnet und als Polygon-Ebene visualisiert. 🚀"

                    elif analysis_type == "nearest" and secondary_layer:
                        new_layer_name = f"Abstand_zu_{secondary_layer['name']}"
                        new_layer_name = make_unique_layer_name(new_layer_name, st.session_state.show_layers)
                        st.session_state.show_layers.append({
                            "name": new_layer_name, 
                            "type": "geojson",            
                            "data": analyzed_geojson,      
                            "folium_kwargs": {             
                                "fillColor": "#00a8ff",
                                "color": "#0044ff",
                                "weight": 3,
                                "fillOpacity": 0.5
                            }
                        })
                        output = f" **Nachbarschaftsanalyse erfolgreich!**\n\nDie relative Distanz von **{layer['name']}** zum Datensatz **{secondary_layer['name']}** wurde präzise berechnet.\n\nDas Ergebnis wurde als neues Attribut `abstand_m` (in Metern) in die GeoJSON-Eigenschaften injiziert. Sie können den Wert jetzt via Hover-Tooltip auf der Karte ablesen! "
                
                    elif analysis_type == "overlay_count" and secondary_layer:
                        new_layer_name = f"Dichte_{secondary_layer['name']}_in_{layer['name']}"
                        new_layer_name = make_unique_layer_name(new_layer_name, st.session_state.show_layers)
                        st.session_state.show_layers.append({
                            "name": new_layer_name, 
                            "type": "choropleth", 
                            "data": analyzed_geojson,
                            "data_field": "objekt_anzahl", 
                            "folium_kwargs": {}
                        })
                        
                        features = analyzed_geojson.get("features", [])
                        total_found = sum([f["properties"].get("objekt_anzahl", 0) for f in features])
                        
                        output = f" **Räumlicher Overlay-Bericht (Zählung) erfolgreich!**\n\nIn den Geometriezonen von **{layer['name']}** wurden insgesamt **{total_found} sich überschneidende Objekte** aus dem Layer **{secondary_layer['name']}** detektiert.\n\nDie Verteilungsdichte wurde automatisch als thematischer **Choropleth-Statistik-Layer** auf der Karte eingefärbt!"


                    elif analysis_type == "area":
                        new_layer_name = f"Flaeche_{layer['name']}"
                        new_layer_name = make_unique_layer_name(new_layer_name, st.session_state.show_layers)

                        st.session_state.show_layers.append({
                            "name": new_layer_name,
                            "type": "choropleth",
                            "data": analyzed_geojson,
                            "data_field": "area_km2",
                            "folium_kwargs": {
                                "fillColor": "Blues",
                                "color": "black",
                                "weight": 1,
                                "fillOpacity": 0.7
                            }
                        })

                        features = analyzed_geojson.get("features", [])

                        area_rows = []

                        for i, feature in enumerate(features):
                            props = feature.get("properties", {})

                            # 尽量自动找一个适合作为“区名”的字段
                            name = (
                                props.get("name")
                                or props.get("Name")
                                or props.get("stadtteil")
                                or props.get("Stadtteil")
                                or props.get("bezirk")
                                or props.get("Bezirk")
                                or props.get("ortsteil")
                                or props.get("Ortsteil")
                                or props.get("GEN")
                                or props.get("BEZ")
                                or props.get("bezeichnung")
                                or props.get("Bezeichnung")
                                or f"Gebiet {i + 1}"
                            )

                            area_m2 = props.get("area_m2", 0)
                            area_km2 = props.get("area_km2", 0)

                            try:
                                area_m2 = round(float(area_m2), 2)
                                area_km2 = round(float(area_km2), 4)
                            except:
                                area_m2 = 0
                                area_km2 = 0

                            area_rows.append({
                                "name": name,
                                "area_m2": area_m2,
                                "area_km2": area_km2
                            })

                        # 按面积从大到小排序，方便用户看
                        area_rows = sorted(area_rows, key=lambda x: x["area_km2"], reverse=True)

                        total_area = round(sum(row["area_km2"] for row in area_rows), 4) if area_rows else 0
                        max_area = area_rows[0]["area_km2"] if area_rows else 0
                        min_area = area_rows[-1]["area_km2"] if area_rows else 0

                        # 生成 Markdown 表格
                        table_text = "| Gebiet | Fläche m² | Fläche km² |\n"
                        table_text += "|---|---:|---:|\n"

                        for row in area_rows:
                            table_text += (
                                f"| {row['name']} | "
                                f"{row['area_m2']:,} | "
                                f"{row['area_km2']} |\n"
                            )

                        output = (
                            f" **Flächenberechnung abgeschlossen!**\n\n"
                            f"Für den Layer **{layer['name']}** wurden die Attribute "
                            f"`area_m2` und `area_km2` berechnet.\n\n"
                            f"**Zusammenfassung:**\n\n"
                            f"- **Anzahl der Gebiete:** {len(area_rows)}\n"
                            f"- **Gesamtfläche:** {total_area} km²\n"
                            f"- **Größte Fläche:** {max_area} km²\n"
                            f"- **Kleinste Fläche:** {min_area} km²\n\n"
                            f"**Fläche je Gebiet:**\n\n"
                            f"{table_text}\n"
                            f"Zusätzlich wurde ein neuer Choropleth-Layer **{new_layer_name}** "
                            f"nach `area_km2` auf der Karte erstellt."
                        )

                        features = analyzed_geojson.get("features", [])
                        area_values = [
                            f["properties"].get("area_km2", 0)
                            for f in features
                            if f.get("properties", {}).get("area_km2") is not None
                        ]

                        total_area = round(sum(area_values), 4) if area_values else 0
                        max_area = round(max(area_values), 4) if area_values else 0
                        min_area = round(min(area_values), 4) if area_values else 0

                        output = (
                            f" **Flächenberechnung abgeschlossen!**\n\n"
                            f"Für den Layer **{layer['name']}** wurden die Attribute "
                            f"`area_m2` und `area_km2` berechnet.\n\n"
                            f"- **Gesamtfläche:** {total_area} km²\n"
                            f"- **Größte Fläche:** {max_area} km²\n"
                            f"- **Kleinste Fläche:** {min_area} km²\n\n"
                            f"Das Ergebnis wurde als Choropleth-Karte nach `area_km2` dargestellt."
                        )    


                    elif analysis_type == "density_by_area" and secondary_layer:
                        new_layer_name = f"Dichte_{secondary_layer['name']}_pro_km2_in_{layer['name']}"
                        new_layer_name = make_unique_layer_name(new_layer_name, st.session_state.show_layers)

                        st.session_state.show_layers.append({
                            "name": new_layer_name,
                            "type": "choropleth",
                            "data": analyzed_geojson,
                            "data_field": "dichte_pro_km2",
                            "folium_kwargs": {
                                "fillColor": "YlOrRd",
                                "color": "black",
                                "weight": 1,
                                "fillOpacity": 0.7
                            }
                        })

                        features = analyzed_geojson.get("features", [])
                        total_points = sum([
                            f["properties"].get("objekt_anzahl", 0)
                            for f in features
                        ])

                        density_values = [
                            f["properties"].get("dichte_pro_km2", 0)
                            for f in features
                            if f.get("properties", {}).get("dichte_pro_km2") is not None
                        ]

                        max_density = round(max(density_values), 2) if density_values else 0

                        output = (
                            f" **Dichteanalyse abgeschlossen!**\n\n"
                            f"Für jedes Polygon im Layer **{layer['name']}** wurde berechnet, "
                            f"wie viele Objekte aus **{secondary_layer['name']}** pro Quadratkilometer vorkommen.\n\n"
                            f"- **Gesamtzahl der gezählten Objekte:** {total_points}\n"
                            f"- **Höchste Dichte:** {max_density} Objekte/km²\n\n"
                            f"Neue Attribute im Ergebnislayer:\n"
                            f"- `area_km2`\n"
                            f"- `objekt_anzahl`\n"
                            f"- `dichte_pro_km2`\n\n"
                            f"Das Ergebnis wurde als Choropleth-Karte nach `dichte_pro_km2` dargestellt."
                        )

                    
                    st.session_state.map_key += 1 # 触发物理刷新
                    
                except Exception as e:
                    st.error(f"Fehler bei der räumlichen Berechnung: {str(e)}")


        # ====================================================================
        # UNIFIED MACHINE LEARNING GATEWAY (TRENDS & SPATIAL PREDICTION)
        # ====================================================================
        elif result["tool"] == "ml_analysis" or any(k in user_query.lower() for k in ["prognose", "vorhersage", "trend", "machine learning", "ml", "regression"]):
            active_pool = st.session_state.show_layers if st.session_state.show_layers else st.session_state.layers
            if not active_pool:
                st.warning("Bitte laden Sie zuerst Geodaten hoch!")
            else:
                # 1. 自动提取用户想要操作的目标图层
                selected_layer_name = get_selected_layer_name(user_query, active_pool)
                layer = next((l for l in active_pool if l["name"] == selected_layer_name), active_pool[-1])
                
                with st.spinner(" Berechne Machine Learning Modell..."):
                    try:
                        # 2. 调度大模型解析机器学习策略与超参数（分类出是时间趋势还是空间预测）
                        ml_plan = parse_ml_intent(user_query)
                        prediction_type = ml_plan.get("prediction_type", "trend")
                        params = ml_plan.get("params", {})
                        
                        # 3. 运行底层的物理 Scikit-Learn 算法算子进行拟合训练
                        ml_result = perform_ml_prediction(prediction_type, layer, params)
                        
                        # 4. 结果分流：路由一（时间趋势分析）
                        if prediction_type == "trend":
                            target = ml_result["target_field"]
                            direction = ml_result["trend_direction"]
                            arrow = "" if direction == "steigend" else ""
                            
                            output = f"{arrow} **Zeitliche ML-Trendanalyse für '{layer['name']}'**\n\n"
                            output += f"- **Analysierte Zielvariable:** `{target}`\n"
                            output += f"- **Modell-Trendrichtung:** Der berechnete Trend ist **{direction}** (Linear-Koeffizient: {ml_result['coef']:.4f}).\n\n"
                            output += " **Prädiktion für kommende Intervalle (Zukunft):**\n"
                            
                            for yr, pred in zip(ml_result["future_years"], ml_result["predictions"]):
                                output += f"  - **Jahr {yr}:** ~ {pred}\n"
                                
                        # 4. 结果分流：路由二（空间格局格局预测）
                        elif prediction_type == "spatial_prediction":
                            new_layer_name = f"ML_Prognose_{layer['name']}"
                            new_layer_name = make_unique_layer_name(new_layer_name, st.session_state.show_layers)

                            ml_data = convert_geojson_points_to_points(ml_result) if "features" in ml_result else ml_result
                            
                            # 核心防崩溃过滤：强制将可能残留在属性里的 Python set/frozenset 对象清洗转化为规范的 list
                            if isinstance(ml_data, list):
                                for pt in ml_data:
                                    if "properties" in pt:
                                        pt["properties"] = {k: (list(v) if isinstance(v, (set, frozenset)) else v) for k, v in pt["properties"].items()}
                            
                            # 5. 将通过随机森林推算出的全新空间数据图层压入 Folium 渲染展示队列
                            st.session_state.show_layers.append({
                                "name": new_layer_name,
                                "type": "points",
                                "data": ml_data,
                                "folium_kwargs": {
                                    "color": "purple",
                                    "icon": "eye-open"
                                }
                            })
                            
                            output = f" **Räumliche Machine-Learning Vorhersage erfolgreich!**\n\n"
                            output += f"Ein **Random-Forest-Regressor** hat die räumlichen Abhängigkeiten des Layers **{layer['name']}** gelernt.\n\n"
                            output += f"Ein neuer Layer **'{new_layer_name}'** wurde generiert und auf der Karte lila (purple) eingefärbt. Bewegen Sie die Maus über die Punkte, um die prädizierten Attribute einzusehen:\n"
                            output += f"- `original_wert` (Realer historischer Wert)\n"
                            output += f"- `vorhersage_wert` (Vom ML-Modell geschätztes Potenzial)\n"
                            output += f"- `residuat_abweichung` (Fehlerrate / Residuum)\n"
                            
                        st.session_state.messages.append({"role": "assistant", "content": output})
                        st.session_state.map_key += 1 # 物理破除 Folium 缓存强制重绘刷新
                        
                    except Exception as e:
                        st.error(f"Fehler bei der ML-Berechnung: {str(e)}")

        # ====================================================================
        # DATA REPORT GENERATION GATEWAY
        # ====================================================================
        elif result["tool"] == "generiere_daten_bericht" or any(k in user_query.lower() for k in ["bericht", "report", "statistik", "zusammenfassung"]):
            target_layers = show_layers if show_layers else layers
            if not target_layers:
                st.warning("Bitte laden Sie zuerst Geodaten hoch, um einen Bericht zu generieren!")
            else:
                # 1. 自动识别用户想要分析哪一个图层
                selected_layer_name = get_selected_layer_name(user_query, target_layers)
                layer = next((l for l in target_layers if l["name"] == selected_layer_name), target_layers[-1])
                
                with st.spinner(f"Generiere ausführlichen Datenbericht für '{layer['name']}'..."):
                    try:
                        # 2. 调用后端功能生成纯文本/Markdown格式的专业报告
                        bericht_output = generiere_daten_bericht(user_query, layer)
                        
                        # 3. 将生成的报告内容作为助手的回复展示在聊天框中
                        st.session_state.messages.append({"role": "assistant", "content": bericht_output})
                        st.session_state.workflow_state.append(f"input: {user_query}, goal: generiere_daten_bericht, result: success")
                    except Exception as e:
                        st.error(f"Fehler bei der Berichtserstellung: {str(e)}")
                        st.session_state.workflow_state.append(f"input: {user_query}, goal: generiere_daten_bericht, result: failure")

        elif result["tool"]=="Layer_verwalten": 
            neue_layers_list = verwalten_layers(user_query,show_layers)
            new_layers = []
            layer_dict = {l["name"]: l for l in show_layers}
            for layer in neue_layers_list:
                original_layer = copy.deepcopy(layer_dict[layer["source"]])
                original_layer["name"] = layer["name"]
                new_layers.append(original_layer)
  
            st.session_state.show_layers=new_layers
            st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")
        
        elif result["tool"]=="Geodatei_analysieren": 
            selected_layer_name = get_selected_layer_name(user_query,layers)
            layer = next((l for l in layers if l["name"] == selected_layer_name), layers[-1])
            output=verstehen_Geodatei(user_query,layer)
            st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")

        elif result["tool"]=="get_overview": 
            output=get_overview(user_query)
            st.session_state.workflow_state.append(f"input: {user_query}, goal: {result["tool"]}, result: success")

        elif result["tool"]=="error":
            st.session_state.workflow_state.append(f"input: {user_query}, goal: unsupported function, result: failure")
        
        else:
            st.session_state.workflow_state.append(f"input: {user_query}, goal: unknown, result: failure")

        mes=output_response(st.session_state.workflow_state)
        output+="\n\n" 
        output+=mes     
        st.session_state.messages.append({
                    "role": "assistant",
                    "content": output
                })       
        st.session_state.map_key += 1
    st.rerun()
