"""Phase 3D contracts, explicit session isolation and atomic publication."""
import asyncio
import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, fields
from threading import Barrier, Event

import geopandas as gpd
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from agent import graph
from agent import tools as tool_module
from agent.graph import create_agent_session
from core.contracts import Dataset, Layer, ToolResult
from core.session import SessionData
from tests.conftest import ROOT
from tests.test_agent_session import make_app


def bound(raw=(), shown=(), model_factory=None):
    return create_agent_session(SessionData.from_legacy(raw, shown), model_factory)


def call(name, args, key):
    return {"name": name, "args": args, "id": key, "type": "tool_call"}


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []
        self.bind_count = 0

    def bind_tools(self, registered):
        self.bind_count += 1
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        return next(self.responses)


def test_contract_fields_and_result_meanings():
    d = Dataset("source", {"type": "FeatureCollection", "features": []}, source="synthetic.geojson")
    layer = Layer(d.dataset_id, "display", "geojson", style={"color": "blue"})
    assert d.dataset_id != layer.layer_id and layer.dataset_id == d.dataset_id
    assert "data" not in {f.name for f in fields(Layer)}
    success = ToolResult(True, "Erfolg: synthetic", (d.dataset_id,), (layer.layer_id,), metrics={"count": 1})
    failure = ToolResult(False, "Fehler", warnings=("synthetic",), error_category="validation")
    assert asdict(success)["dataset_ids"] == (d.dataset_id,) and success.metrics["count"] == 1
    assert not failure.success and failure.error_category == "validation" and failure.warnings


def test_core_import_and_session_work_without_frameworks_or_key():
    code = textwrap.dedent('''
        import importlib.abc
        import os
        import socket
        import sys
        sys.path.insert(0, sys.argv[1])
        assert "DEEPSEEK_API_KEY" not in os.environ
        blocked = {"backend", "frontend", "agent", "streamlit", "langgraph",
                   "langchain_core", "langchain_openai", "openai", "dotenv",
                   "folium", "maps", "gis", "geopandas", "pandas"}
        attempts = []
        class Guard(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in blocked:
                    attempts.append(fullname)
                    raise AssertionError("Forbidden import: " + fullname)
        sys.meta_path.insert(0, Guard())
        def no_network(*args, **kwargs):
            attempts.append("network")
            raise AssertionError("Network forbidden")
        socket.socket.connect = socket.socket.connect_ex = socket.create_connection = no_network
        from core.contracts import Layer
        from core.session import SessionData
        a, b = SessionData(), SessionData()
        dataset_id = a.add_upload("offline", [])
        layer_id = a.add_layer(Layer(dataset_id, "display"))
        assert a.get_layer(layer_id).dataset_id == dataset_id and not b.datasets
        a.reset()
        assert not a.datasets and not a.layers and not attempts
        print("core independent OK")
    ''')
    environment = {key: os.environ[key] for key in
                   ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "core independent OK" in result.stdout


def test_native_frame_crs_and_nested_attributes_are_owned_by_session():
    from shapely.geometry import Point
    frame = gpd.GeoDataFrame({"nested": [{"tags": ["original"]}]}, geometry=[Point(500000, 5700000)], crs=32632)
    session = SessionData()
    key = session.add_upload("projected", frame)
    assert session.get_dataset(key).crs == "EPSG:32632"
    frame.loc[0, "nested"]["tags"].append("input-mutated")
    session.get_dataset(key).data.loc[0, "nested"]["tags"].append("read-mutated")
    session.datasets[key].data.loc[0, "nested"]["tags"].append("registry-mutated")
    stored = session.get_dataset(key)
    assert stored.data.loc[0, "nested"] == {"tags": ["original"]}
    assert stored.data.crs.to_epsg() == 32632
    assert stored.data.geometry.iloc[0].equals(Point(500000, 5700000))


def test_commit_rejects_foreign_layer_ids_without_partial_publication(points):
    a, b = bound([points]), bound([points])
    b.visualize_layer_on_map.invoke({"layer_name": "points"})
    raw, shown = a.session.legacy_layers()
    candidate = dict(raw[0], layer_id=next(iter(b.session.layers)))
    with pytest.raises(ValueError, match="Unknown layer"):
        a.session.commit_views(raw, [candidate], message="Erfolg: synthetic", tool_name="synthetic")
    assert not a.session.layers and len(a.session.datasets) == 1 and not a.session.results
    assert len(b.session.layers) == 1


def test_ids_names_and_defensive_payload_ownership(points):
    a, b = SessionData(), SessionData()
    first = a.add_upload("duplicate", points["data"])
    second = a.add_upload("duplicate", points["data"])
    other = b.add_upload("duplicate", points["data"])
    assert len({a.session_id, b.session_id, first, second, other}) == 5
    assert a.resolve(first)[0].dataset_id == first
    assert a.resolve(second)[0].dataset_id == second
    with pytest.raises(ValueError, match="mehrdeutig"):
        a.resolve("duplicate")
    with pytest.raises(KeyError):
        a.get_dataset(other)
    snapshot = a.get_dataset(first)
    snapshot.data["features"][0]["properties"]["value"] = 999
    points["data"]["features"].clear()
    assert len(a.get_dataset(first).data["features"]) == 3
    assert a.get_dataset(first).data["features"][0]["properties"]["value"] == 1


def test_same_dataset_multiple_layers_no_payload_duplication(points):
    agent = bound([points])
    dataset_id = next(iter(agent.session.datasets))
    for kind in ["points", "heatmap", "geojson"]:
        assert "Erfolg" in agent.visualize_layer_on_map.invoke({"layer_name": dataset_id, "map_type": kind})
    assert len(agent.session.datasets) == 1 and len(agent.session.layers) == 3
    assert {l.dataset_id for l in agent.session.layers.values()} == {dataset_id}
    assert all(not hasattr(l, "data") for l in agent.session.layers.values())
    assert "mehrdeutig" in agent.update_map_style.invoke({"layer_name": dataset_id, "color": "red"})
    assert all(l.style.get("color") != "red" for l in agent.session.layers.values())
    layer_ids = list(agent.session.layers)
    agent.session.remove_layer(layer_ids[0])
    assert dataset_id in agent.session.datasets and len(agent.session.layers) == 2


def test_style_update_retains_ids_and_isolated_snapshots(points):
    agent = bound([points])
    agent.visualize_layer_on_map.invoke({"layer_name": "points", "map_type": "points"})
    layer_id = next(iter(agent.session.layers))
    dataset_id = agent.session.get_layer(layer_id).dataset_id
    assert "Erfolg" in agent.update_map_style.invoke({"layer_name": layer_id, "color": "red"})
    assert list(agent.session.layers) == [layer_id] and list(agent.session.datasets) == [dataset_id]
    assert agent.session.get_layer(layer_id).style["color"] == "red"
    snapshot = agent.session.get_layer(layer_id)
    snapshot.style["color"] = "green"
    assert agent.session.get_layer(layer_id).style["color"] == "red"
    assert agent.session.results[-1].updated_layer_ids == (layer_id,)


def test_dataset_deletion_and_upload_detachment(points):
    agent = bound([points])
    source_id = next(iter(agent.session.datasets))
    agent.visualize_layer_on_map.invoke({"layer_name": "points"})
    layer_id = next(iter(agent.session.layers))
    with pytest.raises(ValueError, match="referenced"):
        agent.session.delete_dataset(source_id)
    agent.session.remove_upload(source_id)
    assert agent.session.raw_view == [] and len(agent.session.map_view) == 1
    assert agent.session.get_layer(layer_id).dataset_id == source_id
    agent.session.delete_dataset(source_id, cascade=True)
    assert agent.session.datasets == {} and agent.session.layers == {}


def test_derived_frame_lineage_and_cascade(points):
    agent = bound([points])
    source_id = next(iter(agent.session.datasets))
    assert "Erfolg" in agent.calculate_buffer_analysis.invoke({"layer_name": source_id, "distance_meters": 50})
    result = agent.session.results[-1]
    assert result.success and result.metrics == {"distance_meters": 50, "feature_count": 3}
    derived_id = result.dataset_ids[0]
    derived = agent.session.get_dataset(derived_id)
    assert isinstance(derived.data, gpd.GeoDataFrame)
    assert derived.parent_ids == (source_id,) and derived.source == "tool:calculate_buffer_analysis"
    assert derived.crs.upper() == "EPSG:4326"
    assert result.layer_ids[0] in agent.session.layers
    assert agent.session.get_layer(result.layer_ids[0]).dataset_id == derived_id
    with pytest.raises(ValueError, match="referenced"):
        agent.session.delete_dataset(source_id)
    agent.session.delete_dataset(source_id, cascade=True)
    assert not agent.session.datasets and not agent.session.layers


def test_duplicate_name_tool_feedback_and_id_resolution(points):
    agent = bound([points, deepcopy(points)])
    message = agent.visualize_layer_on_map.invoke({"layer_name": "points"})
    assert "mehrdeutig" in message and agent.session.map_view == []
    assert agent.session.results[-1].error_category == "ambiguous_reference"
    assert "Erfolg" in agent.visualize_layer_on_map.invoke({"layer_name": next(iter(agent.session.datasets))})


def test_dataset_id_lookup_is_independent_of_display_visibility(points, polygons):
    agent = bound([points], [polygons])
    source_id = agent.session.raw_view[0]["dataset_id"]
    assert "Erfolg" in agent.calculate_buffer_analysis.invoke({"layer_name": source_id, "distance_meters": 30})
    assert agent.session.get_dataset(agent.session.results[-1].dataset_ids[0]).parent_ids == (source_id,)
    # Legacy name lookup remains separately characterized by KD-LOOKUP.
    assert "nicht gefunden" in agent.calculate_buffer_analysis.invoke({"layer_name": "points"})


def test_cross_session_interleaving_and_foreign_id_rejection(points, polygons):
    a, b = bound([points]), bound([polygons])
    a_data, b_data = next(iter(a.session.datasets)), next(iter(b.session.datasets))
    assert "nicht gefunden" in a.visualize_layer_on_map.invoke({"layer_name": b_data})
    assert "Erfolg" in b.visualize_layer_on_map.invoke({"layer_name": b_data})
    b_layer = next(iter(b.session.layers))
    assert "nicht gefunden" in a.update_map_style.invoke({"layer_name": b_layer, "color": "red"})
    assert "Erfolg" in a.visualize_layer_on_map.invoke({"layer_name": a_data})
    assert len(a.session.layers) == 1 and len(b.session.layers) == 1
    assert b.session.get_layer(b_layer).style["color"] == "blue"
    b.session.reset()
    assert not b.session.datasets and not b.session.layers
    assert a_data in a.session.datasets and len(a.session.layers) == 1


def test_concurrent_agents_are_bound_to_their_own_sessions(points):
    gate = Barrier(2)
    class Interleaved(Model):
        def invoke(self, messages):
            if not self.seen:
                gate.wait(timeout=10)
            return super().invoke(messages)
    models = [Interleaved([AIMessage(content="", tool_calls=[call(
        "visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, "1")]),
        AIMessage(content="done")]) for _ in range(2)]
    agents = [bound([deepcopy(points)], model_factory=lambda m=m: m) for m in models]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda a: a.agent_app.invoke({"messages": [HumanMessage(content="show")]}), agents))
    assert all(r["messages"][-1].content == "done" for r in results)
    for agent, model in zip(agents, models):
        assert len(agent.session.layers) == 1
        assert {l.dataset_id for l in agent.session.layers.values()} == set(agent.session.datasets)
        assert next(iter(agent.session.datasets)) in model.seen[0][0].content
    assert set(agents[0].session.datasets).isdisjoint(agents[1].session.datasets)


def test_same_session_concurrent_tools_do_not_lose_updates(points):
    agent = bound([points])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: agent.visualize_layer_on_map.invoke({"layer_name": "points"}), range(12)))
    assert all("Erfolg" in r for r in results)
    assert len(agent.session.layers) == 12 and len(agent.session.datasets) == 1
    assert len({l.name for l in agent.session.layers.values()}) == 12


@pytest.mark.parametrize("async_mode", [False, True])
def test_model_batch_is_ordered_in_sync_and_async_paths(points, async_mode):
    model = Model([AIMessage(content="", tool_calls=[
        call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, "1"),
        call("calculate_buffer_analysis", {"layer_name": "points_points", "distance_meters": 20}, "2"),
        call("update_map_style", {"layer_name": "Puffer_20m_points_points", "color": "red"}, "3")]),
        AIMessage(content="done")])
    agent = bound([points])
    app = graph.create_agent_app(model, agent.TOOLS, agent.get_agent_layers)
    state = {"messages": [HumanMessage(content="batch") ]}
    result = asyncio.run(app.ainvoke(state, config={"max_concurrency": 8})) if async_mode else app.invoke(
        state, config={"max_concurrency": 8})
    feedback = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in feedback] == ["1", "2", "3"]
    assert all("Erfolg" in m.content for m in feedback)
    assert len(agent.session.layers) == 2 and agent.session.map_view[1]["folium_kwargs"]["color"] == "red"


def test_failed_serialization_does_not_publish_a_dataset_or_layer(points, monkeypatch):
    agent = bound([points])
    before = agent.session.legacy_layers()
    real = tool_module.buffer_geometries
    def unserializable(*args, **kwargs):
        result = real(*args, **kwargs)
        result["unserializable"] = [object()] * len(result)
        return result
    monkeypatch.setattr(tool_module, "buffer_geometries", unserializable)
    output = agent.calculate_buffer_analysis.invoke({"layer_name": "points"})
    assert "Fehler" in output and agent.session.legacy_layers() == before
    result = agent.session.results[-1]
    assert not result.success and result.error_category == "commit_or_tool_error"
    assert result.dataset_ids == result.layer_ids == ()


def test_failure_between_pending_dataset_and_layer_never_partially_commits(points, monkeypatch):
    import core.session as session_module
    agent = bound([points])
    before = agent.session.legacy_layers()
    def fail(*args, **kwargs):
        raise ValueError("synthetic layer validation failure")
    monkeypatch.setattr(session_module, "Layer", fail)
    output = agent.calculate_buffer_analysis.invoke({"layer_name": "points"})
    assert "synthetic layer validation failure" in output
    assert agent.session.legacy_layers() == before and len(agent.session.datasets) == 1
    assert not agent.session.results[-1].success


def test_slow_session_does_not_hold_another_sessions_lock(points, monkeypatch):
    a, b = bound([points]), bound([points])
    entered, release = Event(), Event()
    real = tool_module.buffer_geometries
    def wait(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return real(*args, **kwargs)
    monkeypatch.setattr(tool_module, "buffer_geometries", wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(a.calculate_buffer_analysis.invoke, {"layer_name": "points"})
        try:
            assert entered.wait(timeout=10)
            independent = pool.submit(b.visualize_layer_on_map.invoke, {"layer_name": "points"})
            assert "Erfolg" in independent.result(timeout=5)
        finally:
            release.set()
        assert "Erfolg" in slow.result(timeout=10)
    assert len(a.session.layers) == len(b.session.layers) == 1


def test_legacy_views_are_detached_and_reset_invalidates_old_ids(points):
    agent = bound([points])
    old_id = next(iter(agent.session.datasets))
    view = agent.session.raw_view[0]
    view["data"]["features"][0]["properties"]["value"] = 900
    assert agent.session.raw_view[0]["data"]["features"][0]["properties"]["value"] == 1
    agent.session.reset()
    agent.session.add_upload("points", points["data"])
    assert old_id not in agent.session.datasets
    with pytest.raises(KeyError):
        agent.session.get_dataset(old_id)


def test_frontend_upload_pipeline_and_metadata_are_session_owned(session_agent, monkeypatch):
    import streamlit
    from tests.conftest import upload
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [
        upload("synthetic.csv", "lat,lon,name,value\n51,9,A,3\n")])
    monkeypatch.setattr(session_agent.llm, "invoke", lambda messages: AIMessage(content=
        '{"detected_type":"points","data_fields":["value"],"recommended_layer_name":"Uploaded"}'))
    app = make_app(session_agent)
    assert not app.exception and len(session_agent.session.datasets) == 1
    d = next(iter(session_agent.session.datasets.values()))
    assert d.name == "Uploaded" and d.source == "synthetic.csv"
    assert d.data[0]["lat"] == 51 and d.data[0]["properties"]["value"] == 3
    assert app.session_state.processed_files == {"synthetic.csv"}
    assert session_agent.session.map_view == []


def test_frontend_creates_distinct_session_containers_and_lazy_agents(points):
    from streamlit.testing.v1 import AppTest
    a = AppTest.from_file(str(ROOT / "frontend.py"), default_timeout=30).run()
    b = AppTest.from_file(str(ROOT / "frontend.py"), default_timeout=30).run()
    assert not a.exception and not b.exception
    first, second = a.session_state.business_session, b.session_state.business_session
    assert first.session_id != second.session_id and first is not second
    assert a.session_state.session_agent.session is first
    assert b.session_state.session_agent.session is second
    assert a.session_state.session_agent._runtime._model is None
    assert b.session_state.session_agent._runtime._model is None
    first.add_upload("points", points["data"])
    a.session_state.session_agent.visualize_layer_on_map.invoke({"layer_name": "points"})
    a.run()
    b.run()
    assert not a.exception and not b.exception
    assert len(first.map_view) == 1 and second.datasets == {} and second.layers == {}


def test_frontend_reset_rekeys_uploader_to_prevent_immediate_reimport(session_agent, monkeypatch):
    import streamlit
    from tests.conftest import upload
    seen_keys = []
    def uploader(*args, **kwargs):
        seen_keys.append(kwargs["key"])
        return [upload("synthetic.csv", "lat,lon,name,value\n51,9,A,3\n")] if kwargs["key"] == "uploads_0" else []
    monkeypatch.setattr(streamlit, "file_uploader", uploader)
    monkeypatch.setattr(session_agent.llm, "invoke", lambda messages: AIMessage(content="{}"))
    app = make_app(session_agent)
    session_id = session_agent.session.session_id
    assert not app.exception and len(session_agent.session.datasets) == 1, ([e.value for e in app.error], seen_keys)
    next(button for button in app.button if button.label == "🗑️ Chat & Sitzung zurücksetzen").click().run()
    assert not app.exception and session_agent.session.session_id == session_id
    assert session_agent.session.datasets == {} and session_agent.session.layers == {} and session_agent.session.results == []
    assert app.session_state.processed_files == set() and seen_keys[-1] == "uploads_1"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KD-CSV-METADATA: numeric-only CSV sampling contains non-JSON numpy int64")
def test_numeric_only_csv_can_complete_frontend_upload(session_agent, monkeypatch):
    import streamlit
    from tests.conftest import upload
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [
        upload("numeric.csv", "lat,lon,value\n51,9,3\n")])
    monkeypatch.setattr(session_agent.llm, "invoke", lambda messages: AIMessage(content="{}"))
    app = make_app(session_agent)
    assert not app.exception and not app.error and len(session_agent.session.datasets) == 1


def test_frontend_rejects_bad_upload_without_publishing(session_agent, monkeypatch):
    import streamlit
    from tests.conftest import upload
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [upload("bad.csv", "wrong\n1\n")])
    app = make_app(session_agent)
    assert not app.exception and app.error
    assert session_agent.session.datasets == {} and app.session_state.processed_files == set()


def test_two_frontend_sessions_keep_deletion_and_reset_local(session_agent, points, polygons):
    a = session_agent
    b = bound([polygons])
    a.session.replace_from_legacy([points], [])
    first, second = make_app(a), make_app(b)
    before_b = b.session.legacy_layers()
    next(button for button in first.button if button.label == "🗑️ Chat & Sitzung zurücksetzen").click().run()
    assert not first.exception and not second.exception
    assert a.session.datasets == {} and b.session.legacy_layers() == before_b


def test_same_frame_and_style_inputs_do_not_connect_two_sessions():
    from shapely.geometry import Point
    frame = gpd.GeoDataFrame({"nested": [{"tags": ["original"]}]},
                             geometry=[Point(9, 51)], crs=4326)
    metadata = {"nested": {"tags": ["source"]}}
    dataset = Dataset("shared input", frame, metadata=metadata)
    style = {"color": "blue", "nested": {"tags": ["style"]}}
    layer = Layer(dataset.dataset_id, "display", style=style)
    a, b = SessionData(), SessionData()
    for session in (a, b):
        session.add_dataset(dataset)
        session.add_layer(layer)
    frame.loc[0, "nested"]["tags"].append("external")
    metadata["nested"]["tags"].append("external")
    style["nested"]["tags"].append("external")
    a.get_dataset(dataset.dataset_id).data.loc[0, "nested"]["tags"].append("read")
    a.get_layer(layer.layer_id).style["nested"]["tags"].append("read")
    raw, shown = a.legacy_layers()
    shown[0]["folium_kwargs"]["color"] = "red"
    a.commit_views(raw, shown, message="Erfolg: style", tool_name="review")
    for session in (a, b):
        stored = session.get_dataset(dataset.dataset_id)
        assert stored.data.loc[0, "nested"] == {"tags": ["original"]}
        assert stored.metadata == {"nested": {"tags": ["source"]}}
        assert session.get_layer(layer.layer_id).style["nested"] == {"tags": ["style"]}
    assert a.get_layer(layer.layer_id).style["color"] == "red"
    assert b.get_layer(layer.layer_id).style["color"] == "blue"
    a.reset()
    assert not a.datasets and b.get_dataset(dataset.dataset_id).data.geometry.iloc[0].equals(Point(9, 51))


@pytest.mark.parametrize("success", [True, False])
def test_tool_result_return_does_not_expose_stored_history(success):
    session = SessionData()
    if success:
        result = session.commit_views([], [], message="Erfolg: review", tool_name="review",
                                      metrics={"nested": {"count": 1}})
        result.metrics["nested"]["count"] = 999
        assert session.results[-1].metrics == {"nested": {"count": 1}}
    else:
        result = session.record_failure("synthetic failure", "validation")
        result.metrics["external"] = True
        assert session.results[-1].metrics == {}
    assert session.results[-1].success is success


def test_public_async_agent_entry_keeps_concurrent_sessions_bound(points):
    gate = Barrier(2)
    class Interleaved(Model):
        def invoke(self, messages):
            if not self.seen:
                gate.wait(timeout=10)
            return super().invoke(messages)
    models = [Interleaved([
        AIMessage(content="", tool_calls=[
            call("visualize_layer_on_map", {"layer_name": "points", "map_type": "points"}, "1"),
            call("calculate_buffer_analysis", {"layer_name": "points_points", "distance_meters": 20}, "2")]),
        AIMessage(content="done")]) for _ in range(2)]
    agents = [bound([points], model_factory=lambda model=model: model) for model in models]
    async def execute():
        return await asyncio.gather(*[
            agent.agent_app.ainvoke({"messages": [HumanMessage(content="show and buffer")]},
                                    config={"max_concurrency": 8}) for agent in agents])
    results = asyncio.run(execute())
    for agent, model, result in zip(agents, models, results):
        feedback = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert result["messages"][-1].content == "done" and model.bind_count == 1
        assert [m.tool_call_id for m in feedback] == ["1", "2"]
        assert all("Erfolg" in m.content for m in feedback)
        assert len(agent.session.datasets) == len(agent.session.layers) == 2
        assert all(layer.dataset_id in agent.session.datasets for layer in agent.session.layers.values())
    assert set(agents[0].session.datasets).isdisjoint(agents[1].session.datasets)


def test_failing_calculation_cannot_mutate_session_or_shared_input(points, monkeypatch):
    a, b = bound([points]), bound([points])
    input_before = deepcopy(points)
    before = [agent.session.legacy_layers() for agent in (a, b)]
    def mutate_and_fail(frame, distance):
        frame.loc[:, "value"] = 999
        frame.loc[:, "geometry"] = None
        raise ValueError("synthetic failure after mutating calculation input")
    monkeypatch.setattr(tool_module, "buffer_geometries", mutate_and_fail)
    output = a.calculate_buffer_analysis.invoke({"layer_name": "points"})
    assert "synthetic failure after mutating calculation input" in output
    assert points == input_before
    assert [agent.session.legacy_layers() for agent in (a, b)] == before
    assert not a.session.results[-1].success and b.session.results == []
