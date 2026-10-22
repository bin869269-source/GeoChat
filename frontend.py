# %%
# ==============================================================================
# frontend.py - Streamlit Interface for Autonomous ReAct GIS Agent
# ==============================================================================
import streamlit as st
import json
import copy
from streamlit_folium import st_folium
from langchain_core.messages import HumanMessage, AIMessage

# 从重构后的 backend 导入核心 Agent 与地图功能
from backend import (
    agent_app,
    set_agent_context,
    create_layered_map,
    parse_uploaded_file,
    smart_analyze_geodata,
    DEFAULT_MAP_STYLE,
    Titel_hinzufügen
)

# 页面基础配置
st.set_page_config(layout="wide", page_title="GeoGPT Autonomous GIS Agent")
st.markdown("## 🌍 GeoGPT: Autonomer GIS Agent")

# ==========================================
# BLOCK 1: SESSION STATE INITIALIZATION
# ==========================================
if "messages" not in st.session_state:
    st.session_state.messages = []
if "layers" not in st.session_state:
    st.session_state.layers = []            # 用户上传的原始数据图层池
if "show_layers" not in st.session_state:
    st.session_state.show_layers = []       # 最终呈现在地图上的矢量/统计图层
if "processed_files" not in st.session_state:
    st.session_state.processed_files = set()
if "map_key" not in st.session_state:
    st.session_state.map_key = 0            # 控制 Folium 物理强制重绘
if "Karte_Titel" not in st.session_state:
    st.session_state.Karte_Titel = ""
if "show_basemap" not in st.session_state:
    st.session_state.show_basemap = True
if "agent_msg_history" not in st.session_state:
    st.session_state.agent_msg_history = []  # LangChain 原生 Message 对象历史

# ==========================================
# BLOCK 2: SIDEBAR CONTROL PANEL
# ==========================================
with st.sidebar:
    st.markdown("### 🗺️ Kartenverwaltung")
    st.markdown("---")

    # 1. 查看与删除上传的原始数据
    with st.expander("📁 Hochgeladene Rohdaten", expanded=True):
        if st.session_state.layers:
            for i, layer in enumerate(st.session_state.layers):
                col_name, col_delete = st.columns([4, 1])
                with col_name:
                    st.write(f"**{i + 1}.** {layer['name']} (`{layer['type']}`)")
                with col_delete:
                    if st.button("✕", key=f"del_raw_{i}"):
                        deleted_file_id = layer.get("file_id", layer["name"])
                        st.session_state.layers.pop(i)
                        st.session_state.processed_files.discard(deleted_file_id)
                        st.session_state.map_key += 1
                        st.rerun()
        else:
            st.info("Noch keine Geodaten hochgeladen.")

    # 2. 查看与删除地图已渲染的图层
    with st.expander("📍 Aktive Kartenlayer", expanded=True):
        if st.session_state.show_layers:
            for i, layer in enumerate(st.session_state.show_layers):
                col_name, col_delete = st.columns([4, 1])
                with col_name:
                    st.write(f"**{i + 1}.** {layer['name']} (`{layer['type']}`)")
                with col_delete:
                    if st.button("✕", key=f"del_map_{i}"):
                        st.session_state.show_layers.pop(i)
                        st.session_state.map_key += 1
                        st.rerun()
        else:
            st.write("Keine aktiven Layer auf der Karte.")

    st.markdown("---")

    # 3. 地图快速导出 (HTML)
    st.markdown("### 💾 Export")
    if st.button("Karte als HTML exportieren"):
        m_export = create_layered_map(
            st.session_state.show_layers,
            show_basemap=st.session_state.show_basemap
        )
        if st.session_state.Karte_Titel:
            Titel_hinzufügen(m_export, st.session_state.Karte_Titel)
        
        html_path = "map_export.html"
        m_export.save(html_path)
        with open(html_path, "rb") as f:
            st.download_button(
                "📥 HTML-Datei herunterladen",
                f.read(),
                file_name="geogpt_map.html",
                mime="text/html"
            )

    st.markdown("---")
    
    # 4. 底图开关与全局重置
    st.session_state.show_basemap = st.checkbox(
        "OpenStreetMap Basemap anzeigen",
        value=st.session_state.show_basemap
    )

    if st.button("🗑️ Chat & Sitzung zurücksetzen"):
        st.session_state.messages = []
        st.session_state.agent_msg_history = []
        st.session_state.layers = []
        st.session_state.show_layers = []
        st.session_state.processed_files = set()
        st.session_state.map_key += 1
        st.session_state.Karte_Titel = ""
        st.rerun()

# ==========================================
# BLOCK 3: LAYOUT SYSTEM (COLUMNS)
# ==========================================
col1, col2 = st.columns([1, 1.4])

# ======= 左侧：文件上传与对话窗口 =======
with col1:
    st.subheader("💬 Daten & Agent-Dialog")

    # 多文件上传支持
    uploaded_files = st.file_uploader(
        "Geodaten hochladen (GeoJSON, JSON, CSV):",
        type=["json", "csv", "geojson"],
        accept_multiple_files=True
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            file_id = uploaded_file.name
            if file_id not in st.session_state.processed_files:
                with st.spinner(f"GeoGPT analysiert '{file_id}'..."):
                    try:
                        # 1. 结构解析
                        parsed_data = parse_uploaded_file(uploaded_file)
                        raw_data = parsed_data["data"]

                        # 2. 采样切片供 LLM 进行特征盲审
                        if isinstance(raw_data, list):
                            sample_str = json.dumps(raw_data[:3], ensure_ascii=False)
                        elif isinstance(raw_data, dict) and "features" in raw_data:
                            sample_features = raw_data.get("features", [])[:2]
                            sample_str = json.dumps({"type": "FeatureCollection", "features": sample_features}, ensure_ascii=False)
                        else:
                            sample_str = json.dumps(raw_data, ensure_ascii=False)[:1000]

                        # 3. 调用元数据提取专家分析
                        analysis = smart_analyze_geodata(uploaded_file.name, sample_str)
                        detected_type = analysis.get("detected_type", parsed_data["type"])
                        detected_fields = analysis.get("data_fields", [])
                        recommended_name = analysis.get("recommended_layer_name", uploaded_file.name)

                        # 4. 存入原始数据池
                        new_raw_layer = {
                            "name": recommended_name,
                            "type": detected_type,
                            "data": raw_data,
                            "data_fields": detected_fields,
                            "file_id": file_id
                        }
                        st.session_state.layers.append(new_raw_layer)
                        st.session_state.processed_files.add(file_id)

                        st.success(f"Erfolgreich geladen: **{recommended_name}** ({detected_type})")
                    except Exception as e:
                        st.error(f"Fehler beim Parsen von '{file_id}': {e}")

    st.markdown("---")

    # 对话消息容器
    chat_container = st.container(height=420)
    with chat_container:
        if not st.session_state.messages:
            st.chat_message("assistant").write(
                "Hallo! Ich bin dein autonomer GIS-Agent. Du kannst Daten hochladen oder mich bitten, "
                "z.B. **'Zeige die Stadtgrenze von Dresden'**, **'Berechne einen 1000m Puffer'** oder "
                "**'Führe eine räumliche Dichteanalyse durch'**."
            )
        for msg in st.session_state.messages:
            st.chat_message(msg["role"]).write(msg["content"])

# ======= 右侧：交互式 Folium 地图 =======
with col2:
    st.subheader("🗺️ Interaktive Karte")

    # 动态构建地图并注入标题
    m = create_layered_map(
        st.session_state.show_layers,
        show_basemap=st.session_state.show_basemap
    )
    if st.session_state.Karte_Titel:
        Titel_hinzufügen(m, st.session_state.Karte_Titel)

    # 渲染地图组件
    st_folium(
        m,
        width="100%",
        height=620,
        key=f"map_instance_{st.session_state.map_key}"
    )

# ==========================================
# BLOCK 4: AGENT EXECUTION GATEWAY (ReAct LOOP)
# ==========================================
user_query = st.chat_input("Was möchtest du analysieren oder auf der Karte darstellen?")

if user_query:
    # 1. 记录并渲染用户输入
    st.session_state.messages.append({"role": "user", "content": user_query})
    user_msg_obj = HumanMessage(content=user_query)
    st.session_state.agent_msg_history.append(user_msg_obj)

    with st.spinner("🤖 GeoGPT überlegt und führt Werkzeuge aus..."):
        try:
            # 2. 将全局数据指针挂载到 Agent 工具运行环境
            set_agent_context(
                raw_layers=st.session_state.layers,
                show_layers=st.session_state.show_layers
            )

            # 3. 执行端到端的 LangGraph 状态图 (ReAct Loop)
            # Agent 会自主调用工具、执行计算、更新 show_layers，并在推理结束后返回最终文本
            graph_input = {
                "messages": st.session_state.agent_msg_history
            }
            final_state = agent_app.invoke(graph_input)

            # 4. 同步消息历史与输出
            final_ai_msg = final_state["messages"][-1]
            st.session_state.agent_msg_history.append(final_ai_msg)
            
            output_text = final_ai_msg.content
            st.session_state.messages.append({"role": "assistant", "content": output_text})

            # 5. 触发地图物理刷新
            st.session_state.map_key += 1

        except Exception as e:
            error_feedback = f"⚠️ Fehler bei der Ausführung des Agenten: {str(e)}"
            st.session_state.messages.append({"role": "assistant", "content": error_feedback})

    st.rerun()