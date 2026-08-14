import hashlib
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


COLLECTOR = Path(__file__).parents[1] / "package/contents/code/claude-usage-collector"
HELPER = Path(__file__).parents[1] / "package/contents/code/ai-usage-json"
MODULE = runpy.run_path(str(COLLECTOR), run_name="claude_usage_collector_test")
HELPER_MODULE = runpy.run_path(str(HELPER), run_name="ai_usage_json_hash_test")


class ClaudeUsageCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        self.profile_dir = (self.home / ".claude").resolve()
        digest = hashlib.sha256(str(self.profile_dir).encode("utf-8")).hexdigest()[:12]
        self.cache_path = (
            self.home / ".cache/plasma-ai-usage" / f"claude-statusline-{digest}.json"
        )

    def run_collector(self, payload, previous_command=None):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env.pop("CLAUDE_CONFIG_DIR", None)
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

    def test_default_profile_cache_when_config_dir_is_unset(self):
        profile = MODULE["active_profile_dir"]({}, str(self.home))

        self.assertEqual(profile, str((self.home / ".claude").resolve()))

    def test_work_profile_cache_when_config_dir_is_set(self):
        work = self.home / ".claude-work"
        work.mkdir()

        profile = MODULE["active_profile_dir"](
            {"CLAUDE_CONFIG_DIR": str(work)}, str(self.home)
        )

        self.assertEqual(profile, str(work.resolve()))
        self.assertNotEqual(
            MODULE["profile_cache_path"](profile),
            MODULE["profile_cache_path"](str((self.home / ".claude").resolve())),
        )

    def test_profile_cache_path_matches_helper_canonical_hash_contract(self):
        self.profile_dir.mkdir()
        alias = self.home / "profile-alias"
        alias.symlink_to(self.profile_dir, target_is_directory=True)

        for configured_path in (self.profile_dir, alias):
            with self.subTest(configured_path=configured_path), \
                    mock.patch.dict(MODULE["profile_cache_path"].__globals__, {
                        "CACHE_DIR": str(self.home / "cache"),
                    }), mock.patch.dict(HELPER_MODULE["claude_profile_paths"].__globals__, {
                        "CACHE_DIR": str(self.home / "cache"),
                    }):
                canonical = str(configured_path.resolve())
                profile = {
                    "id": HELPER_MODULE["claude_profile_id"](str(configured_path)),
                    "canonical_path": canonical,
                }
                collector_path = MODULE["profile_cache_path"](str(configured_path))
                helper_path = HELPER_MODULE["claude_profile_paths"](
                    profile)["collector_cache"]

            self.assertEqual(collector_path, helper_path)

    def test_cache_payload_keeps_profile_caches_separate(self):
        private_cache = self.home / "cache/private.json"
        work_cache = self.home / "cache/work.json"

        MODULE["cache_payload"](
            json.dumps({"rate_limits": {"five_hour": {"used_percentage": 17}}}),
            str(private_cache),
        )
        MODULE["cache_payload"](
            json.dumps({"rate_limits": {"five_hour": {"used_percentage": 83}}}),
            str(work_cache),
        )

        self.assertEqual(
            json.loads(private_cache.read_text(encoding="utf-8"))["rate_limits"]
            ["five_hour"]["used_percentage"],
            17,
        )
        self.assertEqual(
            json.loads(work_cache.read_text(encoding="utf-8"))["rate_limits"]
            ["five_hour"]["used_percentage"],
            83,
        )

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

    def test_previous_command_receives_payload_stderr_and_exit_without_chain_env(self):
        payload = '{"rate_limits":{},"payload":"unchanged"}'
        command = (
            f'"{sys.executable}" -c '
            "'import os, sys; "
            "sys.stdout.write(sys.stdin.read()); "
            "sys.stderr.write(\"chain=%s\\n\" % "
            "os.environ.get(\"AI_USAGE_PREVIOUS_STATUSLINE\", \"unset\")); "
            "raise SystemExit(9)'"
        )

        result = self.run_collector(payload, command)

        self.assertEqual(result.returncode, 9)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(result.stderr, "chain=unset\n")

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
