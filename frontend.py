# %%
# ==============================================================================
# frontend.py - Streamlit Interface for Autonomous ReAct GIS Agent
# ==============================================================================
import streamlit as st
from streamlit_folium import st_folium
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from agent.graph import create_agent_session
from agent.llm import smart_analyze_geodata, serialize_metadata_sample
from core.session import SessionData
from gis.data_io import parse_uploaded_file, resolve_source_crs, prepare_map_layers
from maps.renderer import (
    create_layered_map,
    Titel_hinzufügen,
    render_map_html
)


def _message_text(content):
    """Extract readable text from plain or block-style LangChain content."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part.strip()).strip()
    return str(content).strip() if content is not None else ""


def _render_chat_history(placeholder):
    with placeholder.container():
        if not st.session_state.messages:
            st.chat_message("assistant").write(
                "Hallo! Ich bin dein autonomer GIS-Agent. Du kannst Daten hochladen oder mich bitten, "
                "z.B. **'Zeige die Stadtgrenze von Dresden'**, **'Berechne einen 1000m Puffer'** oder "
                "**'Führe eine räumliche Dichteanalyse durch'**."
            )
        for msg in st.session_state.messages:
            st.chat_message(msg["role"]).write(msg["content"])


def _render_map(placeholder):
    with placeholder.container():
        try:
            m = create_layered_map(
                prepare_map_layers(st.session_state.business_session.map_view),
                show_basemap=st.session_state.show_basemap
            )
        except ValueError as exc:
            st.error(f"CRS/Karte: {exc}")
            m = create_layered_map([], show_basemap=st.session_state.show_basemap)
        if st.session_state.Karte_Titel:
            Titel_hinzufügen(m, st.session_state.Karte_Titel)

        st_folium(
            m,
            width="100%",
            height=620,
            key=f"map_instance_{st.session_state.map_key}"
        )

# Seitengrundkonfiguration
st.set_page_config(layout="wide", page_title="GeoGPT Autonomous GIS Agent")
st.markdown("## 🌍 GeoGPT: Autonomer GIS Agent")

# ==========================================
# BLOCK 1: SESSION STATE INITIALIZATION
# ==========================================
if "messages" not in st.session_state:
    st.session_state.messages = []
if "business_session" not in st.session_state:
    st.session_state.business_session = SessionData()
if "session_agent" not in st.session_state:
    st.session_state.session_agent = create_agent_session(st.session_state.business_session)
if "processed_files" not in st.session_state:
    st.session_state.processed_files = set()
if "map_key" not in st.session_state:
    st.session_state.map_key = 0            # Erzwingt die Neudarstellung der Folium-Karte
if "upload_key" not in st.session_state:
    st.session_state.upload_key = 0         # Entfernt beim Zurücksetzen noch ausgewählte Uploads
if "Karte_Titel" not in st.session_state:
    st.session_state.Karte_Titel = ""
if "show_basemap" not in st.session_state:
    st.session_state.show_basemap = True
if "agent_msg_history" not in st.session_state:
    st.session_state.agent_msg_history = []  # Verlauf aus nativen LangChain-Nachrichten

# ==========================================
# BLOCK 2: SIDEBAR CONTROL PANEL
# ==========================================
with st.sidebar:
    st.markdown("### 🗺️ Kartenverwaltung")
    st.markdown("---")

    # 1. Hochgeladene Rohdaten anzeigen und entfernen
    with st.expander("📁 Hochgeladene Rohdaten", expanded=True):
        if st.session_state.business_session.raw_view:
            for i, layer in enumerate(st.session_state.business_session.raw_view):
                col_name, col_delete = st.columns([4, 1])
                with col_name:
                    st.write(f"**{i + 1}.** {layer['name']} (`{layer['type']}`)")
                with col_delete:
                    if st.button("✕", key=f"del_raw_{layer['dataset_id']}"):
                        deleted_file_id = layer.get("file_id", layer["name"])
                        st.session_state.business_session.remove_upload(layer["dataset_id"])
                        st.session_state.processed_files.discard(deleted_file_id)
                        st.session_state.map_key += 1
                        st.rerun()
        else:
            st.info("Noch keine Geodaten hochgeladen.")

    # 2. Auf der Karte dargestellte Layer anzeigen und entfernen
    with st.expander("📍 Aktive Kartenlayer", expanded=True):
        if st.session_state.business_session.map_view:
            for i, layer in enumerate(st.session_state.business_session.map_view):
                col_name, col_delete = st.columns([4, 1])
                with col_name:
                    st.write(f"**{i + 1}.** {layer['name']} (`{layer['type']}`)")
                with col_delete:
                    if st.button("✕", key=f"del_map_{layer['layer_id']}"):
                        st.session_state.business_session.remove_layer(layer["layer_id"])
                        st.session_state.map_key += 1
                        st.rerun()
        else:
            st.write("Keine aktiven Layer auf der Karte.")

    st.markdown("---")

    # 3. Karte als HTML exportieren
    st.markdown("### 💾 Export")
    if st.button("Karte als HTML exportieren"):
        try:
            m_export = create_layered_map(
                prepare_map_layers(st.session_state.business_session.map_view),
                show_basemap=st.session_state.show_basemap
            )
            if st.session_state.Karte_Titel:
                Titel_hinzufügen(m_export, st.session_state.Karte_Titel)
            st.download_button(
                "📥 HTML-Datei herunterladen", render_map_html(m_export),
                file_name="geogpt_map.html", mime="text/html"
            )
        except ValueError as exc:
            st.error(f"CRS/Kartenexport: {exc}")

    st.markdown("---")

    # 4. Basiskarte umschalten und Sitzung zurücksetzen
    st.session_state.show_basemap = st.checkbox(
        "OpenStreetMap Basemap anzeigen",
        value=st.session_state.show_basemap
    )

    if st.button("🗑️ Chat & Sitzung zurücksetzen"):
        st.session_state.messages = []
        st.session_state.agent_msg_history = []
        st.session_state.business_session.reset()
        st.session_state.processed_files = set()
        st.session_state.map_key += 1
        st.session_state.upload_key += 1
        st.session_state.Karte_Titel = ""
        st.rerun()

# ==========================================
# BLOCK 3: LAYOUT SYSTEM (COLUMNS)
# ==========================================
col1, col2 = st.columns([1, 1.4])

# ======= Linke Spalte: Datei-Upload und Agent-Dialog =======
with col1:
    st.subheader("💬 Daten & Agent-Dialog")

    # Mehrere Dateien gleichzeitig hochladen
    uploaded_files = st.file_uploader(
        "Geodaten hochladen (GeoJSON, JSON, CSV):",
        type=["json", "csv", "geojson"],
        accept_multiple_files=True,
        key=f"uploads_{st.session_state.upload_key}"
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            file_id = uploaded_file.name
            if file_id not in st.session_state.processed_files:
                with st.spinner(f"GeoGPT analysiert '{file_id}'..."):
                    try:
                        # 1. Datei einlesen und strukturieren
                        parsed_data = parse_uploaded_file(uploaded_file)
                        raw_data = parsed_data["data"]
                        source_crs = resolve_source_crs(raw_data)

                        # 2. Stichprobe für die Attributanalyse durch das LLM erstellen
                        sample_str = serialize_metadata_sample(raw_data)

                        # 3. Metadaten durch das LLM analysieren lassen
                        analysis = smart_analyze_geodata(
                            uploaded_file.name, sample_str, llm=st.session_state.session_agent.llm)
                        detected_type = analysis.get("detected_type", parsed_data["type"])
                        detected_fields = analysis.get("data_fields", [])
                        recommended_name = analysis.get("recommended_layer_name", uploaded_file.name)

                        # 4. Rohdaten in der Sitzung speichern
                        st.session_state.business_session.add_upload(
                            recommended_name, raw_data, file_id=file_id,
                            renderer=detected_type, data_fields=detected_fields, crs=source_crs
                        )
                        st.session_state.processed_files.add(file_id)

                        st.success(f"Erfolgreich geladen: **{recommended_name}** ({detected_type})")
                    except Exception as e:
                        st.error(f"Fehler beim Parsen von '{file_id}': {e}")

    st.markdown("---")

    # Container für den Chatverlauf
    chat_container = st.container(height=420)
    with chat_container:
        chat_history_placeholder = st.empty()

# ======= Rechte Spalte: Interaktive Folium-Karte =======
with col2:
    st.subheader("🗺️ Interaktive Karte")
    map_placeholder = st.empty()

# ==========================================
# BLOCK 4: AGENT EXECUTION GATEWAY (ReAct LOOP)
# ==========================================
user_query = st.chat_input("Was möchtest du analysieren oder auf der Karte darstellen?")

if user_query:
    # 1. Benutzereingabe speichern und anzeigen
    st.session_state.messages.append({"role": "user", "content": user_query})
    user_msg_obj = HumanMessage(content=user_query)
    st.session_state.agent_msg_history.append(user_msg_obj)

    with st.spinner("🤖 GeoGPT überlegt und führt Werkzeuge aus..."):
        try:
            # Die Agent-Werkzeuge sind an diese Sitzung gebunden und veröffentlichen Ergebnisse atomar.
            map_layer_count_before = len(st.session_state.business_session.map_view)
            graph_input = {
                "messages": st.session_state.agent_msg_history
            }
            final_state = st.session_state.session_agent.agent_app.invoke(graph_input)

            # Use the final non-tool-call assistant message when available.
            final_ai_msg = next((message for message in reversed(final_state.get("messages", []))
                                 if isinstance(message, AIMessage) and not message.tool_calls), None)
            output_text = _message_text(final_ai_msg.content) if final_ai_msg else ""
            if not output_text:
                tool_reports = [_message_text(message.content)
                                for message in final_state.get("messages", [])
                                if isinstance(message, ToolMessage) and _message_text(message.content)]
                if tool_reports:
                    output_text = ("Die Aktion wurde ausgeführt, aber der Agent hat keine Zusammenfassung erstellt. "
                                   "Werkzeugergebnis:\n\n"
                                   + tool_reports[-1])
                else:
                    output_text = "Der Agent hat keinen anzeigbaren Text zurückgegeben. Bitte versuche es erneut oder formuliere die Anfrage anders."

            execution_messages = [message for message in final_state.get("messages", [])
                                  if isinstance(message, ToolMessage)]
            visualization_results = [message for message in execution_messages
                                     if message.name == "visualize_layer_on_map"]
            visualization_calls = [call
                                   for message in final_state.get("messages", [])
                                   if isinstance(message, AIMessage)
                                   for call in (message.tool_calls or [])
                                   if call.get("name") == "visualize_layer_on_map"]
            if visualization_results:
                actual_result = _message_text(visualization_results[-1].content)
                if not actual_result.startswith("Erfolg:"):
                    output_text = actual_result or "Das Kartenwerkzeug ist fehlgeschlagen, hat aber keine Fehlerdetails zurückgegeben."
                elif len(st.session_state.business_session.map_view) <= map_layer_count_before:
                    output_text = ("Das Kartenwerkzeug meldet Erfolg, aber in dieser Sitzung wurde kein neuer Kartenlayer angelegt. "
                                   "Prüfe, ob eine andere Anwendungsinstanz läuft, oder sieh im Streamlit-Terminal nach Fehlern.")
            elif visualization_calls:
                output_text = ("Der Agent hat die Kartenerstellung angefordert, aber der Workflow hat kein Ergebnis des Kartenwerkzeugs zurückgegeben. "
                               "Sieh im Streamlit-Terminal nach und versuche es erneut.")

            # Never carry an empty assistant response into the next request.
            st.session_state.agent_msg_history.append(
                final_ai_msg if final_ai_msg and _message_text(final_ai_msg.content) == output_text
                else AIMessage(content=output_text)
            )
            st.session_state.messages.append({"role": "assistant", "content": output_text})

            # 5. Kartenkomponente neu aufbauen
            st.session_state.map_key += 1

        except Exception as e:
            error_feedback = f"⚠️ Fehler bei der Ausführung des Agenten: {str(e)}"
            st.session_state.messages.append({"role": "assistant", "content": error_feedback})
            st.session_state.agent_msg_history.append(AIMessage(content=error_feedback))


# Render both stateful areas after handling this run's user input so the first
# response and map update do not depend on a follow-up widget or map event.
_render_chat_history(chat_history_placeholder)
_render_map(map_placeholder)
