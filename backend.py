"""Transitional compatibility facade; application implementations live in packages.

Plain import does not read credentials or construct a model/graph. The runtime
is initialized only when model access or graph execution is requested.
"""
from agent import llm as agent_llm
from agent.graph import AgentState, AgentRuntime, agent_reasoner as _reasoner, should_continue
from agent.llm import create_llm
from agent.tools import (
    TOOLS, _SHARED_LAYER_BUS, set_agent_context, get_agent_layers,
    make_unique_layer_name, clean_gdf_for_geojson,
    fetch_city_boundary_osm, visualize_layer_on_map, calculate_buffer_analysis,
    calculate_area_and_density, run_machine_learning_spatial_prediction,
    update_map_style, calculate_feature_distances,
    requests,
)
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

_runtime = AgentRuntime(create_llm, TOOLS, get_agent_layers)
agent_app = _runtime


def smart_analyze_geodata(filename: str, sample_data_str: str) -> dict:
    return agent_llm.smart_analyze_geodata(filename, sample_data_str, llm=_runtime.model)


def agent_reasoner(state: AgentState) -> dict:
    return _reasoner(state, llm_with_tools=_runtime.bound_model, get_layers=get_agent_layers)


def __getattr__(name):
    # Compatibility for callers of the original model/workflow attributes.
    if name == "llm":
        return _runtime.model
    if name == "llm_with_tools":
        return _runtime.bound_model
    if name == "workflow":
        return _runtime.workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
