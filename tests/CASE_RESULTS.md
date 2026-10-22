# Phase 2 — Per-test results

Generated from `results.xml`. Known Defect = strict pytest xfail; not Passed.

| Test | Status | Detail |
|---|---|---|
| `tests.test_agent_session::test_missing_credential_blocks_import` | Passed |  |
| `tests.test_agent_session::test_real_graph_multistep` | Passed |  |
| `tests.test_agent_session::test_graph_tool_failure_feedback` | Passed |  |
| `tests.test_agent_session::test_graph_direct_answer` | Passed |  |
| `tests.test_agent_session::test_partial_state_survives_later_llm_error` | Passed |  |
| `tests.test_agent_session::test_state_reference_and_reset` | Passed |  |
| `tests.test_agent_session::test_current_global_context_replacement` | Passed |  |
| `tests.test_agent_session::test_independent_sessions_must_not_cross_write` | Known Defect | KD-SESSION: process-global context replaces session A with B |
| `tests.test_agent_session::test_streamlit_server_layer_management` | Passed |  |
| `tests.test_agent_session::test_streamlit_html_export_gateway` | Passed |  |
| `tests.test_agent_session::test_streamlit_chat_gateway_and_current_history` | Passed |  |
| `tests.test_data_maps::test_csv` | Passed |  |
| `tests.test_data_maps::test_csv_default_name_and_empty` | Passed |  |
| `tests.test_data_maps::test_parse_errors[a.csv-x,y\n1,2\n-ValueError]` | Passed |  |
| `tests.test_data_maps::test_parse_errors[a.csv-lat,lon\nwrong,9\n-ValueError]` | Passed |  |
| `tests.test_data_maps::test_parse_errors[a.csv--ValueError]` | Passed |  |
| `tests.test_data_maps::test_parse_errors[a.json-{-JSONDecodeError]` | Passed |  |
| `tests.test_data_maps::test_parse_errors[a.txt-anything-ValueError]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom0-json]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom0-geojson]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom0-GEOJSON]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom1-json]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom1-geojson]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom1-GEOJSON]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom2-json]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom2-geojson]` | Passed |  |
| `tests.test_data_maps::test_geojson_roundtrip[geom2-GEOJSON]` | Passed |  |
| `tests.test_data_maps::test_json_point_list` | Passed |  |
| `tests.test_data_maps::test_csv_to_tool_to_html[points]` | Passed |  |
| `tests.test_data_maps::test_csv_to_tool_to_html[heatmap]` | Passed |  |
| `tests.test_data_maps::test_empty_or_unsupported_geometry_input[data0]` | Passed |  |
| `tests.test_data_maps::test_empty_or_unsupported_geometry_input[data1]` | Passed |  |
| `tests.test_data_maps::test_empty_or_unsupported_geometry_input[data2]` | Passed |  |
| `tests.test_data_maps::test_empty_or_unsupported_geometry_input[None]` | Passed |  |
| `tests.test_data_maps::test_empty_or_unsupported_geometry_input[bad]` | Passed |  |
| `tests.test_data_maps::test_json_unvalidated_shape_characterization` | Passed |  |
| `tests.test_data_maps::test_metadata_mock_and_fallback` | Passed |  |
| `tests.test_data_maps::test_map_html[points]` | Passed |  |
| `tests.test_data_maps::test_map_html[line]` | Passed |  |
| `tests.test_data_maps::test_map_html[polygon]` | Passed |  |
| `tests.test_data_maps::test_map_html[heatmap]` | Passed |  |
| `tests.test_data_maps::test_map_html[choropleth]` | Passed |  |
| `tests.test_data_maps::test_map_empty_basemap_and_view` | Passed |  |
| `tests.test_data_maps::test_style_reaches_html` | Passed |  |
| `tests.test_gis_correctness::test_crs_assignment_and_real_transform` | Passed |  |
| `tests.test_gis_correctness::test_declared_projected_crs_must_be_transformed` | Known Defect | KD-CRS: incoming CRS is overwritten with EPSG:4326 |
| `tests.test_gis_correctness::test_distance_geodesic_reference[0]` | Passed |  |
| `tests.test_gis_correctness::test_distance_geodesic_reference[51]` | Passed |  |
| `tests.test_gis_correctness::test_polygon_shortest_distance_not_centroid` | Passed |  |
| `tests.test_gis_correctness::test_intersecting_geometry_distance_zero` | Passed |  |
| `tests.test_gis_correctness::test_buffer_ground_radius[0]` | Passed |  |
| `tests.test_gis_correctness::test_buffer_ground_radius[51]` | Known Defect | KD-BUFFER: Web Mercator radius is not ground metres at 51N |
| `tests.test_gis_correctness::test_area_analytic_square` | Passed |  |
| `tests.test_gis_correctness::test_area_geodesic_reference` | Passed |  |
| `tests.test_gis_correctness::test_density_interior_boundary_exterior_and_overlap` | Passed |  |
| `tests.test_gis_correctness::test_tiny_area_retains_nonzero_area_and_density` | Known Defect | KD-TINY: area rounded to zero before density calculation |
| `tests.test_gis_correctness::test_density_uses_unrounded_area` | Known Defect | KD-DENSITY: premature area rounding biases density |
| `tests.test_gis_correctness::test_invalid_area_input_requires_error[empty]` | Known Defect | KD-GEOMETRY: invalid/empty/zero-area polygon accepted as successful area result |
| `tests.test_gis_correctness::test_invalid_area_input_requires_error[zero_area]` | Known Defect | KD-GEOMETRY: invalid/empty/zero-area polygon accepted as successful area result |
| `tests.test_gis_correctness::test_invalid_area_input_requires_error[self_intersection]` | Known Defect | KD-GEOMETRY: invalid/empty/zero-area polygon accepted as successful area result |
| `tests.test_gis_correctness::test_null_geometry_does_not_produce_distance` | Passed |  |
| `tests.test_known_defects::test_buffer_can_read_raw_when_other_layer_visible` | Known Defect | KD-LOOKUP: shown layers hide raw layers in buffer lookup |
| `tests.test_known_defects::test_missing_density_input_is_error` | Known Defect | KD-MISSING-POINTS: missing requested point layer silently becomes area-only success |
| `tests.test_known_defects::test_zero_line_weight` | Known Defect | KD-WEIGHT: if weight rejects zero |
| `tests.test_known_defects::test_single_feature_point_rendering` | Known Defect | KD-FEATURE: single Feature renders no points |
| `tests.test_known_defects::test_styled_heatmap_still_exports` | Passed |  |
| `tests.test_known_defects::test_invalid_coordinate_rejected` | Known Defect | KD-COORDINATES: CSV latitude outside valid range accepted |
| `tests.test_tools::test_registration` | Passed |  |
| `tests.test_tools::test_tool_schema[fetch_city_boundary_osm]` | Passed |  |
| `tests.test_tools::test_tool_schema[visualize_layer_on_map]` | Passed |  |
| `tests.test_tools::test_tool_schema[calculate_buffer_analysis]` | Passed |  |
| `tests.test_tools::test_tool_schema[calculate_area_and_density]` | Passed |  |
| `tests.test_tools::test_tool_schema[run_machine_learning_spatial_prediction]` | Passed |  |
| `tests.test_tools::test_tool_schema[update_map_style]` | Passed |  |
| `tests.test_tools::test_tool_schema[calculate_feature_distances]` | Passed |  |
| `tests.test_tools::test_fetch_boundary` | Passed |  |
| `tests.test_tools::test_fetch_errors[http-HTTP 503]` | Passed |  |
| `tests.test_tools::test_fetch_errors[empty-Keine Geometrie]` | Passed |  |
| `tests.test_tools::test_fetch_errors[timeout-Fehler]` | Passed |  |
| `tests.test_tools::test_fetch_errors[json-Fehler]` | Passed |  |
| `tests.test_tools::test_visualize[points]` | Passed |  |
| `tests.test_tools::test_visualize[geojson]` | Passed |  |
| `tests.test_tools::test_visualize[heatmap]` | Passed |  |
| `tests.test_tools::test_visualize[choropleth]` | Passed |  |
| `tests.test_tools::test_missing_layer_feedback[visualize_layer_on_map-args0]` | Passed |  |
| `tests.test_tools::test_missing_layer_feedback[calculate_buffer_analysis-args1]` | Passed |  |
| `tests.test_tools::test_missing_layer_feedback[calculate_area_and_density-args2]` | Passed |  |
| `tests.test_tools::test_missing_layer_feedback[run_machine_learning_spatial_prediction-args3]` | Passed |  |
| `tests.test_tools::test_missing_layer_feedback[update_map_style-args4]` | Passed |  |
| `tests.test_tools::test_missing_layer_feedback[calculate_feature_distances-args5]` | Passed |  |
| `tests.test_tools::test_buffer_regression` | Passed |  |
| `tests.test_tools::test_area_density_regression` | Passed |  |
| `tests.test_tools::test_area_only` | Passed |  |
| `tests.test_tools::test_ml_real_random_forest_deterministic` | Passed |  |
| `tests.test_tools::test_ml_errors[no_numeric]` | Passed |  |
| `tests.test_tools::test_ml_errors[missing_target]` | Passed |  |
| `tests.test_tools::test_ml_errors[polygon]` | Passed |  |
| `tests.test_tools::test_style_update` | Passed |  |
| `tests.test_tools::test_distances_regression` | Passed |  |
| `tests.test_tools::test_distance_single` | Passed |  |
| `tests.test_tools::test_empty_calculation_errors[calculate_buffer_analysis-args0]` | Passed |  |
| `tests.test_tools::test_empty_calculation_errors[calculate_area_and_density-args1]` | Passed |  |
| `tests.test_tools::test_empty_calculation_errors[run_machine_learning_spatial_prediction-args2]` | Passed |  |
| `tests.test_tools::test_empty_calculation_errors[calculate_feature_distances-args3]` | Passed |  |
