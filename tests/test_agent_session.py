"""Real LangGraph/ToolNode and Streamlit server tests with scripted AI messages."""
import copy
import importlib.util

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.conftest import ROOT


def test_missing_credential_blocks_import(backend, monkeypatch):
    # Read neither .env nor Streamlit secrets. Confirm the production import
    # barrier independently of the happy-path dummy-key fixture.
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    spec = importlib.util.spec_from_file_location("backend_no_key", ROOT / "backend.py")
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY wurde nicht gefunden"):
        spec.loader.exec_module(module)


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
    assert len(shown) == 1  # Characterization; transactional behavior is a later decision.


def test_state_reference_and_reset(backend, points):
    raw, shown = [points], []
    backend.set_agent_context(raw, shown)
    got_raw, got_shown = backend.get_agent_layers()
    assert got_raw is raw and got_shown is shown
    backend.visualize_layer_on_map.invoke({"layer_name": "points"})
    assert len(shown) == 1 and raw == [points]
    backend.set_agent_context([], [])
    assert backend.get_agent_layers() == ([], [])


def test_current_global_context_replacement(backend, points, polygons):
    a_raw, a_shown, b_raw, b_shown = [points], [], [polygons], []
    backend.set_agent_context(a_raw, a_shown)
    backend.set_agent_context(b_raw, b_shown)
    assert backend.get_agent_layers()[0] is b_raw
    backend.visualize_layer_on_map.invoke({"layer_name": "polygons"})
    assert len(b_shown) == 1 and not a_shown


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KD-SESSION: process-global context replaces session A with B")
def test_independent_sessions_must_not_cross_write(backend, points):
    # Deterministic interleaving of A-start, B-start, A-tool: no timing race required.
    a_shown, b_shown = [], []
    backend.set_agent_context([copy.deepcopy(points)], a_shown)
    backend.set_agent_context([copy.deepcopy(points)], b_shown)
    output = backend.visualize_layer_on_map.invoke({"layer_name": "points"})  # A's pending tool
    if "Erfolg" not in output:
        pytest.fail("Visualization failed before session isolation assertion: " + output)
    assert len(a_shown) == 1 and b_shown == []


def test_streamlit_server_layer_management(backend, points, polygons):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "frontend.py"), default_timeout=30).run()
    assert not app.exception
    app.session_state.layers = [dict(points, file_id="synthetic.csv")]
    app.session_state.processed_files = {"synthetic.csv"}
    app.session_state.show_layers = [polygons]
    app.run()
    app.button(key="del_raw_0").click().run()
    assert not app.exception and app.session_state.layers == []
    assert app.session_state.processed_files == set() and len(app.session_state.show_layers) == 1
    app.button(key="del_map_0").click().run()
    assert not app.exception and app.session_state.show_layers == []
    app.session_state.layers = [points]
    app.session_state.messages = [{"role": "user", "content": "synthetic"}]
    app.run()
    next(b for b in app.button if b.label == "🗑️ Chat & Sitzung zurücksetzen").click().run()
    assert not app.exception and app.session_state.layers == [] and app.session_state.messages == []
    assert app.session_state.agent_msg_history == []


def test_streamlit_html_export_gateway(backend, polygons, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.chdir(tmp_path)  # Existing production disk export is confined to test temp storage.
    app = AppTest.from_file(str(ROOT / "frontend.py"), default_timeout=30).run()
    app.session_state.show_layers = [polygons]
    app.run()
    next(b for b in app.button if b.label == "Karte als HTML exportieren").click().run()
    assert not app.exception
    assert "L.geoJson(" in (tmp_path / "map_export.html").read_text(encoding="utf-8")


def test_streamlit_chat_gateway_and_current_history(backend, points, monkeypatch):
    from streamlit.testing.v1 import AppTest
    install_script(backend, monkeypatch, [
        call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, 1), None])
    app = AppTest.from_file(str(ROOT / "frontend.py"), default_timeout=30).run()
    app.session_state.layers = [points]
    app.chat_input[0].set_value("show synthetic points").run()
    assert not app.exception and len(app.session_state.show_layers) == 1
    assert app.session_state.messages[-1] == {"role": "assistant", "content": "finished"}
    # Current frontend retains only user + final AI, losing intermediate tool
    # messages. Keep this characterization separate from future history design.
    history = app.session_state.agent_msg_history
    assert len(history) == 2 and isinstance(history[0], HumanMessage) and isinstance(history[1], AIMessage)
    assert not any(isinstance(m, ToolMessage) for m in history)
