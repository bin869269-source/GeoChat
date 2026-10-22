"""Create explicit session-bound adapters behind credential/network boundaries."""
import importlib.abc
import io
import json
import socket
import sys
from pathlib import Path

import pytest
from shapely.geometry import mapping, Point, Polygon


ROOT = Path(__file__).resolve().parents[1]


def collection(*geometries):
    return json.loads(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(g),
         "properties": {"name": f"object_{i}", "value": i + 1}}
        for i, g in enumerate(geometries)
    ]}))


def layer(name, data, kind="geojson"):
    return {"name": name, "type": kind, "data": data}


def upload(name, data):
    stream = io.StringIO(data if isinstance(data, str) else json.dumps(data))
    stream.name = name
    return stream


@pytest.fixture(autouse=True)
def no_legacy_backend_import(monkeypatch):
    """Prove runtime paths never load the removed facade, even dynamically."""
    attempts = []
    class NoBackend(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] == "backend":
                attempts.append(fullname)
                raise AssertionError("Legacy backend import is forbidden")
    assert "backend" not in sys.modules
    monkeypatch.setattr(sys, "meta_path", [NoBackend(), *sys.meta_path])
    yield
    assert not attempts, "A caught backend import still indicates an unmigrated caller"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Never read a real credential or .env; fail immediately on outbound I/O."""
    import dotenv
    import httpx
    import requests

    attempts = []
    def blocked(*args, **kwargs):
        attempts.append("outbound operation blocked")
        raise AssertionError("Network access forbidden in Phase 2 tests")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "phase2-dummy-not-a-real-key")
    monkeypatch.setenv("PROJ_NETWORK", "OFF")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    # Windows asyncio implements socketpair via loopback TCP. Permit only literal
    # loopback addresses; all HTTP clients remain blocked even on loopback.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    def local_only(original):
        def connect(sock, address):
            if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                return original(sock, address)
            return blocked()
        return connect
    monkeypatch.setattr(socket.socket, "connect", local_only(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", local_only(original_connect_ex))
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    yield
    # Business code catches broad exceptions; do not let it silently conceal an
    # accidental network attempt behind an expected error/fallback result.
    assert attempts == [], "Test attempted outbound I/O, even if business code caught the error"


@pytest.fixture
def session_agent(monkeypatch):
    from agent.graph import create_agent_session
    import langchain_openai
    import streamlit

    class OfflineLLM:
        def __init__(self, **kwargs):
            assert kwargs["openai_api_key"] == "phase2-dummy-not-a-real-key"
            assert kwargs["model"] == "deepseek-chat"
            assert kwargs["base_url"] == "https://api.deepseek.com"

        def bind_tools(self, tools):
            self.tools = tools
            return self

        def invoke(self, messages):
            raise AssertionError("Test must supply a scripted LLM response")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", OfflineLLM)
    monkeypatch.setattr(streamlit, "secrets", {})
    bound = create_agent_session()
    yield bound
    bound.session.reset()


@pytest.fixture
def points():
    return layer("points", collection(Point(9, 51), Point(9.001, 51.001),
                                      Point(9.002, 51.002)), "points")


@pytest.fixture
def polygons():
    return layer("polygons", collection(Polygon([
        (9, 51), (9.01, 51), (9.01, 51.01), (9, 51.01), (9, 51)])))


@pytest.fixture
def bind(session_agent):
    def set_layers(*raw, shown=None):
        raw_list, shown_list = list(raw), [] if shown is None else shown
        session_agent.session.replace_from_legacy(raw_list, shown_list)
        return session_agent.get_agent_layers()
    return set_layers
