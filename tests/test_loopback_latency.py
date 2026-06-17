"""
Tests for the recommended_loopback_latency_ms helper.

This helper computes a safe minimum loopback latency for a given
sample rate. The recommendation scales with sample rate because
higher rates have tighter per-frame deadlines while per-frame DSP
cost is roughly constant. Without enough latency, the loopback
underruns and audio goes robotic/crackly.
"""
import unittest

from utils.pactl_runner import PactlRunner


class TestRecommendedLoopbackLatency(unittest.TestCase):
    """Tests for PactlRunner.recommended_loopback_latency_ms."""

    def test_returns_int_for_zero(self):
        """Zero / negative / unknown rate falls back to 50 ms."""
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(0), 50
        )
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(-1), 50
        )

    def test_baseline_at_48khz_is_50ms(self):
        """The formula is calibrated at 48 kHz = 50 ms."""
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(48000), 50
        )

    def test_increases_with_sample_rate(self):
        """Higher sample rates need more latency cushion (per-frame
        deadline shrinks, per-frame DSP cost is constant)."""
        rates_latencies = [
            (22050, 23),
            (44100, 46),
            (48000, 50),
            (88200, 92),
            (96000, 100),
            (192000, 200),
        ]
        prev = 0
        for rate, expected in rates_latencies:
            actual = PactlRunner.recommended_loopback_latency_ms(rate)
            self.assertEqual(
                actual, expected,
                f"At {rate} Hz expected {expected} ms, got {actual}",
            )
            # Monotonic — never decreases as rate increases
            self.assertGreaterEqual(actual, prev)
            prev = actual

    def test_clamped_to_min_5ms(self):
        """No matter how low the rate, we never go below 5 ms (could
        cause underruns on any system)."""
        # At 100 Hz: 50 * (100/48000) = 0.1 ms, clamped to 5
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(100), 5
        )
        # At 1 Hz: 50 * (1/48000) = 0.001 ms, clamped to 5
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(1), 5
        )

    def test_clamped_to_max_500ms(self):
        """No matter how high the rate, we never go above 500 ms
        (would be obvious lag to the user)."""
        # At 1 MHz: 50 * (1_000_000/48000) = 1041 ms, clamps to 500
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(1_000_000), 500
        )
        # At 384 kHz (highest "real" rate)
        # 50 * (384000/48000) = 400, no clamp needed
        self.assertEqual(
            PactlRunner.recommended_loopback_latency_ms(384000), 400
        )

    def test_return_value_is_int(self):
        """All return values are ints (for clean printf formatting)."""
        for rate in [0, 22050, 44100, 48000, 96000, 192000, 1_000_000]:
            result = PactlRunner.recommended_loopback_latency_ms(rate)
            self.assertIsInstance(result, int)


if __name__ == "__main__":
    unittest.main()
