"""Compatibility facade factory. Every agent instance explicitly owns a session."""
from agent import llm as agent_llm
from agent.graph import AgentState, AgentRuntime, agent_reasoner as _reasoner, should_continue
from agent.llm import create_llm
from agent.tools import create_tools, make_unique_layer_name, clean_gdf_for_geojson, requests
from core.session import SessionData
from gis.data_io import extract_features_to_gdf, parse_uploaded_file
from gis.geometry import buffer_geometries, pairwise_distances
from gis import statistics as gis_statistics
from maps.renderer import (
    DEFAULT_MAP_STYLE,
    create_layered_map,
    add_points_layer,
    add_heatmap_layer,
    add_choropleth_layer,
    add_geojson_layer,
    Titel_hinzufügen,
    render_map_html,
)


class SessionAgent:
    def __init__(self, session, model_factory=None):
        self.session = session
        self.TOOLS = create_tools(session)
        for registered in self.TOOLS:
            setattr(self, registered.name, registered)
        self._runtime = AgentRuntime(model_factory or create_llm, self.TOOLS, self.get_agent_layers)
        self.agent_app = self._runtime

    def get_agent_layers(self):
        return self.session.get_agent_layers()

    def set_agent_context(self, raw_layers, show_layers):
        self.session.replace_from_legacy(raw_layers, show_layers)
        return self

    def smart_analyze_geodata(self, filename, sample_data_str):
        return agent_llm.smart_analyze_geodata(filename, sample_data_str, llm=self._runtime.model)

    def agent_reasoner(self, state):
        return _reasoner(state, llm_with_tools=self._runtime.bound_model, get_layers=self.get_agent_layers)

    def __getattr__(self, name):
        if name == "llm":
            return self._runtime.model
        if name == "llm_with_tools":
            return self._runtime.bound_model
        if name == "workflow":
            return self._runtime.workflow
        if name in _STATIC_EXPORTS:
            return globals()[name]
        raise AttributeError(f"unknown compatibility attribute: {name}")


_STATIC_EXPORTS = (
    "create_agent_session", "SessionData",
    "create_llm", "should_continue", "make_unique_layer_name", "clean_gdf_for_geojson", "requests",
    "extract_features_to_gdf", "parse_uploaded_file", "buffer_geometries", "pairwise_distances", "gis_statistics",
    "DEFAULT_MAP_STYLE", "create_layered_map", "add_points_layer", "add_heatmap_layer", "add_choropleth_layer",
    "add_geojson_layer", "render_map_html",
)
_STATIC_EXPORTS += tuple(name for name in globals() if name.startswith("Titel_"))


def create_agent_session(session=None, model_factory=None):
    return SessionAgent(session if session is not None else SessionData(), model_factory)


def set_agent_context(raw_layers, show_layers):
    """Old context import now returns an explicit handle; never sets a current user."""
    return create_agent_session(SessionData.from_legacy(raw_layers, show_layers))
