import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


COLLECTOR = Path(__file__).parents[1] / "package/contents/code/claude-usage-collector"


class ClaudeUsageCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        self.cache_path = self.home / ".cache/plasma-ai-usage/claude-statusline.json"

    def run_collector(self, payload, previous_command=None):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        if previous_command is None:
            env.pop("AI_USAGE_PREVIOUS_STATUSLINE", None)
        else:
            env["AI_USAGE_PREVIOUS_STATUSLINE"] = previous_command
        return subprocess.run(
            [sys.executable, str(COLLECTOR)],
            input=raw,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def read_cache(self):
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    def test_writes_normalized_rate_limits_with_private_permissions(self):
        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 23, "resets_at": 1784272799},
                "seven_day": {"used_percentage": 16, "resets_at": 1784710799},
            }
        }

        result = self.run_collector(payload)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_cache()["rate_limits"], payload["rate_limits"])
        self.assertEqual(self.cache_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertIsInstance(self.read_cache()["fetched_at"], int)

    def test_malformed_json_does_not_create_cache(self):
        result = self.run_collector("{not json")

        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.cache_path.exists())

    def test_missing_rate_limits_does_not_create_cache(self):
        result = self.run_collector({"model": "claude"})

        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.cache_path.exists())

    def test_removes_unrelated_fields_from_cached_rate_limits(self):
        payload = {
            "session_id": "secret",
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 23,
                    "resets_at": 1784272799,
                    "unrelated": "discard me",
                },
                "other_window": {"used_percentage": 99},
            },
        }

        self.run_collector(payload)

        self.assertEqual(
            self.read_cache()["rate_limits"],
            {"five_hour": {"used_percentage": 23, "resets_at": 1784272799}},
        )
        self.assertEqual(set(self.read_cache()), {"fetched_at", "rate_limits"})

    def test_previous_command_receives_unchanged_payload(self):
        payload = '{"rate_limits":{"five_hour":{"used_percentage":23}},"spacing":"kept"}'
        command = "python3 -c 'import sys; print(sys.stdin.read())'"

        result = self.run_collector(payload, command)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, payload + "\n")

    def test_preserves_previous_command_stderr_and_exit_status(self):
        command = (
            f'"{sys.executable}" -c '
            "'import sys; sys.stderr.write(\"previous error\\n\"); raise SystemExit(7)'"
        )

        result = self.run_collector({"rate_limits": {}}, command)

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, "previous error\n")

    def test_rejects_invalid_percentages_and_resets(self):
        invalid_percentages = (True, -1, 101, float("nan"), float("inf"), 10 ** 1000)
        for value in invalid_percentages:
            with self.subTest(value=type(value).__name__):
                self.run_collector({"rate_limits": {"five_hour": {
                    "used_percentage": value, "resets_at": 200}}})
                self.assertFalse(self.cache_path.exists())
        for value in (True, -1, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(reset=type(value).__name__):
                self.run_collector({"rate_limits": {"five_hour": {
                    "used_percentage": 10, "resets_at": value}}})
                self.assertFalse(self.cache_path.exists())


if __name__ == "__main__":
    unittest.main()
