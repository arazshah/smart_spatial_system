from orchestrator.planning.op_catalog import OP_CATALOG, is_supported


def test_semantic_spatial_nearest_alias_exists():
    assert is_supported("spatial_nearest")
    op = OP_CATALOG["spatial_nearest"]
    assert op.capability_name == "find_nearest_neighbors"
    assert op.input_map["source"] == "source_features"
    assert op.input_map["target"] == "target_features"
    assert op.param_map["k"] == "k"
    assert op.param_map["max_distance_m"] == "max_distance"
    assert "_nearest_distance" in op.notes


def test_top_n_alias_uses_rank_features():
    assert is_supported("top_n")
    op = OP_CATALOG["top_n"]
    assert op.capability_name == "rank_features"
    assert op.input_map["vector"] == "features"
    assert op.param_map["score_field"] == "score_field"
    assert op.param_map["descending"] == "descending"
    assert op.param_map["limit"] == "limit"


def test_spatial_join_alias_exists():
    assert is_supported("spatial_join")
    op = OP_CATALOG["spatial_join"]
    assert op.capability_name == "spatial_join_features"
    assert op.input_map["source"] == "source_features"
    assert op.input_map["target"] == "target_features"
    assert op.param_map["predicate"] == "predicate"


def test_display_summary_export_aliases_exist():
    assert OP_CATALOG["display_vector"].capability_name == "display_vector_layer"
    assert OP_CATALOG["summarize_vector"].capability_name == "summarize_vector_layer"
    assert OP_CATALOG["export_geojson"].capability_name == "export_vector_geojson"


def test_buffer_alias_exists():
    assert is_supported("buffer")
    op = OP_CATALOG["buffer"]
    assert op.capability_name == "buffer_vector_features"
    assert op.input_map["vector"] == "features"
    assert op.param_map["distance"] == "distance"
