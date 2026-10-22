"""Phase 3E final entrypoints and complete offline server-side application flows."""
import ast
from copy import deepcopy
import os
import subprocess
import sys
import textwrap

from langchain_core.messages import AIMessage
import pytest

from agent.graph import SessionAgent, create_agent_session
from core.session import SessionData
from gis.data_io import parse_uploaded_file
from maps.renderer import create_layered_map, render_map_html
from tests.conftest import ROOT, upload
from tests.test_agent_modules import ScriptedModel, tool_call
from tests.test_agent_session import make_app
from tests import check_startup


MODULES = (
    "core.contracts", "core.session", "gis.data_io", "gis.geometry", "gis.statistics",
    "maps.renderer", "agent.llm", "agent.tools", "agent.graph",
)


def test_final_entrypoints_after_facade_removal():
    assert not (ROOT / "backend.py").exists()
    assert "backend" not in sys.modules
    assert SessionAgent.__module__ == "agent.graph"
    assert create_agent_session.__module__ == "agent.graph"
    a, b = create_agent_session(), create_agent_session()
    assert a.session is not b.session and a._runtime._model is b._runtime._model is None
    assert len(a.TOOLS) == len(b.TOOLS) == 7
    data = parse_uploaded_file(upload("points.json", [{"lat": 51, "lon": 9}]))
    dataset_id = a.session.add_upload("source", data["data"])
    assert "Erfolg" in a.visualize_layer_on_map.invoke({"layer_name": dataset_id, "map_type": "points"})
    assert b"L.marker(" in render_map_html(create_layered_map(a.session.map_view))
    assert b.session.datasets == {} and b.session.layers == {}


@pytest.mark.parametrize("reverse", [False, True])
def test_final_modules_in_clean_process_without_backend_or_key(reverse):
    code = textwrap.dedent('''
        import importlib
        import importlib.abc
        import os
        import socket
        import sys
        from types import SimpleNamespace
        sys.path.insert(0, sys.argv[1])
        assert "DEEPSEEK_API_KEY" not in os.environ
        forbidden = {"backend", "frontend", "streamlit", "langchain_openai", "openai", "dotenv"}
        attempts = []
        class Guard(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in forbidden:
                    attempts.append(fullname)
                    raise AssertionError("Forbidden import: " + fullname)
        sys.meta_path.insert(0, Guard())
        def no_network(*args, **kwargs):
            attempts.append("network")
            raise AssertionError("Network forbidden")
        socket.socket.connect = socket.socket.connect_ex = socket.create_connection = no_network
        for name in sys.argv[2].split(","):
            importlib.import_module(name)
        from agent.graph import create_agent_session
        from langchain_core.messages import AIMessage, HumanMessage
        class Model:
            count = 0
            def bind_tools(self, tools):
                self.count += 1
                assert len(tools) == 7
                return SimpleNamespace(invoke=lambda messages: AIMessage(content="independent answer"))
        model = Model()
        agent = create_agent_session(model_factory=lambda: model)
        assert agent._runtime._model is None
        result = agent.agent_app.invoke({"messages": [HumanMessage(content="hello")]})
        assert result["messages"][-1].content == "independent answer" and model.count == 1
        assert not attempts and "backend" not in sys.modules
        print("final modules independent OK")
    ''')
    environment = {key: os.environ[key] for key in
                   ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    environment.update(PROJ_NETWORK="OFF", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    order = tuple(reversed(MODULES)) if reverse else MODULES
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT), ",".join(order)],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "final modules independent OK" in result.stdout


def test_final_dependency_directions_and_no_import_cycles():
    modules = {f"{folder.name}.{path.stem}": path for folder in
               (ROOT / "agent", ROOT / "gis", ROOT / "maps", ROOT / "core")
               for path in folder.glob("*.py")}
    modules["frontend"] = ROOT / "frontend.py"
    allowed = {"frontend": {"agent", "gis", "maps", "core"},
               "agent": {"agent", "gis", "core", "maps"},
               "gis": {"gis"}, "maps": {"maps"}, "core": {"core"}}
    edges = {name: set() for name in modules}
    for name, path in modules.items():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                package = name.rpartition(".")[0]
                base = package if node.level else ""
                assert node.level <= 1, "Unexpected package traversal"
                target = ".".join(part for part in (base, node.module) if part)
                targets = [target] + [target + "." + alias.name for alias in node.names]
            else:
                continue
            for target in targets:
                root = target.split(".")[0]
                assert root != "backend", (name, target)
                if root in allowed:
                    assert root in allowed[name.split(".")[0]], (name, target)
                if target in modules:
                    edges[name].add(target)
    visited, active = set(), set()
    def visit(name):
        assert name not in active, f"Import cycle at {name}"
        if name in visited:
            return
        active.add(name)
        for target in edges[name]:
            visit(target)
        active.remove(name)
        visited.add(name)
    for name in modules:
        visit(name)
    assert visited == set(modules)


@pytest.mark.parametrize("extension", ["csv", "json", "geojson"])
def test_frontend_upload_chat_analysis_export_delete_reset(session_agent, monkeypatch, extension):
    import streamlit
    point_list = [{"lat": 51, "lon": 9, "properties": {"value": 3}}]
    collection = {"type": "FeatureCollection", "features": [{"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [9, 51]}, "properties": {"value": 3}}]}
    contents = {"csv": "lat,lon,name,value\n51,9,A,3\n", "json": point_list, "geojson": collection}
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [
        upload("source." + extension, contents[extension])] if k["key"] == "uploads_0" else [])
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            tool_call("visualize_layer_on_map", {"layer_name": "uploaded", "map_type": "points"}, 1).tool_calls[0],
            tool_call("calculate_buffer_analysis", {"layer_name": "uploaded_points", "distance_meters": 20}, 2).tool_calls[0],
            tool_call("update_map_style", {"layer_name": "Puffer_20m_uploaded_points", "color": "red"}, 3).tool_calls[0]]),
        AIMessage(content="analysis complete"),
        tool_call("calculate_buffer_analysis", {"layer_name": "missing"}, 4),
        AIMessage(content="missing layer explained")])
    metadata_calls, factories = [], []
    def metadata(messages):
        metadata_calls.append(messages)
        return AIMessage(content='{"detected_type":"points","data_fields":["value"],"recommended_layer_name":"uploaded"}')
    model.invoke = metadata
    def factory():
        factories.append("created")
        return model
    monkeypatch.setattr(session_agent._runtime, "_model_factory", factory)
    downloads = []
    download = streamlit.download_button
    def record_download(label, data, **kwargs):
        downloads.append((data, kwargs))
        return download(label, data, **kwargs)
    monkeypatch.setattr(streamlit, "download_button", record_download)
    app = make_app(session_agent)
    assert not app.exception and not app.error and len(session_agent.session.datasets) == 1
    assert len(metadata_calls) == 1 and model.bind_count == 0
    source_id = next(iter(session_agent.session.datasets))
    source = session_agent.session.get_dataset(source_id).data
    app.chat_input[0].set_value("show, buffer and style").run()
    assert not app.exception and app.session_state.messages[-1]["content"] == "analysis complete"
    assert len(session_agent.session.layers) == len(session_agent.session.datasets) == 2
    assert session_agent.session.map_view[1]["folium_kwargs"]["color"] == "red"
    assert session_agent.session.get_dataset(source_id).data == source
    assert factories == ["created"] and model.bind_count == 1
    next(b for b in app.button if b.label == "Karte als HTML exportieren").click().run()
    assert not app.exception and len(downloads) == 1
    payload, options = downloads[0]
    assert b"L.marker(" in payload and b"L.geoJson(" in payload and b'"color": "red"' in payload
    assert options == {"file_name": "geogpt_map.html", "mime": "text/html"}
    before = session_agent.session.legacy_layers()
    app.chat_input[0].set_value("buffer missing layer").run()
    assert not app.exception and app.session_state.messages[-1]["content"] == "missing layer explained"
    assert not session_agent.session.results[-1].success
    assert session_agent.session.legacy_layers() == before
    layer_id = next(iter(session_agent.session.layers))
    app.button(key="del_map_" + layer_id).click().run()
    assert not app.exception and len(session_agent.session.layers) == 1
    assert source_id in session_agent.session.datasets
    next(b for b in app.button if b.label == "🗑️ Chat & Sitzung zurücksetzen").click().run()
    assert not app.exception and not session_agent.session.datasets and not session_agent.session.layers
    assert session_agent.session.results == [] and app.session_state.messages == []
    assert app.session_state.processed_files == set() and app.session_state.upload_key == 1


def test_two_frontend_agents_execute_against_their_own_data(points):
    other = deepcopy(points)
    other["data"]["features"][0]["properties"]["value"] = 999
    models = [ScriptedModel([
        tool_call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, 1),
        AIMessage(content="done")]) for _ in range(2)]
    agents = [create_agent_session(SessionData.from_legacy([entry]), model_factory=lambda model=model: model)
              for entry, model in zip((points, other), models)]
    apps = [make_app(agent) for agent in agents]
    apps[0].chat_input[0].set_value("show").run()
    assert not apps[0].exception and agents[1].session.layers == {}
    apps[1].chat_input[0].set_value("show").run()
    assert not apps[1].exception and all(len(agent.session.layers) == 1 for agent in agents)
    assert [agent.session.map_view[0]["data"]["features"][0]["properties"]["value"]
            for agent in agents] == [1, 999]
    assert set(agents[0].session.datasets).isdisjoint(agents[1].session.datasets)


@pytest.mark.parametrize("log,expected", [
    ("Port 50000 is not available", None),
    ("URL: http://127.0.0.1:0", None),
    ("Ready\n  URL: http://127.0.0.1:50000\n", "http://127.0.0.1:50000"),
])
def test_startup_only_uses_a_bound_address_from_its_log(log, expected):
    assert check_startup._bound_address(log) == expected


@pytest.mark.parametrize("force_timeout", [False, True])
def test_startup_cleanup_reaps_its_real_child(force_timeout, monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    real_wait, real_kill = process.wait, process.kill
    waits, kills = [], []
    def wait(timeout=None):
        waits.append(timeout)
        if force_timeout and len(waits) == 1:
            raise subprocess.TimeoutExpired(process.args, timeout)
        return real_wait(timeout=timeout)
    def kill():
        kills.append(True)
        return real_kill()
    monkeypatch.setattr(process, "wait", wait)
    monkeypatch.setattr(process, "kill", kill)
    try:
        check_startup._stop_process(process)
        assert process.poll() is not None and waits[0] == 5
        assert bool(kills) is force_timeout
    finally:
        if process.poll() is None:
            real_kill()
        real_wait(timeout=5)
