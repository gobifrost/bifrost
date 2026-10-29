"""Capacity-search statistics for the open-loop benchmark."""

from __future__ import annotations

import math
import random
from statistics import NormalDist

MIN_OBSERVATIONS = 400


def _fraction(value: float, label: str) -> None:
    if not 0.0 < value < 1.0:
        raise ValueError(f"{label} must be between zero and one")


def _binomial_cdf(n: int, probability: float, value: int) -> float:
    """Return the exact binomial CDF, evaluated in log space."""
    if value < 0:
        return 0.0
    if value >= n:
        return 1.0
    if probability == 0.0:
        return 1.0
    if probability == 1.0:
        return 0.0
    terms = [
        math.log(math.comb(n, count))
        + count * math.log(probability)
        + (n - count) * math.log1p(-probability)
        for count in range(value + 1)
    ]
    maximum = max(terms)
    return math.exp(maximum) * math.fsum(math.exp(term - maximum) for term in terms)


def _binomial_first_at_least(n: int, probability: float, target: float) -> int:
    """Return the first count whose binomial CDF reaches ``target``."""
    lower = 0
    upper = n
    while lower < upper:
        middle = (lower + upper) // 2
        if _binomial_cdf(n, probability, middle) >= target:
            upper = middle
        else:
            lower = middle + 1
    return lower


def failure_aware_percentile(values: list[float | None], q: float) -> float:
    """Nearest-rank percentile with failed work ranked as infinity."""
    _fraction(q, "percentile")
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(math.inf if value is None else value for value in values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def percentile_ci(n: int, q: float, confidence: float = 0.95) -> tuple[int, int]:
    """Return 1-based distribution-free order-statistic percentile ranks.

    For samples through 2,000 this uses the exact binomial CDF.  Larger
    samples use the normal approximation, avoiding prohibitively large exact
    binomial sums while retaining the deliberately conservative reporting rule.
    """
    _fraction(q, "percentile")
    _fraction(confidence, "confidence")
    if n < 1:
        raise ValueError("percentile confidence interval requires observations")
    tail = (1.0 - confidence) / 2.0
    if n > 2000:
        deviation = math.sqrt(n * q * (1.0 - q))
        z = NormalDist().inv_cdf(1.0 - tail)
        lower = math.floor(n * q - z * deviation) + 1
        upper = math.ceil(n * q + z * deviation) + 1
        return max(1, lower), min(n, upper)

    lower_count = _binomial_first_at_least(n, q, tail) - 1
    upper_count = _binomial_first_at_least(n, q, 1.0 - tail)
    return max(1, lower_count + 1), min(n, upper_count + 1)


def percentile_with_ci(values: list[float | None], q: float, confidence: float = 0.95) -> dict:
    """Return a failure-aware percentile and its distribution-free interval."""
    value = failure_aware_percentile(values, q)
    low_rank, high_rank = percentile_ci(len(values), q, confidence)
    ordered = sorted(math.inf if item is None else item for item in values)
    low = ordered[low_rank - 1]
    high = ordered[high_rank - 1]
    if math.isinf(low) and math.isinf(high):
        half_width = 0.0
    elif math.isinf(low) or math.isinf(high):
        half_width = math.inf
    else:
        half_width = (high - low) / 2.0
    return {"value": value, "low": low, "high": high, "half_width": half_width}


def clopper_pearson_upper(failures: int, n: int, confidence: float = 0.95) -> float:
    """Return the one-sided exact binomial upper confidence bound."""
    _fraction(confidence, "confidence")
    if n < 0 or failures < 0 or failures > n:
        raise ValueError("failures must be between zero and observations")
    if n == 0 or failures == n:
        return 1.0
    target = 1.0 - confidence
    lower = failures / n
    upper = 1.0
    for _ in range(80):
        middle = (lower + upper) / 2.0
        if _binomial_cdf(n, middle, failures) > target:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def observations_needed(rate: float, confidence: float = 0.95) -> int:
    """Return the least failure-free sample size proving ``rate`` or lower."""
    _fraction(rate, "rate")
    _fraction(confidence, "confidence")
    needed = max(1, math.ceil(math.log1p(-confidence) / math.log1p(-rate)))
    while clopper_pearson_upper(0, needed, confidence) > rate:
        needed += 1
    while needed > 1 and clopper_pearson_upper(0, needed - 1, confidence) <= rate:
        needed -= 1
    return needed


def ols_slope(points: list[tuple[float, float]]) -> tuple[float, float, list[float]]:
    """Fit ``y = slope * x + intercept`` and return residuals in point order."""
    if len(points) < 2:
        raise ValueError("OLS slope requires at least two points")
    mean_x = math.fsum(point[0] for point in points) / len(points)
    mean_y = math.fsum(point[1] for point in points) / len(points)
    denominator = math.fsum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0.0:
        raise ValueError("OLS slope requires distinct timestamps")
    slope = math.fsum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    intercept = mean_y - slope * mean_x
    return slope, intercept, [y - (slope * x + intercept) for x, y in points]


def _autocorrelations(residuals: list[float], max_lag: int) -> list[float]:
    """Sample autocorrelations r_1..r_max_lag of mean-centred residuals."""
    n = len(residuals)
    mean = math.fsum(residuals) / n
    centred = [value - mean for value in residuals]
    denominator = math.fsum(value * value for value in centred)
    if denominator == 0.0:
        return [0.0] * max_lag
    return [math.fsum(centred[i] * centred[i + lag] for i in range(n - lag)) / denominator for lag in range(1, max_lag + 1)]


def _flat_top(t: float) -> float:
    t = abs(t)
    return 1.0 if t <= 0.5 else 2.0 * (1.0 - t) if t <= 1.0 else 0.0


def _politis_white_block(residuals: list[float]) -> float:
    """Circular-bootstrap block length of Politis & White (2004) with the Patton, Politis & White (2009) correction.

    The flat-top rule picks m (first lag followed by K_n insignificant
    autocorrelations at 2*sqrt(log10(n)/n)); with M = 2m the long-run
    quantities give b = (2 G^2 / D)^(1/3) n^(1/3), G = sum lambda(k/M)|k|R(k),
    D = (4/3) (sum lambda(k/M) R(k))^2.
    """
    n = len(residuals)
    run = max(5, math.ceil(math.sqrt(math.log10(n))))
    max_lag = min(n - 1, max(2 * run, math.ceil(math.sqrt(n)) + run))
    rho = _autocorrelations(residuals, max_lag)
    threshold = 2.0 * math.sqrt(math.log10(n) / n)
    m = next((lag for lag in range(0, max_lag - run + 1) if all(abs(r) < threshold for r in rho[lag:lag + run])), max_lag - run)
    bandwidth = max(1, 2 * m)
    mean = math.fsum(residuals) / n
    variance = math.fsum((value - mean) ** 2 for value in residuals) / n
    covariance = [variance] + [variance * r for r in _autocorrelations(residuals, min(n - 1, bandwidth))]
    g = 2.0 * math.fsum(_flat_top(k / bandwidth) * k * covariance[k] for k in range(1, len(covariance)))
    long_run = covariance[0] + 2.0 * math.fsum(_flat_top(k / bandwidth) * covariance[k] for k in range(1, len(covariance)))
    d = (4.0 / 3.0) * long_run ** 2
    if d <= 0.0 or g == 0.0:
        return 1.0
    return (2.0 * g * g / d) ** (1.0 / 3.0) * n ** (1.0 / 3.0)


def _delayed_dependence_lag(residuals: list[float], alpha: float = 0.05) -> int:
    """Largest lag up to n/4 whose autocorrelation is significant (0 if none).

    Each r_k is tested against Bartlett's standard error
    sqrt((1 + 2 * sum_{j<k} r_j^2) / n), which accounts for the short-lag
    dependence already present, with a Bonferroni correction over all lags.
    """
    n = len(residuals)
    lags = max(1, n // 4)
    z = NormalDist().inv_cdf(1.0 - alpha / (2.0 * lags))
    rho = _autocorrelations(residuals, lags)
    found = 0
    accumulated = 0.0
    for lag, r in enumerate(rho, start=1):
        if abs(r) > z * math.sqrt((1.0 + 2.0 * accumulated) / n):
            found = lag
        accumulated += r * r
    return found


def _block_length(residuals: list[float]) -> int:
    """Politis-White block length, lengthened to span any delayed dependence (at least 6, at most n).

    The Bonferroni scan catches strong dependence at a distant lag (e.g. a
    seasonal lag 12 behind insignificant short lags) that the flat-top
    bandwidth cannot see, without the familywise false positives of an
    uncorrected scan.
    """
    n = len(residuals)
    if n < 3:
        return n
    block = max(6, math.ceil(_politis_white_block(residuals)), 2 * _delayed_dependence_lag(residuals))
    return min(n, block)


def _bootstrap_upper(points: list[tuple[float, float]], slope: float, intercept: float, residuals: list[float],
                     block: int, seed: int, resamples: int, quantile: float) -> float:
    n = len(points)
    generator = random.Random(seed)
    slopes: list[float] = []
    for _ in range(resamples):
        sampled: list[float] = []
        while len(sampled) < n:
            start = generator.randrange(n)
            sampled.extend(residuals[(start + offset) % n] for offset in range(block))
        slopes.append(ols_slope([(x, slope * x + intercept + sampled[index]) for index, (x, _) in enumerate(points)])[0])
    return failure_aware_percentile(slopes, quantile)


def residual_block_bootstrap_upper(
    points: list[tuple[float, float]],
    seed: int,
    resamples: int = 2000,
    quantile: float = 0.95,
    min_blocks: int = 10,
) -> dict:
    """Upper slope bound from a seeded circular residual-block bootstrap, with block-length sensitivity.

    Residual blocks are added back to the fitted line at the original
    timestamps, so the tested trend is preserved. The bound is computed at
    b/2 (at least 6), b and 2b; it is conclusive only when ``n / 2b`` blocks
    still number at least ``min_blocks``. The caller compares all three
    ``uppers`` with its tolerance and treats disagreement as inconclusive.
    """
    _fraction(quantile, "quantile")
    if resamples < 1 or min_blocks < 1:
        raise ValueError("resamples and min_blocks must be positive")
    slope, intercept, residuals = ols_slope(points)
    n = len(points)
    residual_mean = math.fsum(residuals) / n
    residuals = [value - residual_mean for value in residuals]
    block = _block_length(residuals)
    lengths = (max(6, block // 2), block, 2 * block)
    uppers = [_bootstrap_upper(points, slope, intercept, residuals, length, seed, resamples, quantile) for length in lengths]
    return {
        "slope": slope,
        "upper": uppers[1],
        "uppers": dict(zip(("half", "chosen", "double"), uppers)),
        "block": block,
        "blocks": n / (2 * block),
        "conclusive": n / (2 * block) >= min_blocks,
    }


def _gate(status: str, value: object, limit: object, **detail: object) -> dict:
    """Build one capacity-gate result."""
    return {"status": status, "value": value, "limit": limit, **detail}


def _latency_gate(summary: dict, limit: float) -> dict:
    if summary["value"] > limit:
        status = "fail"
    elif summary["half_width"] > limit * 0.10:
        status = "inconclusive"
    else:
        status = "pass"
    return _gate(status, summary["value"], limit, ci=summary)


def step_verdict(
    records: list[dict],
    backlog: list[tuple[float, float]],
    window: tuple[float, float],
    offered_rate: float,
    thresholds: dict,
    seed: int,
    mix: dict[str, float],
) -> dict:
    """Apply the frozen capacity-search gates to one measured window.

    Classes are gated by their configured ``mix`` weight (>= 10 %), never by
    the sampled share; a gated class with fewer than
    ``MIN_OBSERVATIONS`` arrivals (a configured minimum may raise, never lower, it) cannot pass.
    """
    start, end = window
    if end <= start:
        raise ValueError("measurement window must have positive duration")
    if offered_rate < 0.0:
        raise ValueError("offered rate must not be negative")
    arrivals = [record for record in records if start <= record["scheduled_s"] < end]
    throughput_limit = thresholds.get("throughput_ratio", 0.995)
    completed = sum(
        record.get("outcome") == "correct"
        and record.get("completed_s") is not None
        and start <= record["completed_s"] < end
        for record in records
    )
    if arrivals:
        throughput_value = completed / len(arrivals)
        throughput = _gate("pass" if throughput_value >= throughput_limit else "fail", throughput_value, throughput_limit)
    else:
        throughput = _gate("inconclusive", None, throughput_limit)

    # Unfinished items per second; the caller sets it for the phase (§6).
    drift_limit = thresholds["drift_limit_per_s"]
    backlog_window = [(timestamp, count) for timestamp, count in backlog if start <= timestamp < end]
    if len(backlog_window) < 2:
        drift = _gate("inconclusive", None, drift_limit)
    else:
        bootstrap = residual_block_bootstrap_upper(backlog_window, seed)
        verdicts = {upper <= drift_limit for upper in bootstrap["uppers"].values()}
        if not bootstrap["conclusive"] or len(verdicts) > 1:
            drift_status = "inconclusive"
        elif verdicts == {True}:
            drift_status = "pass"
        else:
            drift_status = "fail"
        drift = _gate(drift_status, bootstrap["upper"], drift_limit, bootstrap=bootstrap)

    by_class: dict[str, list[dict]] = {}
    for record in arrivals:
        by_class.setdefault(record["class"], []).append(record)
    total_weight = sum(mix.values())
    eligible = {name: by_class.get(name, []) for name, weight in mix.items() if weight / total_weight >= 0.10}
    minimum = max(MIN_OBSERVATIONS, thresholds.get("min_observations", MIN_OBSERVATIONS))
    queue_limits = thresholds.get("queue_p95_s", {})
    e2e_limits = thresholds.get("e2e_p95_s", {})
    latency: dict[str, dict] = {}
    reliability: dict[str, dict] = {}
    failure_limit = thresholds.get("failure_upper", 0.01)
    for name, class_records in eligible.items():
        if name not in queue_limits or name not in e2e_limits:
            raise KeyError(f"missing p95 threshold for class {name}")
        queue_values = [
            record.get("queue_s") if record.get("outcome") == "correct" else None
            for record in class_records
        ]
        e2e_values = [
            record["completed_s"] - record["sent_s"]
            if record.get("outcome") == "correct" and record.get("completed_s") is not None and record.get("sent_s") is not None
            else None
            for record in class_records
        ]
        if len(class_records) < minimum:
            short = _gate("inconclusive", None, None, observations=len(class_records), minimum=minimum)
            latency[name] = {"queue_p95": short, "e2e_p95": short}
            reliability[name] = short
            continue
        latency[name] = {
            "queue_p95": _latency_gate(percentile_with_ci(queue_values, 0.95), queue_limits[name]),
            "e2e_p95": _latency_gate(percentile_with_ci(e2e_values, 0.95), e2e_limits[name]),
        }
        failures = sum(record.get("outcome") != "correct" for record in class_records)
        upper = clopper_pearson_upper(failures, len(class_records))
        reliability[name] = _gate("pass" if upper <= failure_limit else "fail", upper, failure_limit)

    faults = thresholds.get("faults", {})
    if not isinstance(faults, dict):
        raise ValueError("faults must be a count dictionary")
    tenant_faults = _gate("pass" if not any(value != 0 for value in faults.values()) else "fail", faults, 0)
    statuses = [throughput["status"], drift["status"], tenant_faults["status"]]
    statuses.extend(gate["status"] for gates in latency.values() for gate in gates.values())
    statuses.extend(gate["status"] for gate in reliability.values())
    overall = "fail" if "fail" in statuses else "inconclusive" if "inconclusive" in statuses else "pass"
    return {
        "overall": overall,
        "throughput": throughput,
        "drift": drift,
        "latency": latency,
        "reliability": reliability,
        "tenant_faults": tenant_faults,
    }


def search_next(history: list[tuple[float, str]], factor: float = 1.25, bisections: int = 2) -> float | None:
    """Choose the next geometric-ramp or bounded-bisection offered rate."""
    if factor <= 1.0:
        raise ValueError("ramp factor must exceed one")
    if bisections < 0:
        raise ValueError("bisections must not be negative")
    valid = [(rate, status) for rate, status in history if status != "invalid"]
    if not valid:
        return None
    for rate, status in valid:
        if rate <= 0.0:
            raise ValueError("rates must be positive")
        if status not in {"pass", "fail", "inconclusive"}:
            raise ValueError(f"unknown capacity result {status!r}")
    boundary_index = next((index for index, (_, status) in enumerate(valid) if status != "pass"), None)
    if boundary_index is None:
        return max(rate for rate, _ in valid) * factor
    upper = valid[boundary_index][0]
    passed = [rate for rate, status in valid[:boundary_index] if status == "pass" and rate < upper]
    if not passed:
        return None
    lower = max(passed)
    attempts = 0
    for rate, status in valid[boundary_index + 1:]:
        if lower < rate < upper:
            attempts += 1
            if status == "pass":
                lower = rate
            else:
                upper = rate
    if attempts >= bisections:
        return None
    return math.sqrt(lower * upper)
