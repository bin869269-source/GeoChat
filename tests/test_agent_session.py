"""Real LangGraph/ToolNode and Streamlit server tests with scripted AI messages."""
import copy
import importlib.util

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.conftest import ROOT


def make_app(backend):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "frontend.py"), default_timeout=30)
    app.session_state.business_session = backend.session
    app.session_state.session_agent = backend
    return app.run()


def test_missing_credential_blocks_creation_not_import(backend, monkeypatch):
    # Approved Phase 3C change: import is credential-free, actual creation is not.
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    spec = importlib.util.spec_from_file_location("backend_no_key", ROOT / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.create_agent_session()._runtime._model is None
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY wurde nicht gefunden"):
        module.create_llm()


def install_script(backend, monkeypatch, steps):
    pending = iter(steps)
    seen = []
    def invoke(messages):
        seen.append(messages)
        step = next(pending)
        return AIMessage(content="finished") if step is None else AIMessage(content="", tool_calls=[step])
    monkeypatch.setattr(backend.llm_with_tools, "invoke", invoke)
    return seen


def call(name, args, number):
    return {"name": name, "args": args, "id": str(number), "type": "tool_call"}


def test_real_graph_multistep(backend, bind, points, monkeypatch):
    raw, shown = bind(points)
    before = copy.deepcopy(raw)
    seen = install_script(backend, monkeypatch, [
        call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, 1),
        call("calculate_buffer_analysis", {"layer_name": "points_points", "distance_meters": 50}, 2), None])
    state = backend.agent_app.invoke({"messages": [HumanMessage(content="show then buffer")]})
    assert len(seen) == 3 and state["messages"][-1].content == "finished"
    results = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [r.tool_call_id for r in results] == ["1", "2"]
    assert all("Erfolg" in r.content for r in results)
    assert len(shown) == 2 and raw == before
    assert "points_points" in seen[-1][0].content


def test_graph_tool_failure_feedback(backend, bind, monkeypatch):
    _, shown = bind()
    seen = install_script(backend, monkeypatch, [
        call("calculate_buffer_analysis", {"layer_name": "absent"}, 1), None])
    state = backend.agent_app.invoke({"messages": [HumanMessage(content="missing layer")]})
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert "nicht gefunden" in result.content and len(seen) == 2 and not shown
    assert any(isinstance(m, ToolMessage) for m in seen[1])


def test_graph_direct_answer(backend, monkeypatch):
    seen = install_script(backend, monkeypatch, [None])
    result = backend.agent_app.invoke({"messages": [HumanMessage(content="hello")]})
    assert len(seen) == 1 and result["messages"][-1].content == "finished"
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])


def test_partial_state_survives_later_llm_error(backend, bind, points, monkeypatch):
    _, shown = bind(points)
    count = 0
    def invoke(messages):
        nonlocal count
        count += 1
        if count == 1:
            return AIMessage(content="", tool_calls=[call("visualize_layer_on_map", {"layer_name": "points"}, 1)])
        raise RuntimeError("synthetic later failure")
    monkeypatch.setattr(backend.llm_with_tools, "invoke", invoke)
    with pytest.raises(RuntimeError, match="synthetic later failure"):
        backend.agent_app.invoke({"messages": [HumanMessage(content="partial")]})
    assert len(shown) == 1  # Successful tools survive a later Agent failure.


def test_state_reference_and_reset(backend, points):
    raw, shown = [points], []
    backend.set_agent_context(raw, shown)
    got_raw, got_shown = backend.get_agent_layers()
    assert got_raw is backend.session.raw_view and got_shown is backend.session.map_view
    assert raw == [points] and shown == []
    backend.visualize_layer_on_map.invoke({"layer_name": "points"})
    assert len(got_shown) == 1 and raw == [points] and shown == []
    backend.set_agent_context([], [])
    assert backend.get_agent_layers() == ([], [])


def test_explicit_context_replacement(backend, points, polygons):
    a_raw, a_shown, b_raw, b_shown = [points], [], [polygons], []
    backend.set_agent_context(a_raw, a_shown)
    backend.set_agent_context(b_raw, b_shown)
    assert backend.get_agent_layers()[0][0]["data"] == b_raw[0]["data"]
    backend.visualize_layer_on_map.invoke({"layer_name": "polygons"})
    assert len(backend.get_agent_layers()[1]) == 1 and not a_shown and not b_shown


def test_independent_sessions_must_not_cross_write(backend, points):
    # Deterministic interleaving of A-start, B-start, A-tool: no timing race required.
    a = backend.create_agent_session()
    b = backend.create_agent_session()
    a.set_agent_context([copy.deepcopy(points)], [])
    b.set_agent_context([copy.deepcopy(points)], [])
    a_shown, b_shown = a.get_agent_layers()[1], b.get_agent_layers()[1]
    output = a.visualize_layer_on_map.invoke({"layer_name": "points"})  # A's pending tool
    if "Erfolg" not in output:
        pytest.fail("Visualization failed before session isolation assertion: " + output)
    assert len(a_shown) == 1 and b_shown == []


def test_streamlit_server_layer_management(backend, points, polygons):
    from streamlit.testing.v1 import AppTest
    app = make_app(backend)
    assert not app.exception
    backend.set_agent_context([dict(points, file_id="synthetic.csv")], [polygons])
    app.session_state.processed_files = {"synthetic.csv"}
    app.run()
    next(b for b in app.button if b.key.startswith("del_raw_")).click().run()
    assert not app.exception and app.session_state.business_session.raw_view == []
    assert app.session_state.processed_files == set() and len(app.session_state.business_session.map_view) == 1
    next(b for b in app.button if b.key.startswith("del_map_")).click().run()
    assert not app.exception and app.session_state.business_session.map_view == []
    backend.set_agent_context([points], [])
    app.session_state.messages = [{"role": "user", "content": "synthetic"}]
    app.run()
    next(b for b in app.button if b.label == "🗑️ Chat & Sitzung zurücksetzen").click().run()
    assert not app.exception and app.session_state.business_session.raw_view == [] and app.session_state.messages == []
    assert app.session_state.agent_msg_history == []


def test_streamlit_html_export_gateway(backend, polygons, tmp_path, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)
    downloads = []
    original_download = st.download_button
    def record_download(label, data, **kwargs):
        downloads.append((label, data, kwargs))
        return original_download(label, data, **kwargs)
    monkeypatch.setattr(st, "download_button", record_download)
    app = make_app(backend)
    backend.set_agent_context([], [polygons])
    app.run()
    next(b for b in app.button if b.label == "Karte als HTML exportieren").click().run()
    assert not app.exception
    assert len(downloads) == 1
    label, payload, options = downloads[0]
    assert label == "📥 HTML-Datei herunterladen"
    assert isinstance(payload, bytes) and "L.geoJson(" in payload.decode("utf-8")
    assert options == {"file_name": "geogpt_map.html", "mime": "text/html"}
    assert not (tmp_path / "map_export.html").exists()


def test_streamlit_chat_gateway_and_current_history(backend, points, monkeypatch):
    from streamlit.testing.v1 import AppTest
    install_script(backend, monkeypatch, [
        call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, 1), None])
    app = make_app(backend)
    backend.set_agent_context([points], [])
    app.chat_input[0].set_value("show synthetic points").run()
    assert not app.exception and len(app.session_state.business_session.map_view) == 1
    assert app.session_state.messages[-1] == {"role": "assistant", "content": "finished"}
    # Current frontend retains only user + final AI, losing intermediate tool
    # messages. Keep this characterization separate from future history design.
    history = app.session_state.agent_msg_history
    assert len(history) == 2 and isinstance(history[0], HumanMessage) and isinstance(history[1], AIMessage)
    assert not any(isinstance(m, ToolMessage) for m in history)
