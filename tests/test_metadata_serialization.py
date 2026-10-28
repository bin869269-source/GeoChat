"""Upload metadata boundary: real numeric values, strict JSON, owned inputs."""
from copy import deepcopy
from decimal import Decimal
import json
import math

import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
from langchain_core.messages import AIMessage
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
from shapely.geometry import Point

from agent.graph import create_agent_session
from agent.llm import serialize_metadata_sample, smart_analyze_geodata
from gis.data_io import parse_uploaded_file
from tests.conftest import collection, upload
from tests.test_agent_session import make_app


def strict_loads(text):
    def invalid(token):
        pytest.fail("Non-standard JSON constant: " + token)
    return json.loads(text, parse_constant=invalid)


@pytest.mark.parametrize("value,expected", [
    (3, 3), (1.25, 1.25), (np.int32(-7), -7), (np.int64(3), 3),
    (np.int64(9007199254740993), 9007199254740993),
    (np.uint64(2**64 - 1), 2**64 - 1),
    (np.float32(.125), .125), (np.float64(1.25), 1.25),
    (np.float32(.1), .10000000149011612),
    (np.float64(1.2345678901234567), 1.2345678901234567),
    (1e-200, 1e-200), (True, True), (np.bool_(True), True),
])
def test_numeric_scalars_keep_json_value_and_type(value, expected):
    data = [{"properties": {"value": value}}]
    decoded = strict_loads(serialize_metadata_sample(data))[0]["properties"]["value"]
    assert decoded == expected and type(decoded) is type(expected)
    assert data[0]["properties"]["value"] is value


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), float("-inf"),
                                   np.float32("nan"), np.float64("inf")])
def test_missing_and_nonfinite_are_null_only_in_preview(value):
    properties = {"value": value}
    data = [{"properties": properties}]
    assert strict_loads(serialize_metadata_sample(data)) == [{"properties": {"value": None}}]
    assert data[0]["properties"] is properties and properties["value"] is value


def test_nested_fields_names_unicode_and_containers_are_unchanged():
    data = [{"name": "Punkt Ä", "properties": {"Attribut": np.int64(7), "nested": [np.float32(.25),
             {"flag": np.bool_(False), "text": "hello", "empty": None}]}}]
    before = deepcopy(data)
    assert strict_loads(serialize_metadata_sample(data)) == [{"name": "Punkt Ä", "properties": {
        "Attribut": 7, "nested": [.25, {"flag": False, "text": "hello", "empty": None}]}}]
    assert data == before and isinstance(data[0]["properties"]["Attribut"], np.int64)


def test_original_sample_sizes_and_finite_json_text_are_preserved():
    points = [{"lat": 51 + i, "lon": 9, "properties": {"value": i}} for i in range(5)]
    features = collection(*(Point(9 + i, 51) for i in range(4)))
    fallback = {"description": "Ä" * 1500}
    assert serialize_metadata_sample(points) == json.dumps(points[:3], ensure_ascii=False)
    assert serialize_metadata_sample(features) == json.dumps({
        "type": "FeatureCollection", "features": features["features"][:2]}, ensure_ascii=False)
    assert serialize_metadata_sample(fallback) == json.dumps(fallback, ensure_ascii=False)[:1000]
    assert len(points) == 5 and len(features["features"]) == 4


@pytest.mark.parametrize("value", [Decimal("1.234567890123456789"), object()])
def test_unsupported_objects_are_rejected_without_stringification(value):
    data = [{"properties": {"value": value}}]
    with pytest.raises(TypeError):
        serialize_metadata_sample(data)
    assert data[0]["properties"]["value"] is value


@pytest.mark.parametrize("geospatial", [False, True])
def test_preview_does_not_mutate_shared_dataframe_payload(geospatial):
    payload = {"count": np.int64(3), "nested": [np.float32(.125)]}
    frame = pd.DataFrame({"payload": [payload], "integer": [np.int64(7)]})
    if geospatial:
        frame = gpd.GeoDataFrame(frame, geometry=[Point(9, 51)], crs="EPSG:4326")
    frame.index = pd.Index([17], name="feature_id")
    before = deepcopy(frame)
    data = [{"properties": frame.at[17, "payload"]}]
    assert strict_loads(serialize_metadata_sample(data)) == [{"properties": {"count": 3, "nested": [.125]}}]
    (assert_geodataframe_equal if geospatial else assert_frame_equal)(frame, before)
    assert frame.at[17, "payload"] is payload
    assert isinstance(payload["count"], np.int64) and isinstance(payload["nested"][0], np.float32)


@pytest.mark.parametrize("text,expected", [
    ("lat,lon,value\n51,9,3\n", {"value": 3}),
    ("lat,lon,value\n51,9,9007199254740993\n", {"value": 9007199254740993}),
    ("lat,lon,count,value\n51.5,9.25,3,2.75\n", {"count": 3.0, "value": 2.75}),
    ("lat,lon,name,count,value\n51,9,A,3,2.75\n", {"count": 3, "value": 2.75}),
    ("lat,lon,name,value\n51,9,A,\n", {"value": None}),
    ("lat,lon,name,value\n51,9,A,inf\n", {"value": None}),
    ("lat,lon,name,value\n51,9,A,-inf\n", {"value": None}),
])
def test_real_csv_frontend_metadata_and_dataset_path(session_agent, monkeypatch, text, expected):
    import streamlit
    original = parse_uploaded_file(upload("boundary.csv", text))["data"]
    samples = []
    def metadata(messages):
        samples.append(strict_loads(messages[-1].content.split("Auszug:\n", 1)[1]))
        return AIMessage(content=json.dumps({"recommended_layer_name": "Metadata CSV",
                         "detected_type": "points", "data_fields": list(expected)}))
    monkeypatch.setattr(session_agent.llm, "invoke", metadata)
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [upload("boundary.csv", text)])
    other = create_agent_session()
    app = make_app(session_agent)
    assert not app.exception and not app.error and len(samples) == 1
    actual = samples[0][0]["properties"]
    assert actual == expected
    assert all(type(actual[k]) is type(v) for k, v in expected.items())
    assert len(session_agent.session.datasets) == 1 and session_agent.session.layers == {}
    dataset = next(iter(session_agent.session.datasets.values()))
    assert dataset.name == "Metadata CSV" and dataset.metadata["data_fields"] == list(expected)
    for key, value in original[0]["properties"].items():
        stored = dataset.data[0]["properties"][key]
        assert type(stored) is type(value)
        assert (math.isnan(stored) if isinstance(value, float) and math.isnan(value) else stored == value)
    assert app.session_state.processed_files == {"boundary.csv"}
    assert session_agent.session.results == [] and other.session.datasets == {} and other.session.layers == {}


def test_failed_metadata_serialization_does_not_publish_or_pollute_session(session_agent, monkeypatch):
    import streamlit
    session_agent.session.add_upload("existing", [{"lat": 51, "lon": 9}])
    before = session_agent.session.datasets
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [upload("bad.json", "{}")])
    monkeypatch.setattr("gis.data_io.parse_uploaded_file", lambda f: {
        "type": "points", "data": [{"properties": {"unsupported": object()}}]})
    monkeypatch.setattr(session_agent.llm, "invoke", lambda m: pytest.fail("No model call after failed serialization"))
    app = make_app(session_agent)
    assert not app.exception and app.error
    assert session_agent.session.datasets == before
    assert session_agent.session.layers == {} and session_agent.session.results == []
    assert app.session_state.processed_files == set()


def test_numeric_csv_without_key_still_requires_metadata_model(monkeypatch):
    import streamlit
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(streamlit, "secrets", {})
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: [
        upload("numeric.csv", "lat,lon,value\n51,9,3\n")])
    agent = create_agent_session()
    app = make_app(agent)
    assert not app.exception and app.error
    assert "DEEPSEEK_API_KEY wurde nicht gefunden" in app.error[0].value
    assert "not JSON serializable" not in app.error[0].value
    assert agent._runtime._model is None and agent.session.datasets == {} and agent.session.layers == {}
    assert agent.session.results == [] and app.session_state.processed_files == set()


def test_large_integer_sample_reaches_real_sdk_as_text_without_network():
    from langchain_openai import ChatOpenAI
    from langchain_openai._compat import httpx as sdk_httpx
    bodies = []

    def fake_send(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        bodies.append(strict_loads(request.content))
        return sdk_httpx.Response(200, request=request, json={
            "id": "synthetic-completion", "object": "chat.completion", "created": 0,
            "model": "deepseek-chat", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"recommended_layer_name":"Large integers"}'}}]})

    # The installed SDK selects its HTTP client via this compatibility module.
    # Supply its own MockTransport; the offline fixture still blocks sockets.
    sample = serialize_metadata_sample([{"properties": {
        "signed": np.int64(9007199254740993), "unsigned": np.uint64(2**64 - 1)}}])
    with sdk_httpx.Client(transport=sdk_httpx.MockTransport(fake_send), trust_env=False) as client:
        model = ChatOpenAI(model="deepseek-chat", base_url="https://api.deepseek.com",
                           api_key="synthetic-test-key-not-a-real-credential", http_client=client,
                           max_retries=0)
        result = smart_analyze_geodata("large.csv", sample, llm=model)
    assert result == {"recommended_layer_name": "Large integers"} and len(bodies) == 1
    content = bodies[0]["messages"][-1]["content"]
    assert type(content) is str and content == "Datei: large.csv\nAuszug:\n" + sample
    values = strict_loads(content.split("Auszug:\n", 1)[1])[0]["properties"]
    assert values == {"signed": 9007199254740993, "unsigned": 18446744073709551615}
    assert all(type(value) is int for value in values.values())
