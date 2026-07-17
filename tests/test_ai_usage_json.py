import json
import multiprocessing
import fcntl
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HELPER = Path(__file__).parents[1] / "package/contents/code/ai-usage-json"
MODULE = runpy.run_path(str(HELPER), run_name="ai_usage_json_test")


class MetadataVersionTests(unittest.TestCase):
    def test_package_version_matches_release(self):
        metadata_path = Path(__file__).parents[1] / "package/metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["KPlugin"]["Version"], "0.1.4")


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
    def provider_for(self, rate_limits, app_server=(None, "app-server unavailable"), now=100):
        provider_codex = MODULE["provider_codex"]
        with mock.patch.dict(
            provider_codex.__globals__,
            {
                "codex_app_server_rate_limits": lambda: app_server,
                "codex_latest_rate_limits": lambda: {
                    "rate_limits": rate_limits,
                    "event_timestamp": now,
                },
            },
        ), mock.patch.object(provider_codex.__globals__["time"], "time", return_value=now):
            return provider_codex({})

    def fake_server(self, response_lines, delay=0, stderr_text=""):
        directory = tempfile.TemporaryDirectory()
        script = Path(directory.name) / "fake-codex-app-server"
        script.write_text(
            "#!%s\n"
            "import json, sys, time\n"
            "requests = [json.loads(sys.stdin.readline()), json.loads(sys.stdin.readline())]\n"
            "time.sleep(%r)\n"
            "sys.stderr.write(%r)\n"
            "sys.stderr.flush()\n"
            "for response in %r:\n"
            "    print(response, flush=True)\n" % (
                sys.executable, delay, stderr_text, response_lines),
            encoding="utf-8",
        )
        os.chmod(script, 0o700)
        self.addCleanup(directory.cleanup)
        return [str(script)]

    def test_reads_rate_limits_from_app_server(self):
        command = self.fake_server([
            json.dumps({"id": 1, "result": {"userAgent": "fake"}}),
            json.dumps({"id": 2, "result": {"rateLimits": {
                "planType": "plus",
                "primary": {"usedPercent": 17, "windowDurationMins": 300,
                            "resetsAt": 1784272799},
            }}}),
        ])
        function = MODULE["codex_app_server_rate_limits"]
        with mock.patch.dict(function.__globals__, {"CODEX_APP_SERVER_COMMAND": command}):
            snapshot = function(timeout=1)

        self.assertIsInstance(snapshot, dict)
        result = self.provider_for(None, app_server=snapshot, now=100)
        self.assertEqual(result["source"], "app-server")
        self.assertEqual(result["plan"], "plus")
        self.assertEqual(result["windows"][0]["key"], "5h")
        self.assertEqual(result["windows"][0]["used_percent"], 17.0)

    def test_prefers_named_codex_rate_limit(self):
        command = self.fake_server([
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {
                "rateLimits": {"planType": "wrong"},
                "rateLimitsByLimitId": {"codex": {
                    "planType": "plus",
                    "primary": {"usedPercent": 8, "windowDurationMins": 300,
                                "resetsAt": 1784272799},
                }},
            }}),
        ])
        function = MODULE["codex_app_server_rate_limits"]
        with mock.patch.dict(function.__globals__, {"CODEX_APP_SERVER_COMMAND": command}):
            snapshot = function(timeout=1)
        self.assertEqual(snapshot["plan_type"], "plus")

    def test_rejects_malformed_app_server_response(self):
        command = self.fake_server([json.dumps({"id": 2, "result": {"rateLimits": "bad"}})])
        function = MODULE["codex_app_server_rate_limits"]
        with mock.patch.dict(function.__globals__, {"CODEX_APP_SERVER_COMMAND": command}):
            snapshot, error = function(timeout=1)
        self.assertIsNone(snapshot)
        self.assertIn("malformed", error.lower())

    def test_rejects_scalar_and_array_app_server_messages(self):
        function = MODULE["codex_app_server_rate_limits"]
        for response in ("1", "[]"):
            with self.subTest(response=response):
                command = self.fake_server([response])
                with mock.patch.dict(function.__globals__, {"CODEX_APP_SERVER_COMMAND": command}):
                    snapshot, error = function(timeout=1)
                self.assertIsNone(snapshot)
                self.assertIn("malformed", error.lower())

    def test_app_server_mapping_rejects_invalid_percentages(self):
        normalize = MODULE["codex_normalize_rate_limits"]
        invalid = (-1, 101, True, "17", float("nan"), float("inf"), 10 ** 10_000)
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.assertIsNone(normalize({"primary": {
                    "usedPercent": value, "windowDurationMins": 300, "resetsAt": 200,
                }}))

    def test_app_server_mapping_rejects_invalid_resets(self):
        normalize = MODULE["codex_normalize_rate_limits"]
        invalid = (-1, True, "200", float("nan"), float("inf"), 10 ** 10_000)
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.assertIsNone(normalize({"primary": {
                    "usedPercent": 17, "windowDurationMins": 300, "resetsAt": value,
                }}))

    def test_app_server_read_obeys_total_timeout(self):
        command = self.fake_server([], delay=1)
        function = MODULE["codex_app_server_rate_limits"]
        with mock.patch.dict(function.__globals__, {"CODEX_APP_SERVER_COMMAND": command}):
            snapshot, error = function(timeout=0.05)
        self.assertIsNone(snapshot)
        self.assertIn("timed out", error.lower())

    def test_app_server_bounds_stdout_and_stderr(self):
        function = MODULE["codex_app_server_rate_limits"]
        cases = (
            (self.fake_server(["x" * 70_000]), "stdout"),
            (self.fake_server([], stderr_text="x" * 70_000), "stderr"),
        )
        for command, stream in cases:
            with self.subTest(stream=stream), mock.patch.dict(
                    function.__globals__, {"CODEX_APP_SERVER_COMMAND": command}):
                snapshot, error = function(timeout=1)
            self.assertIsNone(snapshot)
            self.assertIn(stream, error.lower())
            self.assertIn("size limit", error.lower())

    def test_app_server_cleanup_waits_after_kill(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fake", 0.2), 0]

        MODULE["codex_reap_process"](process)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(
            process.wait.call_args_list,
            [mock.call(timeout=MODULE["CODEX_APP_SERVER_CLEANUP_TIMEOUT"]), mock.call()],
        )
        process.stdin.close.assert_called_once_with()
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_app_server_timeout_falls_back_to_fresh_session(self):
        result = self.provider_for({
            "plan_type": "plus",
            "primary": {"used_percent": 11, "window_minutes": 300, "resets_at": 200},
        }, app_server=(None, "app-server timed out"), now=100)
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "local-session")
        self.assertIn("timed out", result["error"])

    def test_session_event_older_than_900_seconds_is_unavailable(self):
        provider_codex = MODULE["provider_codex"]
        with mock.patch.dict(provider_codex.__globals__, {
            "codex_app_server_rate_limits": lambda: (None, "unavailable"),
            "codex_latest_rate_limits": lambda: {
                "event_timestamp": 99,
                "rate_limits": {"primary": {
                    "used_percent": 11, "window_minutes": 300, "resets_at": 2_000,
                }},
            },
        }), mock.patch.object(provider_codex.__globals__["time"], "time", return_value=1_000):
            result = provider_codex({})
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])

    def test_expired_session_windows_are_omitted(self):
        result = self.provider_for({
            "primary": {"used_percent": 11, "window_minutes": 300, "resets_at": 100},
            "secondary": {"used_percent": 22, "window_minutes": 10080, "resets_at": 200},
        }, now=100)
        self.assertEqual([item["key"] for item in result["windows"]], ["7d"])

    def test_session_fallback_rejects_invalid_percentages_without_crashing(self):
        invalid = (-1, 101, True, "17", float("nan"), float("inf"), 10 ** 10_000)
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                result = self.provider_for({"primary": {
                    "used_percent": value, "window_minutes": 300, "resets_at": 200,
                }})
                self.assertFalse(result["available"])
                self.assertEqual(result["windows"], [])

    def test_session_fallback_rejects_invalid_resets_without_crashing(self):
        invalid = (-1, True, "200", float("nan"), float("inf"), 10 ** 10_000)
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                result = self.provider_for({"primary": {
                    "used_percent": 17, "window_minutes": 300, "resets_at": value,
                }})
                self.assertFalse(result["available"])
                self.assertEqual(result["windows"], [])

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


class ClaudeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.settings = self.home / ".claude/settings.json"
        self.settings.parent.mkdir(parents=True)
        self.state = self.home / ".cache/plasma-ai-usage/claude-integration.json"
        self.collector_cache = self.home / ".cache/plasma-ai-usage/claude-statusline.json"
        self.oauth_cache = self.home / ".cache/plasma-ai-usage/claude-usage.json"
        self.paths = {
            "CLAUDE_SETTINGS": str(self.settings),
            "CLAUDE_INTEGRATION_STATE": str(self.state),
            "CLAUDE_STATUSLINE_CACHE": str(self.collector_cache),
            "CLAUDE_CACHE": str(self.oauth_cache),
            "CACHE_DIR": str(self.state.parent),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def claude_found(self):
        return mock.patch.dict(
            MODULE["claude_integration_status"].__globals__,
            {**self.paths, "claude_version": lambda: "2.1.80 (Claude Code)"},
        )

    def test_status_reports_not_configured(self):
        self.settings.write_text("{}", encoding="utf-8")
        with self.claude_found():
            result = MODULE["claude_integration_status"]()
        self.assertEqual(result["state"], "not-configured")
        self.assertTrue(result["can_setup"])

    def test_missing_claude_cannot_be_set_up(self):
        with mock.patch.dict(
            MODULE["claude_integration_status"].__globals__,
            {**self.paths, "claude_version": lambda: None},
        ):
            result = MODULE["claude_integration_status"]()
        self.assertFalse(result["can_setup"])

    def test_installed_claude_without_settings_shows_sign_in_guidance(self):
        with self.claude_found():
            result = MODULE["claude_integration_status"]()
        self.assertEqual(result["state"], "not-configured")
        self.assertFalse(result["can_setup"])
        self.assertEqual(result["message"], "Claude Code must be installed and signed in first.")

    def test_malformed_existing_settings_is_an_error(self):
        self.settings.write_text("{bad", encoding="utf-8")
        with self.claude_found():
            result = MODULE["claude_integration_status"]()
        self.assertEqual(result["state"], "error")

    def test_version_support_starts_at_2_1_80(self):
        self.settings.write_text("{}", encoding="utf-8")
        cases = (
            ("Claude Code 2.1.79", False),
            ("2.1.80 (Claude Code)", True),
            ("Claude Code v3.0.1", True),
            ("Claude Code development build", False),
        )
        for output, supported in cases:
            with self.subTest(output=output), mock.patch.dict(
                MODULE["claude_integration_status"].__globals__,
                {**self.paths, "claude_version": lambda output=output: output},
            ):
                result = MODULE["claude_integration_status"]()
            self.assertEqual(result["can_setup"], supported)
            self.assertIn("support", result["message"].lower())

    def test_setup_preserves_options_and_remove_restores_previous_value(self):
        previous = {"type": "command", "command": "printf '%s' ok", "padding": 3}
        self.settings.write_text(json.dumps({"theme": "dark", "statusLine": previous}), encoding="utf-8")
        with self.claude_found():
            setup = MODULE["claude_integration_setup"]()
            installed = json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]
            managed_command = json.loads(self.state.read_text(encoding="utf-8"))["managed_command"]
            self.assertTrue(setup["ok"])
            self.assertEqual(installed["command"], managed_command)
            self.assertEqual(installed["type"], "command")
            self.assertEqual(installed["padding"], 3)
            removed = MODULE["claude_integration_remove"]()
        self.assertTrue(removed["ok"])
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"], previous)

    def test_setup_is_idempotent(self):
        previous = {"type": "command", "command": "old", "padding": 2}
        self.settings.write_text(json.dumps({"statusLine": previous}), encoding="utf-8")
        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"]()["ok"])
            first_state = self.state.read_bytes()
            self.assertTrue(MODULE["claude_integration_setup"]()["ok"])
        self.assertEqual(self.state.read_bytes(), first_state)
        self.assertEqual(json.loads(first_state)["previous_statusline"], previous)

    def test_remove_refuses_user_modified_command(self):
        self.settings.write_text(json.dumps({"statusLine": {"command": "old", "padding": 1}}), encoding="utf-8")
        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"]()["ok"])
            changed = json.loads(self.settings.read_text(encoding="utf-8"))
            changed["statusLine"]["command"] = "user replacement"
            self.settings.write_text(json.dumps(changed), encoding="utf-8")
            before = self.settings.read_bytes()
            result = MODULE["claude_integration_remove"]()
        self.assertFalse(result["ok"])
        self.assertEqual(self.settings.read_bytes(), before)

    def test_remove_can_be_retried_after_state_unlink_failure(self):
        previous = {"type": "command", "command": "old", "padding": 1}
        self.settings.write_text(json.dumps({"statusLine": previous}), encoding="utf-8")
        real_unlink = MODULE["os"].unlink
        failed = False

        def fail_state_unlink_once(path):
            nonlocal failed
            if path == str(self.state) and not failed:
                failed = True
                raise OSError("simulated unlink failure")
            return real_unlink(path)

        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"]()["ok"])
            with mock.patch.object(MODULE["os"], "unlink", side_effect=fail_state_unlink_once):
                first = MODULE["claude_integration_remove"]()
            self.assertFalse(first["ok"])
            self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"], previous)
            self.assertTrue(self.state.exists())
            retry = MODULE["claude_integration_remove"]()
        self.assertTrue(retry["ok"])
        self.assertFalse(self.state.exists())


class ClaudeFreshnessTests(unittest.TestCase):
    def cfg(self):
        return {
            "claude_token": None,
            "claude_token_file": None,
            "claude_local_fallback": False,
            "claude_extra_usage": False,
            "claude_cap_5h": None,
            "claude_cap_7d": None,
        }

    def provider_with(self, collector, oauth=None, now=10_000, cfg=None, http_get=None):
        provider_claude = MODULE["provider_claude"]
        paths = {
            "CLAUDE_STATUSLINE_CACHE": "/collector",
            "CLAUDE_CACHE": "/oauth",
            "CLAUDE_CREDENTIALS": "/credentials",
        }

        def read(path):
            return collector if path == "/collector" else oauth if path == "/oauth" else None

        replacements = {
            "read_cache": read, "read_json": lambda _path: None,
            "claude_claim_oauth_attempt": lambda _now: (True, oauth or {}, None),
            "claude_finish_oauth_attempt": lambda *_args, **_kwargs: None,
        }
        if http_get is not None:
            replacements["http_get_json"] = http_get
        with mock.patch.dict(provider_claude.__globals__, paths), \
                mock.patch.object(provider_claude.__globals__["time"], "time", return_value=now), \
                mock.patch.dict(provider_claude.__globals__, replacements):
            return provider_claude(cfg or self.cfg())

    def test_collector_data_older_than_900_seconds_is_unavailable(self):
        result = self.provider_with({
            "fetched_at": 9_099,
            "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": 11_000}},
        })
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])

    def test_fresh_collector_omits_expired_windows(self):
        result = self.provider_with({
            "fetched_at": 9_500,
            "rate_limits": {
                "five_hour": {"used_percentage": 12, "resets_at": 10_000},
                "seven_day": {"used_percentage": 23, "resets_at": 11_000},
            },
        })
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "statusline")
        self.assertEqual([item["key"] for item in result["windows"]], ["7d"])

    def test_malformed_collector_values_fall_back_to_oauth(self):
        oauth = {
            "fetched_at": 9_500,
            "last_attempt_at": 9_500,
            "data": {"five_hour": {"utilization": 31, "resets_at": "1970-01-01T03:03:20Z"}},
        }
        malformed = (True, float("nan"), float("inf"), -1, 101, "12", 10 ** 10_000)
        for percentage in malformed:
            with self.subTest(percentage=percentage):
                result = self.provider_with({
                    "fetched_at": 9_500,
                    "rate_limits": {"five_hour": {
                        "used_percentage": percentage, "resets_at": 11_000,
                    }},
                }, oauth)
            self.assertTrue(result["available"])
            self.assertEqual(result["source"], "api-cached")

    def test_invalid_collector_reset_is_honestly_unavailable(self):
        for resets_at in (True, "11000", float("nan"), float("inf"), -1, 10 ** 10_000):
            with self.subTest(resets_at=resets_at):
                result = self.provider_with({
                    "fetched_at": 9_500,
                    "rate_limits": {"five_hour": {
                        "used_percentage": 12, "resets_at": resets_at,
                    }},
                })
            self.assertFalse(result["available"])
            self.assertEqual(result["windows"], [])

    def test_oauth_data_older_than_3600_seconds_is_unavailable(self):
        result = self.provider_with(None, {
            "fetched_at": 6_399,
            "last_attempt_at": 9_500,
            "data": {"five_hour": {"utilization": 12, "resets_at": "1970-01-01T03:03:20Z"}},
        })
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])

    def test_oauth_429_reports_rate_limit_and_retry_time(self):
        cfg = self.cfg()
        cfg["claude_token"] = "test-token"
        http_error = MODULE["urllib"].error.HTTPError(
            "https://example.invalid", 429, "limited", {"Retry-After": "30"}, None
        )
        self.addCleanup(http_error.close)

        def rate_limited(*_args, **_kwargs):
            raise http_error

        result = self.provider_with(None, cfg=cfg, http_get=rate_limited)
        self.assertFalse(result["available"])
        self.assertIn("usage query rate-limited", result["error"])
        self.assertIn("10030", result["error"])

    def test_non_429_attempt_is_persisted_without_destroying_good_cache(self):
        cfg = self.cfg()
        cfg["claude_token"] = "test-token"
        old = {"fetched_at": 9_000, "data": {"five_hour": {
            "utilization": 31, "resets_at": "1970-01-01T03:03:20Z"}},
               "retry_after_until": 0}
        writes = []
        error = MODULE["urllib"].error.HTTPError("x", 500, "bad", {}, None)
        self.addCleanup(error.close)
        provider = MODULE["provider_claude"]
        with mock.patch.object(provider.__globals__["time"], "time", return_value=10_000), \
                mock.patch.dict(provider.__globals__, {
                    "read_json": lambda _p: None,
                    "read_cache": lambda p: None if p == "/collector" else old,
                    "http_get_json": lambda *_a: (_ for _ in ()).throw(error),
                    "claude_claim_oauth_attempt": lambda now: (
                        writes.append({**old, "last_attempt_at": now}) or
                        (True, {**old, "last_attempt_at": now}, None)),
                    "CLAUDE_STATUSLINE_CACHE": "/collector", "CLAUDE_CACHE": "/oauth",
                    "CLAUDE_CREDENTIALS": "/credentials",
                }):
            provider(cfg)
        self.assertEqual(writes[-1], {**old, "last_attempt_at": 10_000})

    def test_generic_exception_attempt_is_persisted(self):
        cfg = self.cfg()
        cfg["claude_token"] = "test-token"
        writes = []
        provider = MODULE["provider_claude"]
        with mock.patch.object(provider.__globals__["time"], "time", return_value=10_000), \
                mock.patch.dict(provider.__globals__, {
                    "read_json": lambda _p: None, "read_cache": lambda _p: None,
                    "http_get_json": lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")),
                    "claude_claim_oauth_attempt": lambda now: (
                        writes.append({"fetched_at": None, "last_attempt_at": now,
                                       "data": None, "retry_after_until": 0}) or
                        (True, writes[-1], None)),
                    "CLAUDE_STATUSLINE_CACHE": "/collector", "CLAUDE_CACHE": "/oauth",
                    "CLAUDE_CREDENTIALS": "/credentials",
                }):
            provider(cfg)
        self.assertEqual(writes[-1]["last_attempt_at"], 10_000)

    def test_oauth_rejects_invalid_main_dynamic_and_extra_percentages(self):
        invalid = (True, -1, 101, float("nan"), float("inf"), 10 ** 10_000)
        for value in invalid:
            with self.subTest(kind=type(value).__name__):
                data = {
                    "five_hour": {"utilization": value, "resets_at": "1970-01-01T03:03:20Z"},
                    "seven_day_tier": {"utilization": value, "resets_at": "1970-01-01T03:03:20Z"},
                    "extra_usage": {"is_enabled": True, "utilization": value},
                }
                self.assertEqual(MODULE["claude_from_api"](data, True), [])

    def test_oauth_rejects_invalid_reset_values(self):
        for reset in (True, -1, float("nan"), float("inf"), 10 ** 10_000, "not-a-date"):
            with self.subTest(reset=type(reset).__name__):
                self.assertEqual(MODULE["claude_from_api"]({"five_hour": {
                    "utilization": 10, "resets_at": reset}}), [])

    def test_malformed_cached_oauth_container_is_honestly_unavailable(self):
        result = self.provider_with(None, {
            "fetched_at": 9_500, "last_attempt_at": 9_500, "data": ["bad"]})
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])

    def test_concurrent_processes_make_one_oauth_network_attempt(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires Linux fork semantics")
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            provider = MODULE["provider_claude"]
            calls = context.Value("i", 0)
            start = context.Event()
            results = context.Queue()
            cache = str(Path(directory) / "claude-usage.json")
            lock = str(Path(directory) / "claude-oauth.lock")

            def network(*_args):
                with calls.get_lock():
                    calls.value += 1
                return {"five_hour": {"utilization": 12,
                        "resets_at": "2100-01-01T00:00:00Z"}}

            def worker():
                start.wait()
                results.put(provider({**self.cfg(), "claude_token": "token"}))

            replacements = {
                "CLAUDE_STATUSLINE_CACHE": str(Path(directory) / "collector"),
                "CLAUDE_CACHE": cache,
                "CLAUDE_OAUTH_LOCK": lock,
                "CACHE_DIR": directory,
                "CLAUDE_CREDENTIALS": str(Path(directory) / "credentials"),
                "http_get_json": network,
            }
            with mock.patch.dict(provider.__globals__, replacements):
                processes = [context.Process(target=worker) for _ in range(2)]
                for process in processes:
                    process.start()
                start.set()
                for process in processes:
                    process.join(5)
                    self.assertEqual(process.exitcode, 0)
            returned = [results.get(timeout=1) for _ in processes]
            self.assertEqual(calls.value, 1)
            self.assertTrue(any(item["available"] for item in returned))
            self.assertTrue(all(item["available"] or item["windows"] == [] for item in returned))

    def test_oauth_lock_failure_returns_honest_unavailable(self):
        cfg = {**self.cfg(), "claude_token": "token"}
        provider = MODULE["provider_claude"]
        with mock.patch.object(provider.__globals__["time"], "time", return_value=10_000), \
                mock.patch.dict(provider.__globals__, {
                    "read_json": lambda _p: None, "read_cache": lambda _p: None,
                    "claude_claim_oauth_attempt": lambda _now: (False, None, "lock timed out"),
                    "http_get_json": lambda *_a: (_ for _ in ()).throw(AssertionError("network called")),
                    "CLAUDE_STATUSLINE_CACHE": "/collector", "CLAUDE_CACHE": "/oauth",
                    "CLAUDE_CREDENTIALS": "/credentials",
                }):
            result = provider(cfg)
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("lock timed out", result["error"])

    def test_oauth_claim_lock_wait_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / "oauth.lock")
            cache_path = str(Path(directory) / "oauth.json")
            holder = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            self.addCleanup(os.close, holder)
            fcntl.flock(holder, fcntl.LOCK_EX)
            claim = MODULE["claude_claim_oauth_attempt"]
            started = MODULE["time"].monotonic()
            with mock.patch.dict(claim.__globals__, {
                    "CLAUDE_OAUTH_LOCK": lock_path, "CLAUDE_CACHE": cache_path,
                    "CLAUDE_OAUTH_LOCK_TIMEOUT": 0.02}):
                claimed, _cache, error = claim(MODULE["time"].time())
            self.assertFalse(claimed)
            self.assertIn("timed out", error)
            self.assertLess(MODULE["time"].monotonic() - started, 0.5)

    def test_oauth_result_does_not_overwrite_newer_good_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / "oauth.lock")
            cache_path = Path(directory) / "oauth.json"
            newer = {"fetched_at": 20_000, "last_attempt_at": 20_000,
                     "data": {"five_hour": {"utilization": 5}},
                     "retry_after_until": 0}
            cache_path.write_text(json.dumps(newer), encoding="utf-8")
            finish = MODULE["claude_finish_oauth_attempt"]
            with mock.patch.dict(finish.__globals__, {
                    "CLAUDE_OAUTH_LOCK": lock_path, "CLAUDE_CACHE": str(cache_path)}):
                finish(10_000, data={"five_hour": {"utilization": 99}})
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), newer)

    def test_oauth_claim_closes_fd_when_fchmod_fails(self):
        claim = MODULE["claude_claim_oauth_attempt"]
        with mock.patch.object(MODULE["os"], "makedirs"), \
                mock.patch.object(MODULE["os"], "chmod"), \
                mock.patch.object(MODULE["os"], "open", return_value=123), \
                mock.patch.object(MODULE["os"], "fchmod", side_effect=OSError("boom")), \
                mock.patch.object(MODULE["os"], "close") as close, \
                mock.patch.dict(claim.__globals__, {"read_cache": lambda _p: None}):
            claimed, _cache, error = claim(10_000)
        self.assertFalse(claimed)
        self.assertIn("lock failed", error)
        close.assert_called_once_with(123)

    def test_oauth_finish_closes_fd_when_fchmod_fails(self):
        finish = MODULE["claude_finish_oauth_attempt"]
        with mock.patch.object(MODULE["os"], "makedirs"), \
                mock.patch.object(MODULE["os"], "open", return_value=456), \
                mock.patch.object(MODULE["os"], "fchmod", side_effect=OSError("boom")), \
                mock.patch.object(MODULE["os"], "close") as close:
            finish(10_000, data={"five_hour": {"utilization": 5}})
        close.assert_called_once_with(456)

    def test_cache_age_rejects_future_and_non_numeric_timestamps(self):
        valid = MODULE["cache_age_is_valid"]
        self.assertTrue(valid(9_100, 900, 10_000))
        for fetched_at in (9_099, 10_001, None, True, "9500"):
            with self.subTest(fetched_at=fetched_at):
                self.assertFalse(valid(fetched_at, 900, 10_000))


class AntigravityProviderTests(unittest.TestCase):
    def cached_status(self):
        return {
            "planStatus": {
                "planInfo": {"planName": "Pro", "monthlyPromptCredits": 1000},
                "availablePromptCredits": 750,
            },
        }

    def provider_with_cache(self, fetched_at, now=10_000):
        provider_antigravity = MODULE["provider_antigravity"]
        with mock.patch.object(provider_antigravity.__globals__["time"], "time", return_value=now), \
                mock.patch.dict(provider_antigravity.__globals__, {
                    "antigravity_user_status": lambda: None,
                    "read_cache": lambda _path: {
                        "fetched_at": fetched_at,
                        "data": self.cached_status(),
                    },
                }):
            return provider_antigravity({})

    def test_cache_at_599_seconds_is_available(self):
        result = self.provider_with_cache(9_401)

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "local-cached")

    def test_cache_at_600_seconds_is_available(self):
        result = self.provider_with_cache(9_400)

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "local-cached")

    def test_cache_at_601_seconds_is_unavailable_with_age(self):
        result = self.provider_with_cache(9_399)

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("Antigravity not running", result["error"])
        self.assertIn("601", result["error"])

    def test_invalid_cache_timestamps_are_unavailable_without_exceptions(self):
        invalid = (True, None, "9401", 10_001, float("nan"), float("inf"),
                   float("-inf"), 10 ** 10_000, -(10 ** 10_000))
        for fetched_at in invalid:
            with self.subTest(fetched_at=type(fetched_at).__name__):
                result = self.provider_with_cache(fetched_at)
            self.assertFalse(result["available"])
            self.assertEqual(result["windows"], [])
            self.assertIn("Antigravity not running", result["error"])

    def test_expired_model_window_is_removed_live_and_cached(self):
        status = {"cascadeModelConfigData": {"clientModelConfigs": [{
            "label": "Model", "quotaInfo": {"remainingFraction": .5,
            "resetTime": "1970-01-01T02:46:40Z"}}]}}
        provider = MODULE["provider_antigravity"]
        for live in (True, False):
            with self.subTest(live=live), mock.patch.object(
                    provider.__globals__["time"], "time", return_value=10_000), \
                    mock.patch.dict(provider.__globals__, {
                        "antigravity_user_status": lambda: status if live else None,
                        "read_cache": lambda _p: {"fetched_at": 9_900, "data": status},
                        "write_cache": lambda *_a: None,
                    }):
                result = provider({})
            self.assertFalse(result["available"])
            self.assertEqual(result["windows"], [])

    def test_antigravity_rejects_invalid_external_numerics(self):
        invalid = (True, -1, 1.1, float("nan"), float("inf"), 10 ** 10_000)
        for value in invalid:
            status = {"cascadeModelConfigData": {"clientModelConfigs": [{
                "quotaInfo": {"remainingFraction": value, "resetTime": None}}]}}
            with self.subTest(value=type(value).__name__):
                self.assertEqual(MODULE["antigravity_build"](status)[1], [])

    def test_antigravity_rejects_invalid_credit_ranges(self):
        for monthly, available in ((True, 0), (0, 0), (100, -1), (100, 101),
                                   (float("inf"), 1), (10 ** 10_000, 1)):
            with self.subTest(monthly=type(monthly).__name__, available=available):
                status = {"planStatus": {"planInfo": {"monthlyPromptCredits": monthly},
                                         "availablePromptCredits": available}}
                self.assertEqual(MODULE["antigravity_build"](status)[1], [])

    def test_antigravity_malformed_container_is_honestly_unavailable(self):
        self.assertEqual(MODULE["antigravity_build"](["bad"]), (None, []))


if __name__ == "__main__":
    unittest.main()
