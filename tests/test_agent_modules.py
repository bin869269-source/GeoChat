"""Phase 3C: real workflow/tools with explicit, deterministic model boundaries."""

from gis import geometry
from gis import statistics as gis_statistics
from agent import llm as agent_llm
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END

from agent import graph, llm
from agent import tools as tool_module
from tests.conftest import ROOT


class ScriptedModel:
    """Return a distinct bound object, so accidentally using the raw LLM fails."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.bind_count = 0
        self.seen = []
        self.registered = None

    def bind_tools(self, registered):
        self.bind_count += 1
        self.registered = registered
        return SimpleNamespace(invoke=self.bound_invoke)

    def bound_invoke(self, messages):
        self.seen.append(messages)
        return next(self.responses)


def tool_call(name, args, number):
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": args, "id": str(number), "type": "tool_call"}])


@pytest.fixture
def tools(session_agent):
    return session_agent


def test_agent_imports_and_mock_workflow_without_backend_or_key():
    code = textwrap.dedent('''
        import importlib.abc
        import os
        import socket
        import sys
        sys.path.insert(0, sys.argv[1])
        assert "DEEPSEEK_API_KEY" not in os.environ
        forbidden = {"backend", "frontend", "streamlit", "maps", "folium",
                     "langchain_openai", "openai", "dotenv"}
        attempts = []
        class BlockApplication(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in forbidden:
                    attempts.append(fullname)
                    raise AssertionError("Forbidden import: " + fullname)
        sys.meta_path.insert(0, BlockApplication())
        def block(*args, **kwargs):
            attempts.append("network")
            raise AssertionError("Network forbidden")
        socket.socket.connect = socket.socket.connect_ex = block
        socket.create_connection = block
        import agent.llm
        import agent.graph
        import agent.tools
        from langchain_core.messages import AIMessage, HumanMessage
        class Model:
            count = 0
            def bind_tools(self, tools):
                self.count += 1
                assert len(tools) == 7
                return self
            def invoke(self, messages):
                return AIMessage(content="offline done")
        model = Model()
        from core.session import SessionData
        session = SessionData()
        registered = agent.tools.create_tools(session)
        app = agent.graph.create_agent_app(model, registered, session.get_agent_layers)
        result = app.invoke({"messages":[HumanMessage(content="synthetic")]})
        assert result["messages"][-1].content == "offline done" and model.count == 1
        assert not attempts
        assert not any(n.split(".")[0] in forbidden for n in sys.modules)
        print("agent imports and workflow independent OK")
    ''')
    environment = {name: os.environ[name] for name in
                   ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    environment["PROJ_NETWORK"] = "OFF"
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "independent OK" in result.stdout


def test_session_factory_does_not_initialize_model_or_graph(session_agent):
    assert session_agent._runtime._model is None
    assert session_agent._runtime._bound_model is None
    assert session_agent._runtime._workflow is None
    assert session_agent._runtime._app is None


def test_explicit_model_config_without_reading_configuration(monkeypatch):
    import dotenv
    import langchain_openai
    calls = []
    sentinel = object()
    def construct(**kwargs):
        calls.append(kwargs)
        return sentinel
    def forbidden(*args, **kwargs):
        pytest.fail("Explicit key must not cause credential-file reads")
    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", construct)
    assert llm.create_llm("synthetic-explicit-key") is sentinel
    assert calls == [{"model": "deepseek-chat", "openai_api_key": "synthetic-explicit-key",
                      "base_url": "https://api.deepseek.com", "timeout": 60, "max_retries": 2}]


@pytest.mark.parametrize("use_environment", [True, False])
def test_credential_priority_and_repository_dotenv_location(monkeypatch, use_environment):
    import dotenv
    import langchain_openai
    import streamlit
    paths = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: paths.append(kwargs["dotenv_path"]))
    monkeypatch.setattr(streamlit, "secrets", {"DEEPSEEK_API_KEY": "synthetic-secret"})
    if use_environment:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-environment")
    else:
        monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **kwargs: kwargs)
    config = llm.create_llm()
    assert config["openai_api_key"] == ("synthetic-environment" if use_environment else "synthetic-secret")
    assert paths == [ROOT / ".env"]  # Only record the path; never open it.


def test_missing_key_is_checked_at_creation(monkeypatch):
    import streamlit
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setattr(streamlit, "secrets", {})
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY wurde nicht gefunden"):
        llm.create_llm()


@pytest.mark.parametrize("content", [
    '{"detected_type":"points","data_fields":["value"],"recommended_layer_name":"Example"}',
    '```json\n{"detected_type":"points","data_fields":["value"],"recommended_layer_name":"Example"}\n```',
])
def test_metadata_uses_explicit_model_and_preserves_json(content, monkeypatch):
    seen = []
    def invoke(messages):
        seen.append(messages)
        return AIMessage(content=content)
    monkeypatch.setattr(llm, "create_llm", lambda: pytest.fail("Injected model must be reused"))
    result = llm.smart_analyze_geodata("synthetic.csv", "synthetic sample", llm=SimpleNamespace(invoke=invoke))
    assert result == {"detected_type": "points", "data_fields": ["value"], "recommended_layer_name": "Example"}
    assert "Datei: synthetic.csv" in seen[0][1].content and "synthetic sample" in seen[0][1].content


@pytest.mark.parametrize("mode", ["no_json", "bad_json", "model_error"])
def test_metadata_fallbacks(mode):
    def invoke(messages):
        if mode == "model_error":
            raise RuntimeError("synthetic model failure")
        return AIMessage(content="no json" if mode == "no_json" else "{invalid}")
    assert llm.smart_analyze_geodata("synthetic.csv", "sample", llm=SimpleNamespace(invoke=invoke)) == {
        "detected_type": "geojson", "data_fields": [], "recommended_layer_name": "synthetic.csv"}


def test_metadata_default_model_is_created_only_on_call(monkeypatch):
    calls = []
    def factory():
        calls.append("created")
        return SimpleNamespace(invoke=lambda messages: AIMessage(content='{"recommended_layer_name":"Lazy"}'))
    monkeypatch.setattr(llm, "create_llm", factory)
    assert calls == []
    assert llm.smart_analyze_geodata("a.csv", "sample") == {"recommended_layer_name": "Lazy"}
    assert calls == ["created"]


def test_runtime_constructs_and_binds_compiles_once(monkeypatch, tools):
    model = ScriptedModel([AIMessage(content="one"), AIMessage(content="two")])
    factories, compilations = [], []
    original_compile = graph.StateGraph.compile
    def factory():
        factories.append("created")
        return model
    def compile_once(self, *args, **kwargs):
        compilations.append(self)
        return original_compile(self, *args, **kwargs)
    monkeypatch.setattr(graph.StateGraph, "compile", compile_once)
    runtime = graph.AgentRuntime(factory, tools.TOOLS, tools.get_agent_layers)
    assert not factories and model.bind_count == 0
    assert runtime.model is model and model.bind_count == 0
    assert runtime.bound_model is runtime.bound_model and model.bind_count == 1
    assert runtime.workflow is runtime.workflow and compilations == []
    app = runtime.app
    assert runtime.app is app and len(compilations) == 1
    for expected in ["one", "two"]:
        assert runtime.invoke({"messages": [HumanMessage(content="synthetic")]})["messages"][-1].content == expected
    assert factories == ["created"] and model.bind_count == 1 and len(compilations) == 1


def test_runtime_concurrent_initialization_reuses_one_app(tools):
    from concurrent.futures import ThreadPoolExecutor
    model = ScriptedModel([])
    factories = []
    def factory():
        factories.append("created")
        return model
    runtime = graph.AgentRuntime(factory, tools.TOOLS, tools.get_agent_layers)
    with ThreadPoolExecutor(max_workers=4) as pool:
        apps = list(pool.map(lambda _: runtime.app, range(8)))
    assert all(app is apps[0] for app in apps)
    assert factories == ["created"] and model.bind_count == 1


def test_graph_topology_and_tool_registry(tools):
    model = ScriptedModel([])
    app = graph.create_agent_app(model, tools.TOOLS, tools.get_agent_layers)
    view = app.get_graph()
    assert set(view.nodes) == {"__start__", "agent", "tools", "__end__"}
    assert {(e.source, e.target) for e in view.edges} == {
        ("__start__", "agent"), ("agent", "tools"), ("agent", "__end__"), ("tools", "agent")}
    assert model.bind_count == 1 and model.registered is tools.TOOLS and len(tools.TOOLS) == 7


@pytest.mark.parametrize("calls,expected", [(False, END), (True, "tools")])
def test_original_conditional_route(calls, expected):
    message = tool_call("update_map_style", {"layer_name": "a"}, 1) if calls else AIMessage(content="done")
    assert graph.should_continue({"messages": [message]}) == expected


def test_prompt_reads_current_layers_and_preserves_tool_messages(points, polygons, tools):
    seen = []
    model = SimpleNamespace(invoke=lambda messages: (seen.append(messages) or AIMessage(content="done")))
    tools.session.replace_from_legacy([points], [polygons])
    messages = [SystemMessage(content="obsolete"), HumanMessage(content="synthetic"),
                ToolMessage(content="tool evidence", tool_call_id="1")]
    graph.agent_reasoner({"messages": messages}, llm_with_tools=model, get_layers=tools.get_agent_layers)
    assert seen[0][1:] == messages[1:]
    prompt = seen[0][0].content
    assert "obsolete" not in prompt and "'points' (points)" in prompt and "'polygons' (geojson)" in prompt
    assert "value (numerisch)" in prompt and "name (text/other)" in prompt
    assert "erfinde niemals einen Feldnamen" in prompt
    assert "autonomer und professioneller GIS-Agent" in prompt and "führe die Werkzeuge nacheinander aus" in prompt
    tools.session.replace_from_legacy([], [])
    graph.agent_reasoner({"messages": messages}, llm_with_tools=model, get_layers=tools.get_agent_layers)
    assert "'points'" not in seen[1][0].content and "Keine" in seen[1][0].content


def test_single_step_graph_direct_tools(points, tools):
    shown = []
    tools.session.replace_from_legacy([points], shown)
    shown = tools.get_agent_layers()[1]
    model = ScriptedModel([tool_call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, 1),
                           AIMessage(content="done")])
    result = graph.create_agent_app(model, tools.TOOLS, tools.get_agent_layers).invoke({
        "messages": [HumanMessage(content="show points")]})
    assert len(shown) == 1 and shown[0]["type"] == "points"
    assert result["messages"][-1].content == "done"
    assert any(isinstance(m, ToolMessage) and "Erfolg" in m.content for m in result["messages"])


def test_multistep_fetch_buffer_style(polygons, monkeypatch, tools):
    shown = []
    tools.session.replace_from_legacy([], shown)
    shown = tools.get_agent_layers()[1]
    monkeypatch.setattr(tool_module.requests, "get", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: polygons["data"]))
    model = ScriptedModel([
        tool_call("fetch_city_boundary_osm", {"city_name": "Test"}, 1),
        tool_call("calculate_buffer_analysis", {"layer_name": "Stadtgrenze_Test", "distance_meters": 100}, 2),
        tool_call("update_map_style", {"layer_name": "Puffer_100m_Stadtgrenze_Test", "color": "purple"}, 3),
        AIMessage(content="done")])
    result = graph.create_agent_app(model, tools.TOOLS, tools.get_agent_layers).invoke({
        "messages": [HumanMessage(content="synthetic multistep")]})
    responses = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in responses] == ["1", "2", "3"] and all("Erfolg" in m.content for m in responses)
    assert len(shown) == 2 and shown[1]["folium_kwargs"]["color"] == "purple"
    assert len(model.seen) == 4 and model.bind_count == 1


def test_error_feedback_returns_to_model(tools):
    model = ScriptedModel([tool_call("calculate_buffer_analysis", {"layer_name": "missing"}, 1),
                           AIMessage(content="failed task explained")])
    result = graph.create_agent_app(model, tools.TOOLS, tools.get_agent_layers).invoke({
        "messages": [HumanMessage(content="synthetic missing layer")]})
    assert "nicht gefunden" in next(m.content for m in model.seen[1] if isinstance(m, ToolMessage))
    assert result["messages"][-1].content == "failed task explained"
    assert tools.get_agent_layers() == ([], [])


def test_session_agent_reuses_models_and_explicit_session(session_agent, tools):
    assert session_agent.TOOLS is tools.TOOLS and session_agent.session is tools.session
    for tool in tools.TOOLS:
        assert getattr(session_agent, tool.name) is tool
    raw, shown = [], []
    session_agent.session.replace_from_legacy(raw, shown)
    assert tools.get_agent_layers()[0] is session_agent.session.raw_view
    assert tools.get_agent_layers()[1] is session_agent.session.map_view
    assert session_agent.llm is session_agent._runtime.model
    assert session_agent.llm_with_tools is session_agent._runtime.bound_model
    assert session_agent.workflow is session_agent._runtime.workflow


def test_tools_delegate_buffer_area_density(points, polygons, monkeypatch, tools):
    calls = []
    buffer = geometry.buffer_geometries
    area = gis_statistics.calculate_area
    density = gis_statistics.calculate_density
    def spy(name, implementation):
        def invoke(*args, **kwargs):
            calls.append(name)
            return implementation(*args, **kwargs)
        return invoke
    monkeypatch.setattr(tool_module, "buffer_geometries", spy("buffer", buffer))
    monkeypatch.setattr(gis_statistics, "calculate_area", spy("area", area))
    monkeypatch.setattr(gis_statistics, "calculate_density", spy("density", density))
    tools.session.replace_from_legacy([points, polygons], [])
    assert "Erfolg" in tools.calculate_buffer_analysis.invoke({"layer_name": "points"})
    assert "Erfolg" in tools.calculate_area_and_density.invoke({"polygon_layer_name": "polygons", "point_layer_name": "points"})
    assert calls == ["buffer", "area", "density"]


def test_formal_metadata_reasoner_and_graph_reuse_one_model(session_agent, monkeypatch):
    model = ScriptedModel([AIMessage(content="direct reasoner"), AIMessage(content="graph answer")])
    metadata_calls, factories = [], []
    def metadata_invoke(messages):
        metadata_calls.append(messages)
        return AIMessage(content='{"recommended_layer_name":"Shared model"}')
    model.invoke = metadata_invoke
    def factory():
        factories.append("created")
        return model
    monkeypatch.setattr(session_agent._runtime, "_model_factory", factory)
    assert agent_llm.smart_analyze_geodata('synthetic.csv', 'sample', llm=session_agent.llm) == {
        "recommended_layer_name": "Shared model"}
    assert len(metadata_calls) == 1 and model.bind_count == 0
    state = {"messages": [HumanMessage(content="synthetic")]}
    assert graph.agent_reasoner(state, llm_with_tools=session_agent.llm_with_tools, get_layers=session_agent.get_agent_layers)["messages"][0].content == "direct reasoner"
    assert graph.should_continue({"messages": [AIMessage(content="done")]}) == END
    assert session_agent.agent_app.invoke(state)["messages"][-1].content == "graph answer"
    assert factories == ["created"] and model.bind_count == 1
    assert session_agent.llm is model and session_agent.workflow is session_agent._runtime.workflow
    with pytest.raises(AttributeError, match="unknown_compatibility_attribute"):
        getattr(session_agent, "unknown_compatibility_attribute")


@pytest.mark.parametrize("name,args,expected", [
    ("fetch_city_boundary_osm", {"city_name": "missing"}, "HTTP 503"),
    ("visualize_layer_on_map", {"layer_name": "missing"}, "nicht gefunden"),
    ("calculate_buffer_analysis", {"layer_name": "missing"}, "nicht gefunden"),
    ("calculate_area_and_density", {"polygon_layer_name": "missing"}, "nicht gefunden"),
    ("run_machine_learning_spatial_prediction", {"layer_name": "missing"}, "nicht gefunden"),
    ("update_map_style", {"layer_name": "missing"}, "nicht gefunden"),
    ("calculate_feature_distances", {"layer_name": "missing"}, "nicht gefunden"),
])
def test_all_seven_tools_bind_dispatch_and_return_feedback(name, args, expected, monkeypatch, tools):
    monkeypatch.setattr(tool_module.requests, "get", lambda *a, **k: SimpleNamespace(status_code=503))
    model = ScriptedModel([tool_call(name, args, 1), AIMessage(content="handled")])
    result = graph.create_agent_app(model, tools.TOOLS, tools.get_agent_layers).invoke({
        "messages": [HumanMessage(content="synthetic tool dispatch")]})
    feedback = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(feedback) == 1 and feedback[0].name == name
    assert feedback[0].tool_call_id == "1" and expected in feedback[0].content
    assert model.bind_count == 1 and model.registered is tools.TOOLS
    assert any(isinstance(m, ToolMessage) and expected in m.content for m in model.seen[1])
    assert result["messages"][-1].content == "handled" and tools.get_agent_layers() == ([], [])
