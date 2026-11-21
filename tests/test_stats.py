"""Pure-Python unit tests for dol8.stats.

Runs with the stdlib unittest runner (no pytest dependency):

    python tests/test_stats.py
"""

import os
import sys
import unittest

# Make the dol8 package importable when tests run from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dol8 import stats  # noqa: E402


class TestComputeStats(unittest.TestCase):
    def test_empty(self) -> None:
        s = stats.compute_stats([])
        self.assertEqual(s.n, 0)
        self.assertEqual(s.mean, 0.0)

    def test_single_sample(self) -> None:
        s = stats.compute_stats([42.0])
        self.assertEqual(s.n, 1)
        self.assertEqual(s.mean, 42.0)
        self.assertEqual(s.stddev, 0.0)
        self.assertEqual(s.cov, 0.0)

    def test_mean_and_stddev(self) -> None:
        # Classic textbook sample: mean = 5, sample stddev ~= 2.138.
        # No value is a 3*IQR outlier, so nothing should be dropped.
        s = stats.compute_stats([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        self.assertEqual(s.n, 8)
        self.assertEqual(s.dropped, [])
        self.assertAlmostEqual(s.mean, 5.0, places=10)
        self.assertAlmostEqual(s.stddev, 2.1380, places=3)

    def test_cov_zero_mean_is_safe(self) -> None:
        # mean == 0 must not divide by zero; cov should be 0.
        s = stats.compute_stats([-1.0, 1.0])
        self.assertEqual(s.cov, 0.0)


class TestOutlierRejection(unittest.TestCase):
    def test_one_outlier_dropped(self) -> None:
        # 10 stable samples plus one wild outlier. The 3*IQR fence collapses
        # to [10, 10] because Q1=Q3=10, so 1000 is outside and dropped.
        samples = [10.0] * 10 + [1000.0]
        s = stats.compute_stats(samples)
        self.assertEqual(s.n, 10)
        self.assertEqual(len(s.dropped), 1)
        self.assertAlmostEqual(s.mean, 10.0, places=5)

    def test_outlier_below_fence_dropped(self) -> None:
        # Same shape, outlier on the low side.
        samples = [100.0] * 10 + [1.0]
        s = stats.compute_stats(samples)
        self.assertEqual(s.n, 10)
        self.assertEqual(len(s.dropped), 1)

    def test_no_false_positives(self) -> None:
        # Tight cluster, no outliers.
        samples = [100.0, 100.1, 100.2, 99.9, 99.8]
        s = stats.compute_stats(samples)
        self.assertEqual(s.n, 5)
        self.assertEqual(s.dropped, [])

    def test_all_identical(self) -> None:
        s = stats.compute_stats([7.0, 7.0, 7.0, 7.0])
        self.assertEqual(s.n, 4)
        self.assertEqual(s.dropped, [])
        self.assertEqual(s.stddev, 0.0)

    def test_two_clusters_no_outliers(self) -> None:
        # Two clearly separated but legitimate clusters: the IQR fence
        # must be wide enough to keep both. This guards against the
        # fence being too aggressive on bimodal data.
        samples = [10.0, 10.0, 10.0, 20.0, 20.0, 20.0]
        s = stats.compute_stats(samples)
        self.assertEqual(s.n, 6)
        self.assertEqual(s.dropped, [])


class TestConfidenceClassification(unittest.TestCase):
    def test_exact_tier_with_high_stability_is_exact(self) -> None:
        # EXACT source (RAPL Joules) measured consistently -> EXACT.
        self.assertEqual(stats.classify_confidence(0.005, "EXACT"), "EXACT")

    def test_medium_proxy_tier_capped_at_medium(self) -> None:
        # Even with perfect stability, a MEDIUM_PROXY source cannot be
        # promoted above MEDIUM.
        self.assertEqual(
            stats.classify_confidence(0.01, "MEDIUM_PROXY"), "MEDIUM"
        )

    def test_low_stability_demotes_to_low(self) -> None:
        # Very noisy measurement on an EXACT source -> LOW (we measured
        # real Joules but the runs disagreed so wildly we can't trust
        # any single number).
        self.assertEqual(stats.classify_confidence(0.5, "EXACT"), "LOW")

    def test_low_proxy_tier_cannot_be_promoted(self) -> None:
        # LOW_PROXY tier always returns LOW regardless of stability.
        self.assertEqual(
            stats.classify_confidence(0.001, "LOW_PROXY"), "LOW"
        )

    def test_medium_band(self) -> None:
        # CoV in the MEDIUM band on a MEDIUM_PROXY tier -> MEDIUM.
        self.assertEqual(
            stats.classify_confidence(0.05, "MEDIUM_PROXY"), "MEDIUM"
        )

    def test_exact_tier_medium_stability_demoted(self) -> None:
        # EXACT source with MEDIUM stability -> MEDIUM (real Joules, but
        # noisy enough that we should be cautious).
        self.assertEqual(
            stats.classify_confidence(0.05, "EXACT"), "MEDIUM"
        )


class TestRegressionVerdict(unittest.TestCase):
    def _stats(self, mean: float, stddev: float) -> stats.SampleStats:
        cov = stddev / abs(mean) if mean else 0.0
        return stats.SampleStats(
            mean=mean,
            stddev=stddev,
            cov=cov,
            n=10,
            samples=[],
            dropped=[],
        )

    def test_clear_regression_fails(self) -> None:
        old = self._stats(100.0, 0.5)
        new = self._stats(120.0, 0.5)  # +20%, far above 5% threshold
        v = stats.regression_verdict(
            old,
            new,
            threshold_pct=5.0,
            min_confidence="MEDIUM",
            tier_confidence="EXACT",
        )
        self.assertEqual(v.gate_action, "fail")
        self.assertFalse(v.is_inconclusive)

    def test_inside_noise_band_inconclusive(self) -> None:
        old = self._stats(100.0, 5.0)
        new = self._stats(102.0, 5.0)  # +2%, combined stddev ~7.07 -> 2*sigma ~14
        v = stats.regression_verdict(
            old,
            new,
            threshold_pct=5.0,
            min_confidence="MEDIUM",
            tier_confidence="EXACT",
        )
        self.assertTrue(v.is_inconclusive)
        self.assertEqual(v.gate_action, "pass")

    def test_low_confidence_tier_forces_advisory(self) -> None:
        old = self._stats(100.0, 0.5)
        new = self._stats(150.0, 0.5)  # +50%, clearly a regression
        v = stats.regression_verdict(
            old,
            new,
            threshold_pct=5.0,
            min_confidence="MEDIUM",
            tier_confidence="LOW_PROXY",
        )
        # Tier E ceiling forces advisory, even though the delta is huge.
        self.assertEqual(v.gate_action, "advisory")
        self.assertEqual(v.confidence, "LOW")

    def test_small_improvement_passes(self) -> None:
        old = self._stats(100.0, 0.5)
        new = self._stats(99.0, 0.5)  # -1%, improvement
        v = stats.regression_verdict(
            old,
            new,
            threshold_pct=5.0,
            min_confidence="MEDIUM",
            tier_confidence="EXACT",
        )
        self.assertEqual(v.gate_action, "pass")


if __name__ == "__main__":
    unittest.main()