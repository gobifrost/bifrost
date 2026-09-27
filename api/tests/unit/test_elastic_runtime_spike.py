"""The reported latency summary uses explicit, deterministic rank semantics."""
import pytest

from scripts.elastic_runtime_spike import percentile


def test_percentiles_are_nearest_rank_without_mutating_observations():
    observations = [4.0, 1.0, 3.0, 2.0]
    assert percentile(observations, .5) == 2.0
    assert percentile(observations, .95) == 4.0
    assert observations == [4.0, 1.0, 3.0, 2.0]


@pytest.mark.parametrize("values,quantile", [([], .5), ([1.0], 0), ([1.0], 1.1)])
def test_reject_invalid_summary_inputs(values, quantile):
    with pytest.raises(ValueError):
        percentile(values, quantile)
