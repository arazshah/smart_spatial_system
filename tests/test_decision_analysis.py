"""decision_analysis: AHP weights and TOPSIS."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

import plugins.decision_analysis as module  # noqa: E402
from plugins.decision_analysis import calculate_ahp_weights, rank_topsis  # noqa: E402


def row(**props):
    return {"type": "Feature", "properties": props, "geometry": None}


# ------------------------------------------------------------------------ AHP
def test_ahp_matches_saaty_reference_example():
    matrix = [[1, 3, 5], [1 / 3, 1, 3], [1 / 5, 1 / 3, 1]]
    out = calculate_ahp_weights(["cost", "distance", "quality"], matrix)
    weights = out["weights"]
    assert weights["cost"] == pytest.approx(0.637, abs=1e-3)
    assert weights["distance"] == pytest.approx(0.258, abs=1e-3)
    assert weights["quality"] == pytest.approx(0.105, abs=1e-3)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert out["lambda_max"] == pytest.approx(3.0385, abs=1e-3)
    assert out["consistency_ratio"] == pytest.approx(0.0332, abs=1e-3) and out["consistent"] is True
    assert out["ranking"] == ["cost", "distance", "quality"]


def test_ahp_from_pairwise_judgements_matches_the_matrix_form():
    judgements = [{"a": "cost", "b": "distance", "value": 3}, {"a": "cost", "b": "quality", "value": 5},
                  {"a": "distance", "b": "quality", "value": 3}]
    pairs = calculate_ahp_weights(["cost", "distance", "quality"], judgements)
    full = calculate_ahp_weights(["cost", "distance", "quality"],
                                 [[1, 3, 5], [1 / 3, 1, 3], [1 / 5, 1 / 3, 1]])
    for name in pairs["weights"]:
        assert pairs["weights"][name] == pytest.approx(full["weights"][name], abs=1e-9)
    reverse = calculate_ahp_weights(["x", "y"], [{"a": "x", "b": "y", "value": 1 / 4}])
    assert reverse["weights"]["y"] == pytest.approx(0.8)


def test_ahp_flags_inconsistent_judgements_and_two_criteria_are_always_consistent():
    contradictory = [{"a": "a", "b": "b", "value": 9}, {"a": "b", "b": "c", "value": 9},
                     {"a": "c", "b": "a", "value": 9}]
    out = calculate_ahp_weights(["a", "b", "c"], contradictory)
    assert out["consistent"] is False and out["consistency_ratio"] > 0.5
    assert calculate_ahp_weights(["a", "b"], [[1, 5], [0.2, 1]])["consistency_ratio"] == 0.0


@pytest.mark.parametrize("criteria, comparisons", [
    (["a"], [[1]]), (["a", "a"], [[1, 1], [1, 1]]), (["a", "b"], [[1, 2], [2, 1]]),  # not reciprocal
    (["a", "b"], [[2, 1], [1, 1]]), (["a", "b", "c"], [[1, 2], [0.5, 1]]),
    (["a", "b"], [{"a": "a", "b": "b", "value": 10}]), (["a", "b"], [{"a": "a", "b": "z", "value": 2}]),
    (["a", "b", "c"], [{"a": "a", "b": "b", "value": 2}]),  # missing pairs
    (["a", "b"], [{"a": "a", "b": "b", "value": 2}, {"a": "b", "b": "a", "value": 2}]),  # duplicate pair
    ([f"c{i}" for i in range(11)], [[1] * 11] * 11), ([], []), (["a", ""], [[1, 1], [1, 1]]),
])
def test_ahp_validation(criteria, comparisons):
    with pytest.raises(ValueError):
        calculate_ahp_weights(criteria, comparisons)


# ----------------------------------------------------------------------- TOPSIS
def reference_topsis(matrix, weights, benefit):
    m = np.array(matrix, dtype=float)
    w = np.array(weights, dtype=float) / sum(weights)
    v = m / np.sqrt((m**2).sum(axis=0)) * w
    best = np.where(benefit, v.max(axis=0), v.min(axis=0))
    worst = np.where(benefit, v.min(axis=0), v.max(axis=0))
    d_best = np.sqrt(((v - best) ** 2).sum(axis=1))
    d_worst = np.sqrt(((v - worst) ** 2).sum(axis=1))
    return d_worst / (d_best + d_worst)


def test_topsis_matches_an_independent_implementation():
    matrix = [[250, 16, 12, 5], [200, 16, 8, 3], [300, 32, 16, 4], [275, 32, 8, 4], [225, 16, 16, 2]]
    data = [row(price=p, mem=m, hdd=h, look=lk) for p, m, h, lk in matrix]
    criteria = [{"field": "price", "weight": 0.2, "direction": "cost"},
                {"field": "mem", "weight": 0.3}, {"field": "hdd", "weight": 0.1},
                {"field": "look", "weight": 0.4}]
    out = rank_topsis(data, criteria)
    expected = reference_topsis(matrix, [0.2, 0.3, 0.1, 0.4], np.array([False, True, True, True]))
    scores = [f["properties"]["topsis_score"] for f in out.features]
    assert scores == pytest.approx(list(expected), abs=1e-12)
    ranks = [f["properties"]["topsis_rank"] for f in out.features]
    assert ranks == [int(r) for r in (-expected).argsort().argsort() + 1]
    assert out.metadata["ideal"]["price"] < out.metadata["anti_ideal"]["price"]  # cost: low is ideal


def test_topsis_ties_missing_values_and_directions():
    data = [row(a=1, b="٥"), row(a=1, b=5), row(a=2, b=1), row(a=None, b=9), row(a="x", b=9)]
    out = rank_topsis(data, [{"field": "a", "direction": "cost"}, {"field": "b"}])
    props = [f["properties"] for f in out.features]
    assert props[0]["topsis_score"] == pytest.approx(props[1]["topsis_score"])
    assert props[0]["topsis_rank"] == props[1]["topsis_rank"] == 1 and props[2]["topsis_rank"] == 3
    assert props[3]["topsis_score"] is None and props[4]["topsis_rank"] is None
    assert out.metadata["skipped_features"] == 2
    with pytest.raises(ValueError, match="Feature 3"):
        rank_topsis(data, [{"field": "a"}], missing="raise")


@pytest.mark.parametrize("criteria", [
    [], None, [{"weight": 1}], [{"field": "a", "direction": "up"}], [{"field": "a", "weight": -1}],
    [{"field": "a", "weight": 0}], [{"field": "a"}, {"field": "a"}], [{"field": "a", "weight": "x"}],
    [{"field": f"c{i}"} for i in range(11)],
])
def test_topsis_validation(criteria):
    with pytest.raises(ValueError):
        rank_topsis([row(a=1), row(a=2)], criteria)


def test_topsis_needs_two_complete_alternatives_and_is_bounded(monkeypatch):
    with pytest.raises(ValueError, match="at least 2"):
        rank_topsis([row(a=1), row(a=None)], [{"field": "a"}])
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_features": 1})
    with pytest.raises(ValueError, match="feature count"):
        rank_topsis([row(a=1), row(a=2)], [{"field": "a"}])


def test_ahp_weights_feed_topsis():
    ahp = calculate_ahp_weights(["price", "quality"], [[1, 3], [1 / 3, 1]])
    criteria = [{"field": "price", "weight": ahp["weights"]["price"], "direction": "cost"},
                {"field": "quality", "weight": ahp["weights"]["quality"]}]
    out = rank_topsis([row(price=100, quality=5), row(price=300, quality=9)], criteria)
    assert out.features[0]["properties"]["topsis_rank"] == 1  # price weighs 3x as much as quality
