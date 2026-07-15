import runpy
import unittest
from pathlib import Path
from unittest import mock


HELPER = Path(__file__).parents[1] / "package/contents/code/ai-usage-json"
MODULE = runpy.run_path(str(HELPER), run_name="ai_usage_json_test")


class CodexWindowIdentityTests(unittest.TestCase):
    def test_formats_whole_days(self):
        self.assertEqual(MODULE["codex_window_identity"]("primary", 10080), ("7d", "7-Day"))

    def test_formats_whole_hours(self):
        self.assertEqual(MODULE["codex_window_identity"]("primary", 300), ("5h", "5-Hour"))

    def test_formats_remaining_minutes(self):
        self.assertEqual(MODULE["codex_window_identity"]("primary", 90), ("90m", "90-Minute"))

    def test_uses_neutral_slot_identity_for_invalid_duration(self):
        for value in (None, True, 90.5, 0, -60):
            with self.subTest(value=value):
                self.assertEqual(
                    MODULE["codex_window_identity"]("primary", value),
                    ("primary", "Primary"),
                )


class CodexProviderTests(unittest.TestCase):
    def provider_for(self, rate_limits):
        provider_codex = MODULE["provider_codex"]
        with mock.patch.dict(
            provider_codex.__globals__,
            {"codex_latest_rate_limits": lambda: rate_limits},
        ):
            return provider_codex({})

    def test_uses_current_primary_duration(self):
        result = self.provider_for({
            "plan_type": "plus",
            "primary": {"used_percent": 17.0, "window_minutes": 10080, "resets_at": 123},
        })

        self.assertEqual([(w["key"], w["label"]) for w in result["windows"]], [("7d", "7-Day")])

    def test_preserves_historical_primary_secondary_order(self):
        result = self.provider_for({
            "primary": {"used_percent": 9.0, "window_minutes": 300, "resets_at": 123},
            "secondary": {"used_percent": 1.0, "window_minutes": 10080, "resets_at": 456},
        })

        self.assertEqual(
            [(w["key"], w["label"]) for w in result["windows"]],
            [("5h", "5-Hour"), ("7d", "7-Day")],
        )

    def test_uses_neutral_identity_when_duration_is_invalid(self):
        result = self.provider_for({
            "primary": {"used_percent": 3.0, "window_minutes": None, "resets_at": 123},
        })

        self.assertEqual([(w["key"], w["label"]) for w in result["windows"]], [("primary", "Primary")])

    def test_does_not_emit_window_without_real_percentage(self):
        result = self.provider_for({
            "primary": {"window_minutes": 10080, "resets_at": 123},
        })

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIsNotNone(result["error"])


if __name__ == "__main__":
    unittest.main()
