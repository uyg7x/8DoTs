"""Statistical analysis for dol8 energy comparisons.

This module is the rigorous statistical layer the comparison engine relies
on for honest regression detection. The headline features:

  * mean / stddev / CoV computed from raw per-run samples
  * 3*IQR outlier rejection (drops runs derailed by GC pauses, OS scheduler
    hiccups, thermal spikes - the things that wreck a 3-run average).
    IQR is used instead of modified-z-score because z-score is unreliable
    for the small N (10-30) we run, and breaks entirely when MAD==0
    (which happens whenever more than half the samples are identical).
  * confidence classification (LOW / MEDIUM / HIGH / EXACT) from CoV,
    capped by the tier's intrinsic ceiling so a Tier E result can never
    be promoted above LOW no matter how stable
  * 2-sigma noise filter: if |delta| < 2 * combined_stddev the change is
    inside the noise band and is marked INCONCLUSIVE
  * gate decision: combines confidence + threshold + min_confidence policy
    so a LOW_PROXY result can only advise, never fail a CI build

Label conventions:
  - `tier_confidence` (from capabilities.py): EXACT, HIGH_PROXY,
    MEDIUM_PROXY, LOW_PROXY. Describes the *source* quality.
  - `confidence` (computed here): EXACT, HIGH, MEDIUM, LOW. Describes the
    *overall* trust in the reported number, which is the lower of the
    source quality and the measured stability.

This file replaces the previous stats.py. If other modules in your project
(cli.py, gate.py, watch.py) imported the old API, port them to the new
function names listed below, or keep both files side by side. compareJ.py
only uses the API in this file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple


# Confidence thresholds on the coefficient of variation (stddev / |mean|).
# Empirical and deliberately conservative; tighten only with calibration data.
COV_HIGH_MAX = 0.02   # <= 2% noise  -> HIGH stability
COV_MEDIUM_MAX = 0.10  # <= 10% noise -> MEDIUM stability; above -> LOW

# Unified rank ladder for confidence labels. Both the PROXY-suffixed
# tier labels and the plain measured-stability labels map onto the same
# ladder so they can be compared directly.
_CONFIDENCE_RANK = {
    "LOW": 0,
    "LOW_PROXY": 0,
    "MEDIUM": 1,
    "MEDIUM_PROXY": 1,
    "HIGH": 2,
    "HIGH_PROXY": 2,
    "EXACT": 3,
}
_RANK_TO_CONFIDENCE = {3: "EXACT", 2: "HIGH", 1: "MEDIUM", 0: "LOW"}


@dataclass
class SampleStats:
    """Per-side statistics after outlier rejection."""

    mean: float
    stddev: float
    cov: float
    n: int
    samples: List[float]
    dropped: List[float]

    def as_dict(self) -> dict:
        return {
            "mean": self.mean,
            "stddev": self.stddev,
            "cov": self.cov,
            "n": self.n,
            "samples": self.samples,
            "dropped_outliers": self.dropped,
        }


def compute_stats(samples: List[float]) -> SampleStats:
    """Compute mean/stddev/CoV with 3*IQR outlier rejection.

    The 3*IQR fence (Q1 - 3*IQR, Q3 + 3*IQR) is more conservative than the
    classic 1.5*IQR Tukey fence, which is important for the small sample
    sizes (N=10-30) we run - we want to drop only runs that are clearly
    wrecked (GC pause, thermal spike), not legitimate variance.

    When IQR==0 (more than half the samples are identical, e.g. nine 10.0
    readings plus one 1000.0) the fence collapses to the identical value
    and any deviation is correctly flagged as an outlier. This is the case
    that broke the previous MAD-based modified-z-score approach, because
    MAD==0 caused it to skip rejection entirely.
    """
    if not samples:
        return SampleStats(0.0, 0.0, 0.0, 0, [], [])

    sorted_samples = sorted(samples)
    q1, q3 = _quartiles(sorted_samples)
    iqr = q3 - q1
    lower = q1 - 3.0 * iqr
    upper = q3 + 3.0 * iqr

    kept: List[float] = []
    dropped: List[float] = []
    for x in samples:
        if x < lower or x > upper:
            dropped.append(x)
        else:
            kept.append(x)

    if not kept:
        # Pathological: every sample flagged as an outlier. Keep them all
        # rather than returning an empty result.
        kept = list(samples)
        dropped = []

    n = len(kept)
    mean = sum(kept) / n
    if n > 1:
        variance = sum((x - mean) ** 2 for x in kept) / (n - 1)
    else:
        variance = 0.0
    stddev = math.sqrt(variance)
    cov = (stddev / abs(mean)) if abs(mean) > 1e-12 else 0.0

    return SampleStats(
        mean=mean, stddev=stddev, cov=cov, n=n, samples=kept, dropped=dropped
    )


def _quartiles(sorted_vals: List[float]) -> Tuple[float, float]:
    """Linear-interpolation Q1 and Q3 (numpy-default method)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        v = sorted_vals[0]
        return v, v

    def _percentile(p: float) -> float:
        pos = p * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return sorted_vals[lo]
        frac = pos - lo
        return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac

    return _percentile(0.25), _percentile(0.75)


def classify_confidence(cov: float, tier_confidence: str) -> str:
    """Map a CoV onto a confidence label.

    The tier's intrinsic confidence is a ceiling: a Tier E (LOW_PROXY)
    result can never be promoted above LOW no matter how stable the
    samples are, because the metric itself is a weak proxy for energy.
    The one special case is an EXACT source (RAPL Joules) with a
    HIGH-stability measurement - that reports EXACT, because we measured
    real energy consistently.
    """
    if cov <= COV_HIGH_MAX:
        measured = "HIGH"
    elif cov <= COV_MEDIUM_MAX:
        measured = "MEDIUM"
    else:
        measured = "LOW"

    if tier_confidence == "EXACT" and measured == "HIGH":
        return "EXACT"

    tier_rank = _CONFIDENCE_RANK.get(tier_confidence, 0)
    measured_rank = _CONFIDENCE_RANK[measured]
    return _RANK_TO_CONFIDENCE[min(tier_rank, measured_rank)]


@dataclass
class RegressionVerdict:
    """Final verdict on whether the change is a real regression."""

    delta: float
    delta_pct: float
    combined_stddev: float
    is_inconclusive: bool  # True if delta is inside the 2*sigma noise band
    confidence: str  # LOW / MEDIUM / HIGH / EXACT
    gate_action: str  # "pass" | "fail" | "advisory"
    reason: str

    def as_dict(self) -> dict:
        return {
            "delta": self.delta,
            "delta_pct": self.delta_pct,
            "combined_stddev": self.combined_stddev,
            "is_inconclusive": self.is_inconclusive,
            "confidence": self.confidence,
            "gate_action": self.gate_action,
            "reason": self.reason,
        }


def regression_verdict(
    old: SampleStats,
    new: SampleStats,
    threshold_pct: float,
    min_confidence: str,
    tier_confidence: str,
) -> RegressionVerdict:
    """Decide whether the regression is real and whether to gate on it.

    Two-stage logic:

      1. Statistical reality. If |delta| < 2 * sqrt(std_old^2 + std_new^2)
         the change is inside the combined noise band -> INCONCLUSIVE, and
         we refuse to claim a regression even if the percentage looks big.
      2. Gate policy. If measured confidence is below `min_confidence`,
         the gate is ADVISORY (never fails CI), regardless of threshold.
         This is what prevents a Windows Tier E runner from blocking a PR
         on noise.
    """
    delta = new.mean - old.mean
    delta_pct = (delta / old.mean * 100.0) if abs(old.mean) > 1e-12 else 0.0

    combined_stddev = math.sqrt(old.stddev ** 2 + new.stddev ** 2)
    is_inconclusive = abs(delta) < 2.0 * combined_stddev

    confidence = classify_confidence(max(old.cov, new.cov), tier_confidence)

    min_rank = _CONFIDENCE_RANK.get(min_confidence, 0)
    conf_rank = _CONFIDENCE_RANK.get(confidence, 0)

    if conf_rank < min_rank:
        return RegressionVerdict(
            delta=delta,
            delta_pct=delta_pct,
            combined_stddev=combined_stddev,
            is_inconclusive=is_inconclusive,
            confidence=confidence,
            gate_action="advisory",
            reason=(
                f"confidence {confidence} below --min-confidence "
                f"{min_confidence}; advisory only, CI not blocked"
            ),
        )

    if is_inconclusive:
        return RegressionVerdict(
            delta=delta,
            delta_pct=delta_pct,
            combined_stddev=combined_stddev,
            is_inconclusive=True,
            confidence=confidence,
            gate_action="pass",
            reason=(
                f"delta {delta:+.4g} inside 2*sigma noise band "
                f"(combined_stddev={combined_stddev:.4g}); cannot claim "
                f"regression"
            ),
        )

    if delta_pct > threshold_pct:
        return RegressionVerdict(
            delta=delta,
            delta_pct=delta_pct,
            combined_stddev=combined_stddev,
            is_inconclusive=False,
            confidence=confidence,
            gate_action="fail",
            reason=(
                f"regression {delta_pct:+.2f}% exceeds threshold "
                f"{threshold_pct:.2f}%"
            ),
        )

    return RegressionVerdict(
        delta=delta,
        delta_pct=delta_pct,
        combined_stddev=combined_stddev,
        is_inconclusive=False,
        confidence=confidence,
        gate_action="pass",
        reason=(
            f"regression {delta_pct:+.2f}% within threshold "
            f"{threshold_pct:.2f}%"
        ),
    )