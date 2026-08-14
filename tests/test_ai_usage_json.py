import json
import io
import hashlib
import errno
import multiprocessing
import fcntl
import os
import runpy
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


HELPER = Path(__file__).parents[1] / "package/contents/code/ai-usage-json"
MODULE = runpy.run_path(str(HELPER), run_name="ai_usage_json_test")


class MetadataVersionTests(unittest.TestCase):
    def test_package_version_matches_release(self):
        metadata_path = Path(__file__).parents[1] / "package/metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["KPlugin"]["Version"], "0.2.0")


class ClaudeProfileDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        (self.home / ".claude").mkdir()
        (self.home / ".claude/.credentials.json").write_text("{}", encoding="utf-8")
        (self.home / ".claude-work/projects").mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_discovers_default_and_named_profiles(self):
        profiles = MODULE["discover_claude_profiles"]({}, str(self.home))
        self.assertEqual([p["label"] for p in profiles], ["Claude", "Claude Work"])
        self.assertTrue(all(p["base_provider"] == "claude" for p in profiles))
        self.assertTrue(all(p["enabled"] for p in profiles))
        self.assertTrue(all(p.get("manual_paths") == [] for p in profiles))

    def test_normalizes_symlinked_home_before_discovering_profiles(self):
        home_alias = self.home.parent / (self.home.name + "-home-alias")
        home_alias.symlink_to(self.home, target_is_directory=True)

        profiles = MODULE["discover_claude_profiles"]({}, str(home_alias))

        self.assertEqual([p["path"] for p in profiles], [
            str(self.home / ".claude"),
            str(self.home / ".claude-work"),
        ])

    def test_rejects_similarly_named_directory_without_profile_marker(self):
        (self.home / ".claude-not-a-profile").mkdir()
        profiles = MODULE["discover_claude_profiles"]({}, str(self.home))
        self.assertNotIn(str(self.home / ".claude-not-a-profile"),
                         [p["canonical_path"] for p in profiles])

    def test_automatic_profile_rejects_markers_with_wrong_file_types(self):
        profile = self.home / ".claude-wrong-types"
        (profile / ".credentials.json").mkdir(parents=True)
        (profile / "settings.json").mkdir()
        (profile / "projects").write_text("not a directory", encoding="utf-8")

        profiles = MODULE["discover_claude_profiles"]({}, str(self.home))

        self.assertNotIn(str(profile.resolve()),
                         [item["canonical_path"] for item in profiles])

    def test_manual_profile_with_only_wrong_marker_types_is_invalid(self):
        profile = self.home / "manual-wrong-types"
        (profile / ".credentials.json").mkdir(parents=True)
        (profile / "settings.json").mkdir()
        (profile / "projects").write_text("not a directory", encoding="utf-8")

        profiles = MODULE["discover_claude_profiles"]({
            "manual": [{"path": str(profile)}], "overrides": {},
        }, str(self.home))

        manual = next(item for item in profiles
                      if item["canonical_path"] == str(profile.resolve()))
        self.assertEqual(manual["profile_error"], "profile marker not found")

    def test_marker_symlinks_to_correct_target_types_are_accepted(self):
        targets = self.home / "marker-targets"
        targets.mkdir()
        credentials = targets / "credentials.json"
        credentials.write_text("{}", encoding="utf-8")
        settings = targets / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        projects = targets / "projects"
        projects.mkdir()
        profile = self.home / ".claude-linked-markers"
        profile.mkdir()
        (profile / ".credentials.json").symlink_to(credentials)
        (profile / "settings.json").symlink_to(settings)
        (profile / "projects").symlink_to(projects, target_is_directory=True)

        profiles = MODULE["discover_claude_profiles"]({}, str(self.home))

        linked = next(item for item in profiles
                      if item["canonical_path"] == str(profile.resolve()))
        self.assertTrue(linked["automatic"])
        self.assertNotIn("profile_error", linked)

    def test_manual_invalid_path_remains_unavailable_candidate(self):
        missing = self.home / "elsewhere/missing"
        raw = {"manual": [{"path": str(missing)}], "overrides": {}}
        profiles = MODULE["discover_claude_profiles"](raw, str(self.home))
        manual = next(p for p in profiles if not p["automatic"])
        self.assertEqual(manual["canonical_path"], str(missing))
        self.assertEqual(manual["profile_error"], "profile directory does not exist")

    def test_deduplicates_symlink_and_applies_override_by_canonical_path(self):
        alias = self.home / "work-alias"
        alias.symlink_to(self.home / ".claude-work")
        canonical = str((self.home / ".claude-work").resolve())
        raw = {
            "manual": [{"path": str(alias)}],
            "overrides": {canonical: {"label": "Beruflich", "enabled": False}},
        }
        profiles = MODULE["discover_claude_profiles"](raw, str(self.home))
        work = next(p for p in profiles if p["canonical_path"] == canonical)
        self.assertEqual(work["label"], "Beruflich")
        self.assertFalse(work["enabled"])
        self.assertEqual(sum(p["canonical_path"] == canonical for p in profiles), 1)

    def test_preserves_all_raw_manual_aliases_when_automatic_profile_wins(self):
        first_alias = self.home / "work-alias"
        second_alias = self.home / "work-alias-two"
        first_alias.symlink_to(self.home / ".claude-work")
        second_alias.symlink_to(self.home / ".claude-work")
        raw = {
            "manual": [
                {"path": str(first_alias)},
                {"path": str(second_alias)},
            ],
            "overrides": {},
        }

        profiles = MODULE["discover_claude_profiles"](raw, str(self.home))

        work = next(
            profile for profile in profiles
            if profile["canonical_path"] == str((self.home / ".claude-work").resolve())
        )
        self.assertTrue(work["automatic"])
        self.assertIn("manual_paths", work)
        self.assertEqual(
            work["manual_paths"],
            [str(first_alias), str(second_alias)],
        )

    def test_preserves_raw_manual_aliases_for_manual_only_profile(self):
        target = self.home / "outside"
        target.mkdir()
        (target / "settings.json").write_text("{}", encoding="utf-8")
        first_alias = self.home / "outside-alias"
        second_alias = self.home / "outside-alias-two"
        first_alias.symlink_to(target)
        second_alias.symlink_to(target)
        raw = {
            "manual": [
                {"path": str(first_alias)},
                {"path": str(second_alias)},
            ],
            "overrides": {},
        }

        profiles = MODULE["discover_claude_profiles"](raw, str(self.home))

        manual = next(profile for profile in profiles if not profile["automatic"])
        self.assertIn("manual_paths", manual)
        self.assertEqual(
            manual["manual_paths"],
            [str(first_alias), str(second_alias)],
        )

    def test_applies_symlink_override_after_canonicalizing_its_key(self):
        alias = self.home / "work-alias"
        alias.symlink_to(self.home / ".claude-work")
        raw = {
            "manual": [],
            "overrides": {str(alias): {"label": "Alias", "enabled": False}},
        }
        profiles = MODULE["discover_claude_profiles"](raw, str(self.home))
        work = next(p for p in profiles if p["label"] == "Alias")
        self.assertEqual(work["canonical_path"], str((self.home / ".claude-work").resolve()))
        self.assertFalse(work["enabled"])

    def test_reads_manual_profile_markers_from_canonical_path(self):
        profile_path = "/profile-alias"
        canonical_path = "/profile-target"
        discover = MODULE["discover_claude_profiles"]

        def realpath(path):
            return canonical_path if path == profile_path else path

        with mock.patch.object(MODULE["os"].path, "realpath", side_effect=realpath), \
                mock.patch.object(MODULE["os"].path, "isdir",
                                  side_effect=lambda path: path == canonical_path), \
                mock.patch.object(MODULE["os"].path, "isfile",
                                  side_effect=lambda path: path ==
                                  canonical_path + "/.credentials.json"), \
                mock.patch.object(MODULE["os"].path, "exists",
                                  side_effect=lambda path: path == canonical_path):
            profiles = discover({"manual": [{"path": profile_path}], "overrides": {}}, "/home/test")

        self.assertEqual(profiles[0]["canonical_path"], canonical_path)
        self.assertNotIn("profile_error", profiles[0])

    def test_stable_id_does_not_contain_path(self):
        profile_id = MODULE["claude_profile_id"](str(self.home / ".claude"))
        self.assertRegex(profile_id, r"^claude-[0-9a-f]{12}$")
        self.assertNotIn(self.home.name, profile_id)

    def test_override_rejects_unknown_keys(self):
        canonical = str((self.home / ".claude").resolve())
        raw = {
            "manual": [],
            "overrides": {canonical: {"label": "Private", "colour": "red"}},
        }

        with self.assertRaisesRegex(ValueError, "unknown Claude profile override key"):
            MODULE["discover_claude_profiles"](raw, str(self.home))

    def test_load_config_parses_valid_claude_profiles_json(self):
        raw = {"manual": [], "overrides": {}}
        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_CLAUDE_PROFILES_JSON": json.dumps(raw),
        }, clear=True):
            config = MODULE["load_config"]()
        self.assertEqual(config["claude_profiles"], raw)
        self.assertIsNone(config["claude_profiles_error"])

    def test_load_config_preserves_malformed_claude_profiles_json_error(self):
        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_CLAUDE_PROFILES_JSON": "{not json",
        }, clear=True):
            config = MODULE["load_config"]()
        self.assertIsNone(config["claude_profiles"])
        self.assertIsInstance(config["claude_profiles_error"], ValueError)

    def test_load_config_preserves_non_object_claude_profiles_json_error(self):
        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_CLAUDE_PROFILES_JSON": "[]",
        }, clear=True):
            config = MODULE["load_config"]()
        self.assertIsNone(config["claude_profiles"])
        self.assertIsInstance(config["claude_profiles_error"], ValueError)

    def test_load_config_distinguishes_empty_provider_list_from_missing_env(self):
        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_PROVIDERS": "",
        }, clear=True):
            explicitly_empty = MODULE["load_config"]()
        with mock.patch.dict(MODULE["os"].environ, {}, clear=True):
            missing = MODULE["load_config"]()

        self.assertEqual(explicitly_empty["providers"], [])
        self.assertEqual(missing["providers"], MODULE["DEFAULT_ORDER"])

    def test_main_with_explicitly_empty_provider_list_calls_no_provider(self):
        output = io.StringIO()

        def unexpected_provider(_cfg):
            self.fail("provider must not be called for an explicitly empty list")

        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_PROVIDERS": "",
        }, clear=True), mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "PROVIDERS": {
                        "claude": unexpected_provider,
                        "codex": unexpected_provider,
                        "antigravity": unexpected_provider,
                    },
                }):
            MODULE["main"]()

        self.assertEqual(json.loads(output.getvalue())["providers"], [])

    def test_main_returns_unavailable_claude_for_invalid_profile_config(self):
        output = io.StringIO()
        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_CLAUDE_PROFILES_JSON": "[]",
            "AI_USAGE_PROVIDERS": "claude",
        }, clear=True), mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                mock.patch.object(MODULE["sys"], "stdout", output):
            MODULE["main"]()
        result = json.loads(output.getvalue())
        self.assertEqual(len(result["providers"]), 1)
        claude = result["providers"][0]
        self.assertFalse(claude["available"])
        self.assertEqual(claude["windows"], [])
        self.assertEqual(claude["base_provider"], "claude")
        self.assertIn("invalid Claude profile configuration", claude["error"])

    def test_claude_profiles_command_returns_profiles_without_usage_queries(self):
        output = io.StringIO()
        with mock.patch.dict(MODULE["os"].environ, {
            "AI_USAGE_CLAUDE_PROFILES_JSON": "{}",
        }, clear=True), mock.patch.object(MODULE["sys"], "argv", [
            "ai-usage-json", "--claude-profiles",
        ]), mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "discover_claude_profiles": lambda raw: [{"id": "claude-test"}],
                    "PROVIDERS": {"claude": lambda _cfg: self.fail("usage query called")},
                }):
            MODULE["main"]()
        self.assertEqual(json.loads(output.getvalue()), {"profiles": [{"id": "claude-test"}]})


class ClaudeMultiProfileProviderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.cache_dir = self.home / "cache"
        self.cfg = {
            "claude_token": None,
            "claude_token_file": None,
            "claude_local_fallback": False,
            "claude_extra_usage": False,
            "claude_cap_5h": None,
            "claude_cap_7d": None,
            "claude_profiles": {"manual": [], "overrides": {}},
            "claude_profiles_error": None,
            "providers": ["claude", "codex", "antigravity"],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def make_profile(self, dirname, token, plan, label=None, enabled=True):
        path = self.home / dirname
        path.mkdir(parents=True)
        oauth = {"subscriptionType": plan}
        if token is not None:
            oauth["accessToken"] = token
        (path / ".credentials.json").write_text(json.dumps({
            "claudeAiOauth": oauth,
        }), encoding="utf-8")
        return {
            "id": MODULE["claude_profile_id"](str(path)),
            "base_provider": "claude",
            "label": label or ("Claude" if dirname == ".claude" else "Claude Work"),
            "path": str(path),
            "canonical_path": str(path.resolve()),
            "automatic": True,
            "enabled": enabled,
        }

    def profile_paths(self, profile):
        with mock.patch.dict(MODULE["claude_profile_id"].__globals__, {
            "CACHE_DIR": str(self.cache_dir),
        }):
            return MODULE["claude_profile_paths"](profile)

    def test_profiles_use_separate_credentials_labels_and_ids(self):
        private = self.make_profile(".claude", "private-token", "max", "Privat")
        work = self.make_profile(".claude-work", "work-token", "team", "Arbeit")
        calls = []

        def fake_get(_url, token):
            calls.append(token)
            utilization = 11 if token == "private-token" else 22
            return {"five_hour": {"utilization": utilization, "resets_at": 200}}

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir), "http_get_json": fake_get}), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            private_result = MODULE["provider_claude"](self.cfg, private)
            work_result = MODULE["provider_claude"](self.cfg, work)

        self.assertEqual(calls, ["private-token", "work-token"])
        self.assertEqual(private_result["windows"][0]["used_percent"], 11)
        self.assertEqual(work_result["windows"][0]["used_percent"], 22)
        self.assertEqual(private_result["base_provider"], "claude")
        self.assertEqual(private_result["profile_path"], private["canonical_path"])
        self.assertEqual(private_result["label"], "Privat")
        self.assertEqual(private_result["plan"], "max")
        self.assertNotEqual(private_result["id"], work_result["id"])
        serialized = json.dumps([private_result, work_result])
        self.assertNotIn("private-token", serialized)
        self.assertNotIn("work-token", serialized)
        self.assertNotIn("credentials", serialized)

    def test_profile_paths_are_isolated_for_every_profile_path(self):
        private = self.make_profile(".claude", None, "max")
        work = self.make_profile(".claude-work", None, "team")

        private_paths = self.profile_paths(private)
        work_paths = self.profile_paths(work)

        self.assertEqual(set(private_paths), {
            "credentials", "projects", "settings", "oauth_cache", "oauth_lock",
            "collector_cache",
        })
        for key in private_paths:
            with self.subTest(key=key):
                self.assertNotEqual(private_paths[key], work_paths[key])

    def test_private_cache_is_never_returned_for_work(self):
        private = self.make_profile(".claude", None, "max")
        work = self.make_profile(".claude-work", None, "team")
        private_paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        private_cache = {
            "fetched_at": 90,
            "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 77, "resets_at": 200}},
            "retry_after_until": 0,
        }
        MODULE["atomic_write_json"](
            private_paths["oauth_cache"], private_cache, str(self.cache_dir))

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir)}), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            private_result = MODULE["provider_claude"](self.cfg, private)
            work_result = MODULE["provider_claude"](self.cfg, work)

        self.assertEqual(private_result["windows"][0]["used_percent"], 77)
        self.assertFalse(work_result["available"])
        self.assertEqual(work_result["windows"], [])
        self.assertEqual(work_result["error"], "no access token for profile")

    def test_local_estimate_reads_only_supplied_projects_directory(self):
        private = self.make_profile(".claude", None, "max")
        work = self.make_profile(".claude-work", None, "team")
        for profile, tokens in ((private, 3), (work, 19)):
            projects = Path(profile["canonical_path"]) / "projects" / "session"
            projects.mkdir(parents=True)
            (projects / "usage.jsonl").write_text(json.dumps({
                "timestamp": "1970-01-01T00:15:00Z",
                "message": {"usage": {"input_tokens": tokens}},
            }) + "\n", encoding="utf-8")

        with mock.patch.object(MODULE["claude_local_estimate"].__globals__["time"],
                               "time", return_value=1_000):
            private_windows = MODULE["claude_local_estimate"](
                str(Path(private["canonical_path"]) / "projects"), None, None)
            work_windows = MODULE["claude_local_estimate"](
                str(Path(work["canonical_path"]) / "projects"), None, None)

        self.assertEqual(private_windows[0]["detail"], "~3 tok (no cap set)")
        self.assertEqual(work_windows[0]["detail"], "~19 tok (no cap set)")

    def test_local_estimate_counts_only_bounded_nonnegative_exact_integers(self):
        profile = self.make_profile(".claude", None, "max")
        projects = Path(profile["canonical_path"]) / "projects" / "session"
        projects.mkdir(parents=True)
        (projects / "usage.jsonl").write_text(json.dumps({
            "timestamp": "1970-01-01T00:15:00Z",
            "message": {"usage": {
                "valid": 7,
                "boolean": True,
                "negative": -3,
                "oversized": MODULE["MAX_SAFE_EXTERNAL_NUMBER"] + 1,
                "float": 2.0,
            }},
        }) + "\n", encoding="utf-8")

        with mock.patch.object(MODULE["claude_local_estimate"].__globals__["time"],
                               "time", return_value=1_000):
            windows = MODULE["claude_local_estimate"](
                str(Path(profile["canonical_path"]) / "projects"), None, None)

        self.assertEqual(windows[0]["detail"], "~7 tok (no cap set)")

    def test_local_estimate_stops_at_absolute_deadline(self):
        profile = self.make_profile(".claude", None, "max")
        projects = Path(profile["canonical_path"]) / "projects" / "session"
        projects.mkdir(parents=True)
        (projects / "usage.jsonl").write_text(json.dumps({
            "timestamp": "1970-01-01T00:15:00Z",
            "message": {"usage": {"input_tokens": 7}},
        }) + "\n", encoding="utf-8")

        estimate = MODULE["claude_local_estimate"](
            str(projects.parent), None, None, deadline=time.monotonic() - 1)

        self.assertIsNone(estimate)

    def test_provider_passes_absolute_deadline_to_local_estimate(self):
        profile = self.make_profile(".claude", None, "max")
        cfg = {**self.cfg, "claude_local_fallback": True}
        seen_deadlines = []
        deadline = time.monotonic() + 1

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir),
                "claude_local_estimate": lambda _projects, _cap_5h, _cap_7d,
                deadline=None: seen_deadlines.append(deadline) or None,
                }):
            result = MODULE["provider_claude"](cfg, profile, deadline=deadline)

        self.assertFalse(result["available"])
        self.assertEqual(seen_deadlines, [deadline])

    def test_invalid_manual_profile_is_explicitly_unavailable(self):
        profile = {
            "id": MODULE["claude_profile_id"](str(self.home / "missing")),
            "base_provider": "claude",
            "label": "Defektes Profil",
            "path": str(self.home / "missing"),
            "canonical_path": str(self.home / "missing"),
            "automatic": False,
            "enabled": True,
            "profile_error": "profile directory does not exist",
        }

        result = MODULE["provider_claude"](self.cfg, profile)

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["label"], "Defektes Profil")
        self.assertEqual(result["error"], profile["profile_error"])
        self.assertEqual(result["base_provider"], "claude")
        self.assertEqual(result["profile_path"], profile["canonical_path"])

    def test_legacy_caches_migrate_only_to_missing_default_profile_caches(self):
        private = self.make_profile(".claude", None, "max")
        work = self.make_profile(".claude-work", None, "team")
        private_paths = self.profile_paths(private)
        work_paths = self.profile_paths(work)
        self.cache_dir.mkdir(mode=0o700)
        legacy_oauth = self.cache_dir / "claude-usage.json"
        legacy_collector = self.cache_dir / "claude-statusline.json"
        oauth_data = {
            "fetched_at": 90, "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 41, "resets_at": 200}},
            "retry_after_until": 0,
        }
        collector_data = {
            "fetched_at": 90,
            "rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": 200}},
        }
        legacy_oauth.write_text(json.dumps(oauth_data), encoding="utf-8")
        legacy_collector.write_text(json.dumps(collector_data), encoding="utf-8")
        real_expanduser = MODULE["os"].path.expanduser

        def expanduser(path):
            return private["canonical_path"] if path == "~/.claude" else real_expanduser(path)

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir),
                "CLAUDE_CACHE": str(legacy_oauth),
                "CLAUDE_STATUSLINE_CACHE": str(legacy_collector),
            }), mock.patch.object(MODULE["os"].path, "expanduser", side_effect=expanduser), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            private_result = MODULE["provider_claude"](self.cfg, private)
            work_result = MODULE["provider_claude"](self.cfg, work)

        self.assertEqual(private_result["windows"][0]["used_percent"], 42)
        self.assertFalse(work_result["available"])
        self.assertEqual(json.loads(Path(private_paths["oauth_cache"]).read_text(
            encoding="utf-8")), oauth_data)
        self.assertEqual(json.loads(Path(private_paths["collector_cache"]).read_text(
            encoding="utf-8")), collector_data)
        self.assertFalse(Path(work_paths["oauth_cache"]).exists())
        self.assertFalse(Path(work_paths["collector_cache"]).exists())
        self.assertTrue(legacy_oauth.exists())
        self.assertTrue(legacy_collector.exists())
        self.assertEqual(Path(private_paths["oauth_cache"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(private_paths["collector_cache"]).stat().st_mode & 0o777, 0o600)

    def test_legacy_cache_does_not_overwrite_existing_hashed_cache(self):
        private = self.make_profile(".claude", None, "max")
        paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        legacy = self.cache_dir / "claude-usage.json"
        legacy.write_text(json.dumps({
            "fetched_at": 90, "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 91, "resets_at": 200}},
        }), encoding="utf-8")
        current = {
            "fetched_at": 90, "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 13, "resets_at": 200}},
            "retry_after_until": 0,
        }
        MODULE["atomic_write_json"](paths["oauth_cache"], current, str(self.cache_dir))
        real_expanduser = MODULE["os"].path.expanduser

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir), "CLAUDE_CACHE": str(legacy),
                "CLAUDE_STATUSLINE_CACHE": str(self.cache_dir / "missing-collector.json"),
            }), mock.patch.object(
                MODULE["os"].path, "expanduser",
                side_effect=lambda path: private["canonical_path"]
                if path == "~/.claude" else real_expanduser(path)), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            result = MODULE["provider_claude"](self.cfg, private)

        self.assertEqual(result["windows"][0]["used_percent"], 13)
        self.assertEqual(json.loads(Path(paths["oauth_cache"]).read_text(
            encoding="utf-8")), current)

    def test_invalid_legacy_cache_is_not_migrated(self):
        private = self.make_profile(".claude", None, "max")
        paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        legacy = self.cache_dir / "claude-usage.json"
        legacy.write_text("[]", encoding="utf-8")
        real_expanduser = MODULE["os"].path.expanduser

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir), "CLAUDE_CACHE": str(legacy),
                "CLAUDE_STATUSLINE_CACHE": str(self.cache_dir / "missing-collector.json"),
            }), mock.patch.object(
                MODULE["os"].path, "expanduser",
                side_effect=lambda path: private["canonical_path"]
                if path == "~/.claude" else real_expanduser(path)), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            result = MODULE["provider_claude"](self.cfg, private)

        self.assertFalse(result["available"])
        self.assertFalse(Path(paths["oauth_cache"]).exists())

    def test_legacy_migration_never_overwrites_cache_created_during_race(self):
        private = self.make_profile(".claude", None, "max")
        paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        legacy_oauth = self.cache_dir / "claude-usage.json"
        legacy_collector = self.cache_dir / "claude-statusline.json"
        legacy_oauth.write_text(json.dumps({
            "fetched_at": 90, "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 91, "resets_at": 200}},
            "retry_after_until": 0,
        }), encoding="utf-8")
        legacy_collector.write_text(json.dumps({
            "fetched_at": 90,
            "rate_limits": {"five_hour": {"used_percentage": 92, "resets_at": 200}},
        }), encoding="utf-8")
        raced = {
            paths["oauth_cache"]: {
                "fetched_at": 95, "last_attempt_at": 95,
                "data": {"five_hour": {"utilization": 13, "resets_at": 200}},
                "retry_after_until": 0,
            },
            paths["collector_cache"]: {
                "fetched_at": 95,
                "rate_limits": {"five_hour": {
                    "used_percentage": 14, "resets_at": 200}},
            },
        }

        def create_raced_cache(_temporary, destination):
            MODULE["atomic_write_json"](
                destination, raced[destination], str(Path(destination).parent))
            raise FileExistsError(destination)

        real_expanduser = MODULE["os"].path.expanduser
        with mock.patch.dict(MODULE["migrate_legacy_claude_caches"].__globals__, {
                "CLAUDE_CACHE": str(legacy_oauth),
                "CLAUDE_STATUSLINE_CACHE": str(legacy_collector),
            }), mock.patch.object(MODULE["os"], "link", side_effect=create_raced_cache), \
                mock.patch.object(
                MODULE["os"].path, "expanduser",
                side_effect=lambda path: private["canonical_path"]
                if path == "~/.claude" else real_expanduser(path)):
            statuses = MODULE["migrate_legacy_claude_caches"](private, paths)

        self.assertEqual(statuses["oauth_cache"]["status"], "exists")
        self.assertEqual(statuses["collector_cache"]["status"], "exists")
        for destination, expected in raced.items():
            with self.subTest(destination=destination):
                self.assertEqual(json.loads(Path(destination).read_text(encoding="utf-8")),
                                 expected)

    def test_semantically_unusable_legacy_cache_objects_are_not_migrated(self):
        private = self.make_profile(".claude", None, "max")
        paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        legacy_oauth = self.cache_dir / "claude-usage.json"
        legacy_collector = self.cache_dir / "claude-statusline.json"
        legacy_oauth.write_text(json.dumps({
            "fetched_at": 90, "data": {},
        }), encoding="utf-8")
        legacy_collector.write_text(json.dumps({
            "fetched_at": "90", "rate_limits": {},
        }), encoding="utf-8")
        real_expanduser = MODULE["os"].path.expanduser

        with mock.patch.dict(MODULE["migrate_legacy_claude_caches"].__globals__, {
                "CLAUDE_CACHE": str(legacy_oauth),
                "CLAUDE_STATUSLINE_CACHE": str(legacy_collector),
            }), mock.patch.object(
                MODULE["os"].path, "expanduser",
                side_effect=lambda path: private["canonical_path"]
                if path == "~/.claude" else real_expanduser(path)):
            statuses = MODULE["migrate_legacy_claude_caches"](private, paths)

        self.assertEqual(statuses["oauth_cache"]["status"], "invalid")
        self.assertEqual(statuses["collector_cache"]["status"], "invalid")
        self.assertFalse(Path(paths["oauth_cache"]).exists())
        self.assertFalse(Path(paths["collector_cache"]).exists())

    def test_migration_io_error_is_reported_while_provider_evaluation_continues(self):
        private = self.make_profile(".claude", None, "max")
        paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        legacy_oauth = self.cache_dir / "claude-usage.json"
        legacy_oauth.write_text(json.dumps({
            "fetched_at": 90, "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 41, "resets_at": 200}},
            "retry_after_until": 0,
        }), encoding="utf-8")
        real_expanduser = MODULE["os"].path.expanduser

        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir),
                "CLAUDE_CACHE": str(legacy_oauth),
                "CLAUDE_STATUSLINE_CACHE": str(self.cache_dir / "missing-collector.json"),
                "atomic_create_json": mock.Mock(side_effect=OSError("disk full")),
            }), mock.patch.object(
                MODULE["os"].path, "expanduser",
                side_effect=lambda path: private["canonical_path"]
                if path == "~/.claude" else real_expanduser(path)), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            result = MODULE["provider_claude"](self.cfg, private)

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("no access token for profile", result["error"])
        self.assertIn("legacy OAuth cache migration failed: disk full", result["error"])

    def test_provider_rereads_target_created_during_failed_migration(self):
        private = self.make_profile(".claude", None, "max")
        paths = self.profile_paths(private)
        self.cache_dir.mkdir(mode=0o700)
        legacy_oauth = self.cache_dir / "claude-usage.json"
        legacy_oauth.write_text(json.dumps({
            "fetched_at": 90, "last_attempt_at": 90,
            "data": {"five_hour": {"utilization": 41, "resets_at": 200}},
            "retry_after_until": 0,
        }), encoding="utf-8")
        raced = {
            "fetched_at": 95, "last_attempt_at": 95,
            "data": {"five_hour": {"utilization": 23, "resets_at": 200}},
            "retry_after_until": 0,
        }

        def write_then_fail(destination, _data, destination_dir):
            MODULE["atomic_write_json"](destination, raced, destination_dir)
            raise OSError("directory sync failed")

        real_expanduser = MODULE["os"].path.expanduser
        with mock.patch.dict(MODULE["provider_claude"].__globals__, {
                "CACHE_DIR": str(self.cache_dir),
                "CLAUDE_CACHE": str(legacy_oauth),
                "CLAUDE_STATUSLINE_CACHE": str(self.cache_dir / "missing-collector.json"),
                "atomic_create_json": write_then_fail,
            }), mock.patch.object(
                MODULE["os"].path, "expanduser",
                side_effect=lambda path: private["canonical_path"]
                if path == "~/.claude" else real_expanduser(path)), \
                mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                                  "time", return_value=100):
            result = MODULE["provider_claude"](self.cfg, private)

        self.assertTrue(result["available"])
        self.assertEqual(result["windows"][0]["used_percent"], 23)
        self.assertIn("legacy OAuth cache migration failed: directory sync failed",
                      result["error"])

    def test_main_expands_enabled_profiles_and_calls_other_providers_once(self):
        private = self.make_profile(".claude", None, "max", "Privat")
        work = self.make_profile(".claude-work", None, "team", "Arbeit")
        hidden = self.make_profile(".claude-hidden", None, "free", "Versteckt", False)
        calls = []
        output = io.StringIO()

        def fake_provider(pid, label):
            calls.append(pid)
            return MODULE["provider"](pid, label, True, "test", None, [], None)

        with mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "load_config": lambda: self.cfg,
                    "discover_claude_profiles": lambda _raw: [private, work, hidden],
                    "PROVIDERS": {
                        "claude": lambda _cfg, profile, _deadline: (
                            calls.append(profile["id"]) or MODULE["provider"](
                                profile["id"], profile["label"], True, "test", None, [], None,
                                base_provider="claude")),
                        "codex": lambda _cfg: fake_provider("codex", "Codex"),
                        "antigravity": lambda _cfg: fake_provider("antigravity", "Antigravity"),
                    },
                }):
            MODULE["main"]()

        report = json.loads(output.getvalue())
        self.assertEqual([item["label"] for item in report["providers"]], [
            "Privat", "Arbeit", "Codex", "Antigravity",
        ])
        self.assertNotIn(hidden["id"], calls)
        self.assertEqual(calls.count("codex"), 1)
        self.assertEqual(calls.count("antigravity"), 1)

    def test_main_emits_unavailable_claude_when_no_profiles_exist(self):
        cfg = {**self.cfg, "providers": ["claude"]}
        output = io.StringIO()
        with mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "load_config": lambda: cfg,
                    "discover_claude_profiles": lambda _raw: [],
                }):
            MODULE["main"]()

        result = json.loads(output.getvalue())["providers"][0]
        self.assertEqual(result["base_provider"], "claude")
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("profile", result["error"].lower())


class ClaudeParallelCollectionTests(unittest.TestCase):
    @staticmethod
    def profiles(count=4):
        return [{
            "id": "claude-%d" % index,
            "label": "Claude %d" % index,
            "canonical_path": "/profile/%d" % index,
        } for index in range(count)]

    def setUp(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires Linux fork semantics")
        self.context = multiprocessing.get_context("fork")

    def test_helper_subprocess_exits_near_deadline_with_blocked_worker(self):
        code = "\n".join((
            "import json, runpy, time",
            "module = runpy.run_path(%r, run_name='deadline_subprocess')" % str(HELPER),
            "profiles = [{'id': 'claude-slow', 'label': 'Slow', "
            "'canonical_path': '/slow'}]",
            "def slow(_cfg, profile, _deadline):",
            "    time.sleep(0.5)",
            "    return {'id': profile['id']}",
            "started = time.monotonic()",
            "result = module['collect_claude_profiles']("
            "{}, profiles, provider_call=slow, deadline=started + 0.05)",
            "print(json.dumps({'elapsed': time.monotonic() - started, "
            "'result': result}))",
        ))

        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            check=True, timeout=2)
        wallclock = time.monotonic() - started
        report = json.loads(completed.stdout)

        self.assertLess(wallclock, 0.25)
        self.assertLess(report["elapsed"], 0.2)
        self.assertFalse(report["result"][0]["available"])
        self.assertIn("deadline", report["result"][0]["error"])

    def test_slow_drip_worker_is_killed_at_deadline(self):
        counter = self.context.Value("i", 0)

        def slow_drip(_cfg, _profile, _deadline):
            while True:
                with counter.get_lock():
                    counter.value += 1
                time.sleep(0.005)

        results = MODULE["collect_claude_profiles"](
            {}, self.profiles(1), provider_call=slow_drip,
            deadline=time.monotonic() + 0.04)
        after_return = counter.value
        time.sleep(0.05)

        self.assertGreater(after_return, 0)
        self.assertEqual(counter.value, after_return)
        self.assertFalse(results[0]["available"])
        self.assertIn("deadline", results[0]["error"])

    def test_more_than_four_profiles_are_scheduled_in_slots_and_keep_order(self):
        active = self.context.Value("i", 0)
        maximum_active = self.context.Value("i", 0)
        started = self.context.Array("i", [0] * 6)

        def provider_call(_cfg, profile, _deadline):
            index = int(profile["id"].rsplit("-", 1)[1])
            started[index] = 1
            with active.get_lock():
                active.value += 1
                maximum_active.value = max(maximum_active.value, active.value)
            try:
                if index == 5:
                    time.sleep(0.5)
                else:
                    time.sleep(0.01 + index * 0.002)
                return {"id": profile["id"]}
            finally:
                with active.get_lock():
                    active.value -= 1

        results = MODULE["collect_claude_profiles"](
            {}, self.profiles(6), provider_call=provider_call,
            deadline=time.monotonic() + 0.12, max_workers=4)

        self.assertEqual(maximum_active.value, 4)
        self.assertEqual(list(started), [1, 1, 1, 1, 1, 1])
        self.assertEqual([item["id"] for item in results], [
            "claude-0", "claude-1", "claude-2", "claude-3",
            "claude-4", "claude-5",
        ])
        self.assertTrue(all(item == {"id": "claude-%d" % index}
                            for index, item in enumerate(results[:5])))
        self.assertFalse(results[5]["available"])
        self.assertIn("deadline", results[5]["error"])

    def test_running_and_unstarted_profiles_are_explicit_timeouts(self):
        started = self.context.Array("i", [0] * 6)

        def blocked(_cfg, profile, _deadline):
            index = int(profile["id"].rsplit("-", 1)[1])
            started[index] = 1
            time.sleep(0.5)

        results = MODULE["collect_claude_profiles"](
            {}, self.profiles(6), provider_call=blocked,
            deadline=time.monotonic() + 0.04, max_workers=4)

        self.assertEqual(list(started), [1, 1, 1, 1, 0, 0])
        self.assertEqual([item["id"] for item in results], [
            "claude-0", "claude-1", "claude-2", "claude-3",
            "claude-4", "claude-5",
        ])
        self.assertTrue(all(not item["available"] for item in results))
        self.assertTrue(all("deadline" in item["error"] for item in results))

    def test_worker_exception_is_explicit_and_isolated(self):
        def provider_call(_cfg, profile, _deadline):
            if profile["id"] == "claude-0":
                raise RuntimeError("worker exploded")
            return {"id": profile["id"]}

        results = MODULE["collect_claude_profiles"](
            {}, self.profiles(2), provider_call=provider_call,
            deadline=time.monotonic() + 0.2)

        self.assertFalse(results[0]["available"])
        self.assertIn("internal error: worker exploded", results[0]["error"])
        self.assertEqual(results[1], {"id": "claude-1"})

    def test_collection_closes_processes_and_file_descriptors(self):
        child_pids_before = {child.pid for child in multiprocessing.active_children()}
        descriptor_count_before = len(os.listdir("/proc/self/fd"))

        results = MODULE["collect_claude_profiles"](
            {}, self.profiles(6),
            provider_call=lambda _cfg, profile, _deadline: {"id": profile["id"]},
            deadline=time.monotonic() + 0.5)

        self.assertEqual([item["id"] for item in results], [
            "claude-0", "claude-1", "claude-2", "claude-3",
            "claude-4", "claude-5",
        ])
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            child_pids_before)
        self.assertEqual(len(os.listdir("/proc/self/fd")), descriptor_count_before)

    def test_main_serializes_pipe_resource_failure_for_current_and_remaining_profiles(self):
        profiles = self.profiles(3)
        for profile in profiles:
            profile["enabled"] = True
        cfg = {
            "providers": ["claude"],
            "claude_profiles": {"manual": [], "overrides": {}},
            "claude_profiles_error": None,
        }
        output = io.StringIO()
        descriptor_count_before = len(os.listdir("/proc/self/fd"))
        child_pids_before = {child.pid for child in multiprocessing.active_children()}

        class PipeFailureContext:
            @staticmethod
            def Pipe(duplex=False):
                raise OSError(errno.EMFILE, "too many open files")

        with mock.patch.object(MODULE["multiprocessing"], "get_context",
                               return_value=PipeFailureContext()), \
                mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "load_config": lambda: cfg,
                    "discover_claude_profiles": lambda _raw: profiles,
                }):
            MODULE["main"]()

        results = json.loads(output.getvalue())["providers"]
        self.assertEqual([item["id"] for item in results], [
            "claude-0", "claude-1", "claude-2",
        ])
        self.assertTrue(all(not item["available"] for item in results))
        self.assertTrue(all("too many open files" in item["error"] for item in results))
        self.assertEqual(len(os.listdir("/proc/self/fd")), descriptor_count_before)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            child_pids_before)

    def test_main_serializes_unexpected_collection_oserror_for_every_profile(self):
        profiles = self.profiles(2)
        for profile in profiles:
            profile["enabled"] = True
        cfg = {
            "providers": ["claude"],
            "claude_profiles": {"manual": [], "overrides": {}},
            "claude_profiles_error": None,
        }
        output = io.StringIO()
        with mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "load_config": lambda: cfg,
                    "discover_claude_profiles": lambda _raw: profiles,
                    "collect_claude_profiles": mock.Mock(
                        side_effect=OSError(errno.EMFILE, "collection exhausted files")),
                }):
            MODULE["main"]()

        results = json.loads(output.getvalue())["providers"]
        self.assertEqual([item["id"] for item in results], ["claude-0", "claude-1"])
        self.assertTrue(all(not item["available"] for item in results))
        self.assertTrue(all(item["windows"] == [] for item in results))
        self.assertTrue(all("collection exhausted files" in item["error"]
                            for item in results))

    def test_process_constructor_failure_closes_pipe_and_preserves_completed_result(self):
        real_context = self.context

        class ConstructorFailureContext:
            process_count = 0

            @staticmethod
            def Pipe(duplex=False):
                return real_context.Pipe(duplex=duplex)

            @classmethod
            def Process(cls, *args, **kwargs):
                cls.process_count += 1
                if cls.process_count == 2:
                    raise RuntimeError("constructor exploded")
                return real_context.Process(*args, **kwargs)

        descriptor_count_before = len(os.listdir("/proc/self/fd"))
        child_pids_before = {child.pid for child in multiprocessing.active_children()}
        with mock.patch.object(MODULE["multiprocessing"], "get_context",
                               return_value=ConstructorFailureContext()):
            results = MODULE["collect_claude_profiles"](
                {}, self.profiles(3),
                provider_call=lambda _cfg, profile, _deadline: {"id": profile["id"]},
                deadline=time.monotonic() + 0.5, max_workers=1)

        self.assertEqual(results[0], {"id": "claude-0"})
        self.assertTrue(all(not item["available"] for item in results[1:]))
        self.assertTrue(all("constructor exploded" in item["error"]
                            for item in results[1:]))
        self.assertEqual(ConstructorFailureContext.process_count, 2)
        self.assertEqual(len(os.listdir("/proc/self/fd")), descriptor_count_before)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            child_pids_before)

    def test_process_start_failure_closes_every_handle_and_stops_scheduling(self):
        real_context = self.context
        failed_processes = []

        class StartFailureProcess:
            def __init__(self):
                self.closed = False

            def start(self):
                raise OSError(errno.EMFILE, "start ran out of file descriptors")

            @staticmethod
            def is_alive():
                return False

            def close(self):
                self.closed = True

        class StartFailureContext:
            process_count = 0

            @staticmethod
            def Pipe(duplex=False):
                return real_context.Pipe(duplex=duplex)

            @classmethod
            def Process(cls, *args, **kwargs):
                cls.process_count += 1
                if cls.process_count == 2:
                    process = StartFailureProcess()
                    failed_processes.append(process)
                    return process
                return real_context.Process(*args, **kwargs)

        descriptor_count_before = len(os.listdir("/proc/self/fd"))
        child_pids_before = {child.pid for child in multiprocessing.active_children()}
        with mock.patch.object(MODULE["multiprocessing"], "get_context",
                               return_value=StartFailureContext()):
            results = MODULE["collect_claude_profiles"](
                {}, self.profiles(3),
                provider_call=lambda _cfg, profile, _deadline: {"id": profile["id"]},
                deadline=time.monotonic() + 0.5, max_workers=1)

        self.assertEqual(results[0], {"id": "claude-0"})
        self.assertTrue(all(not item["available"] for item in results[1:]))
        self.assertTrue(all("start ran out of file descriptors" in item["error"]
                            for item in results[1:]))
        self.assertEqual(StartFailureContext.process_count, 2)
        self.assertTrue(failed_processes[0].closed)
        self.assertEqual(len(os.listdir("/proc/self/fd")), descriptor_count_before)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            child_pids_before)


class ClaudeMultiProfileMainTests(unittest.TestCase):
    def test_one_profile_failure_does_not_affect_other_provider_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cache_dir = home / "cache"
            private_path = home / ".claude"
            work_path = home / ".claude-work"
            private_path.mkdir()
            work_path.mkdir()
            for path, token, plan in (
                    (private_path, "private-token", "max"),
                    (work_path, "work-token", "team")):
                (path / ".credentials.json").write_text(json.dumps({
                    "claudeAiOauth": {"accessToken": token, "subscriptionType": plan},
                }), encoding="utf-8")
            profile_config = {
                "manual": [],
                "overrides": {
                    str(private_path): {"label": "Privat"},
                    str(work_path): {"label": "Arbeit"},
                },
            }
            cfg = {
                "providers": ["claude", "codex", "antigravity"],
                "claude_token": None,
                "claude_token_file": None,
                "claude_local_fallback": False,
                "claude_extra_usage": False,
                "claude_cap_5h": None,
                "claude_cap_7d": None,
                "claude_profiles": profile_config,
                "claude_profiles_error": None,
            }
            output = io.StringIO()
            discover = MODULE["discover_claude_profiles"]

            def fake_get(_url, token, timeout=None):
                self.assertGreater(timeout, 0)
                if token == "work-token":
                    raise RuntimeError("work endpoint unavailable")
                return {"five_hour": {"utilization": 17, "resets_at": 200}}

            def simple_provider(pid, label):
                return MODULE["provider"](pid, label, True, "test", None, [], None)

            with mock.patch.object(MODULE["sys"], "argv", ["ai-usage-json"]), \
                    mock.patch.object(MODULE["sys"], "stdout", output), \
                    mock.patch.object(MODULE["time"], "time", return_value=100), \
                    mock.patch.dict(MODULE["main"].__globals__, {
                        "CACHE_DIR": str(cache_dir),
                        "load_config": lambda: cfg,
                        "discover_claude_profiles": lambda raw: discover(raw, str(home)),
                        "http_get_json": fake_get,
                        "PROVIDERS": {
                            "claude": MODULE["provider_claude"],
                            "codex": lambda _cfg: simple_provider("codex", "Codex"),
                            "antigravity": lambda _cfg: simple_provider(
                                "antigravity", "Antigravity"),
                        },
                    }):
                MODULE["main"]()

            report = json.loads(output.getvalue())

        self.assertEqual(
            [(item["base_provider"], item["label"])
             for item in report["providers"][:2]],
            [("claude", "Privat"), ("claude", "Arbeit")],
        )
        self.assertTrue(report["providers"][0]["available"])
        self.assertFalse(report["providers"][1]["available"])
        self.assertEqual(
            [(item["id"], item["available"]) for item in report["providers"][2:]],
            [("codex", True), ("antigravity", True)],
        )
        self.assertEqual(len({item["id"] for item in report["providers"]}),
                         len(report["providers"]))
        self.assertNotIn("accessToken", json.dumps(report))


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
        self.profile = self.home / ".claude"
        self.settings = self.profile / "settings.json"
        self.profile.mkdir(parents=True)
        self.cache_dir = self.home / ".cache/plasma-ai-usage"
        state_suffix = hashlib.sha256(
            os.path.realpath(self.settings).encode("utf-8")
        ).hexdigest()[:12]
        self.state = self.cache_dir / f"claude-integration-{state_suffix}.json"
        self.legacy_state = self.cache_dir / "claude-integration.json"
        self.paths = {
            "CACHE_DIR": str(self.cache_dir),
            "CLAUDE_SETTINGS": str(self.settings),
            "CLAUDE_INTEGRATION_STATE": str(self.legacy_state),
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
            result = MODULE["claude_integration_status"](str(self.profile))
        self.assertEqual(result["state"], "not-configured")
        self.assertTrue(result["can_setup"])

    def test_missing_claude_cannot_be_set_up(self):
        with mock.patch.dict(
            MODULE["claude_integration_status"].__globals__,
            {**self.paths, "claude_version": lambda: None},
        ):
            result = MODULE["claude_integration_status"](str(self.profile))
        self.assertFalse(result["can_setup"])

    def test_installed_claude_without_settings_shows_sign_in_guidance(self):
        with self.claude_found():
            result = MODULE["claude_integration_status"](str(self.profile))
        self.assertEqual(result["state"], "not-configured")
        self.assertFalse(result["can_setup"])
        self.assertEqual(result["message"], "Claude Code must be installed and signed in first.")

    def test_malformed_existing_settings_is_an_error(self):
        self.settings.write_text("{bad", encoding="utf-8")
        with self.claude_found():
            result = MODULE["claude_integration_status"](str(self.profile))
        self.assertEqual(result["state"], "error")

    def test_malformed_existing_integration_state_is_an_error(self):
        self.settings.write_text("{}", encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.state.write_text("{bad", encoding="utf-8")

        with self.claude_found():
            result = MODULE["claude_integration_setup"](str(self.profile))

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "error")
        self.assertEqual(self.state.read_text(encoding="utf-8"), "{bad")

    def test_incomplete_state_reviewer_repro_fails_hard_for_every_action(self):
        managed = "managed collector command"
        incomplete = {
            "settings_realpath": os.path.realpath(self.settings),
            "managed_command": managed,
        }
        for action_name in (
                "claude_integration_status",
                "claude_integration_setup",
                "claude_integration_remove"):
            with self.subTest(action=action_name):
                self.settings.write_text(json.dumps({
                    "theme": "dark",
                    "statusLine": {"type": "command", "command": managed},
                }), encoding="utf-8")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self.state.write_text(json.dumps(incomplete), encoding="utf-8")
                before = self.settings.read_bytes()

                with self.claude_found():
                    result = MODULE[action_name](str(self.profile))

                self.assertEqual(result["state"], "error")
                self.assertIn("state is invalid", result["message"].lower())
                if action_name != "claude_integration_status":
                    self.assertFalse(result["ok"])
                self.assertEqual(self.settings.read_bytes(), before)

    def test_invalid_state_schema_is_rejected_without_changing_settings(self):
        managed = "managed collector command"
        valid = {
            "settings_realpath": os.path.realpath(self.settings),
            "managed_command": managed,
            "previous_statusline_present": False,
            "previous_statusline": None,
        }
        invalid_states = {
            "missing settings path": {key: value for key, value in valid.items()
                                      if key != "settings_realpath"},
            "empty settings path": {**valid, "settings_realpath": ""},
            "non-string settings path": {**valid, "settings_realpath": 1},
            "missing managed command": {key: value for key, value in valid.items()
                                        if key != "managed_command"},
            "empty managed command": {**valid, "managed_command": ""},
            "non-string managed command": {**valid, "managed_command": []},
            "missing presence flag": {key: value for key, value in valid.items()
                                      if key != "previous_statusline_present"},
            "non-bool presence flag": {**valid, "previous_statusline_present": 0},
            "false without previous value": {key: value for key, value in valid.items()
                                             if key != "previous_statusline"},
            "false with previous object": {**valid, "previous_statusline": {}},
            "true without previous value": {
                **{key: value for key, value in valid.items()
                   if key != "previous_statusline"},
                "previous_statusline_present": True,
            },
            "true with invalid previous value": {
                **valid,
                "previous_statusline_present": True,
                "previous_statusline": "invalid",
            },
        }
        actions = (
            "claude_integration_status",
            "claude_integration_setup",
            "claude_integration_remove",
        )
        for case, state_data in invalid_states.items():
            for action_name in actions:
                with self.subTest(case=case, action=action_name):
                    self.settings.write_text(json.dumps({
                        "theme": "dark",
                        "statusLine": {"type": "command", "command": managed},
                    }), encoding="utf-8")
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    self.state.write_text(json.dumps(state_data), encoding="utf-8")
                    before = self.settings.read_bytes()

                    with self.claude_found():
                        result = MODULE[action_name](str(self.profile))

                    self.assertEqual(result["state"], "error")
                    self.assertIn("state is invalid", result["message"].lower())
                    if action_name != "claude_integration_status":
                        self.assertFalse(result["ok"])
                    self.assertEqual(self.settings.read_bytes(), before)

    def test_present_null_statusline_is_restored_as_present_null(self):
        self.settings.write_text(json.dumps({"statusLine": None}), encoding="utf-8")

        with self.claude_found():
            setup = MODULE["claude_integration_setup"](str(self.profile))
            removed = MODULE["claude_integration_remove"](str(self.profile))

        self.assertTrue(setup["ok"])
        self.assertTrue(removed["ok"])
        restored = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertIn("statusLine", restored)
        self.assertIsNone(restored["statusLine"])

    def test_migrates_legacy_default_integration_without_previous_statusline(self):
        managed = MODULE["managed_claude_command"](None)
        self.settings.write_text(json.dumps({
            "theme": "dark",
            "statusLine": {"type": "command", "command": managed},
        }), encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.legacy_state.write_text(json.dumps({
            "previous_statusline_present": False,
            "previous_statusline": None,
            "managed_command": managed,
        }), encoding="utf-8")

        with self.claude_found():
            status = MODULE["claude_integration_status"](str(self.profile))
            migrated = json.loads(self.state.read_text(encoding="utf-8"))
            removed = MODULE["claude_integration_remove"](str(self.profile))

        self.assertEqual(status["state"], "configured")
        self.assertEqual(migrated["settings_realpath"], os.path.realpath(self.settings))
        self.assertFalse(self.legacy_state.exists())
        self.assertTrue(removed["ok"])
        restored = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(restored, {"theme": "dark"})

    def test_migrates_legacy_default_integration_and_restores_previous_statusline(self):
        previous = {"type": "command", "command": "old statusline", "padding": 4}
        managed = MODULE["managed_claude_command"](previous)
        installed = {**previous, "command": managed}
        self.settings.write_text(json.dumps({
            "theme": "dark", "statusLine": installed,
        }), encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.legacy_state.write_text(json.dumps({
            "previous_statusline_present": True,
            "previous_statusline": previous,
            "managed_command": managed,
        }), encoding="utf-8")

        with self.claude_found():
            status = MODULE["claude_integration_status"](str(self.profile))
            removed = MODULE["claude_integration_remove"](str(self.profile))

        self.assertEqual(status["state"], "configured")
        self.assertTrue(removed["ok"])
        restored = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(restored["statusLine"], previous)

    def test_legacy_state_for_already_restored_settings_is_recognized(self):
        managed = MODULE["managed_claude_command"](None)
        self.settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.legacy_state.write_text(json.dumps({
            "previous_statusline_present": False,
            "previous_statusline": None,
            "managed_command": managed,
        }), encoding="utf-8")

        with self.claude_found():
            status = MODULE["claude_integration_status"](str(self.profile))
            removed = MODULE["claude_integration_remove"](str(self.profile))

        self.assertEqual(status["state"], "not-configured")
        self.assertTrue(status["can_setup"])
        self.assertTrue(removed["ok"])
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")),
                         {"theme": "dark"})

    def test_invalid_legacy_state_fails_hard_and_is_not_deleted(self):
        managed = MODULE["managed_claude_command"](None)
        invalid = {
            "managed_command": managed,
            "previous_statusline_present": False,
        }
        for action_name in (
                "claude_integration_status",
                "claude_integration_setup",
                "claude_integration_remove"):
            with self.subTest(action=action_name):
                self.settings.write_text(json.dumps({
                    "statusLine": {"type": "command", "command": managed},
                }), encoding="utf-8")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self.legacy_state.write_text(json.dumps(invalid), encoding="utf-8")
                self.state.unlink(missing_ok=True)
                before = self.settings.read_bytes()

                with self.claude_found():
                    result = MODULE[action_name](str(self.profile))

                self.assertEqual(result["state"], "error")
                if action_name != "claude_integration_status":
                    self.assertFalse(result["ok"])
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertTrue(self.legacy_state.exists())
                self.assertFalse(self.state.exists())

    def test_legacy_migration_does_not_overwrite_state_created_during_race(self):
        managed = MODULE["managed_claude_command"](None)
        self.settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": managed},
        }), encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.legacy_state.write_text(json.dumps({
            "previous_statusline_present": False,
            "previous_statusline": None,
            "managed_command": managed,
        }), encoding="utf-8")
        raced_state = {
            "settings_realpath": os.path.realpath(self.settings),
            "previous_statusline_present": False,
            "previous_statusline": None,
            "managed_command": managed,
            "created_by": "racer",
        }

        def race_create(destination, _data, destination_dir):
            MODULE["atomic_write_json"](destination, raced_state, destination_dir)
            return False

        with self.claude_found(), mock.patch.dict(
                MODULE["claude_integration_status"].__globals__, {
                    "atomic_create_json": race_create,
                }):
            status = MODULE["claude_integration_status"](str(self.profile))

        self.assertEqual(status["state"], "configured")
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8")), raced_state)
        self.assertFalse(self.legacy_state.exists())

    def test_setup_refuses_to_chain_an_existing_self_wrapper_without_state(self):
        managed = MODULE["managed_claude_command"](None)
        self.settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": managed},
        }), encoding="utf-8")
        before = self.settings.read_bytes()

        with self.claude_found():
            status = MODULE["claude_integration_status"](str(self.profile))
            setup = MODULE["claude_integration_setup"](str(self.profile))

        self.assertEqual(status["state"], "error")
        self.assertIn("collector wrapper", status["message"].lower())
        self.assertFalse(setup["ok"])
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(
            json.loads(self.settings.read_text(encoding="utf-8"))
            ["statusLine"]["command"].count(os.path.abspath(MODULE["CLAUDE_COLLECTOR"])),
            1,
        )

    def test_current_state_rejects_nested_direct_self_wrapper_for_every_action(self):
        previous = {
            "type": "command",
            "command": os.path.abspath(MODULE["CLAUDE_COLLECTOR"]),
        }
        managed = MODULE["managed_claude_command"](previous)
        state_data = {
            "settings_realpath": os.path.realpath(self.settings),
            "previous_statusline_present": True,
            "previous_statusline": previous,
            "managed_command": managed,
        }
        installed = {**previous, "command": managed}

        for action_name in (
                "claude_integration_status",
                "claude_integration_setup",
                "claude_integration_remove"):
            with self.subTest(action=action_name):
                self.settings.write_text(json.dumps({
                    "theme": "dark", "statusLine": installed,
                }), encoding="utf-8")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self.state.write_text(json.dumps(state_data), encoding="utf-8")
                before = self.settings.read_bytes()

                with self.claude_found():
                    result = MODULE[action_name](str(self.profile))

                self.assertEqual(result["state"], "error")
                if action_name != "claude_integration_status":
                    self.assertFalse(result["ok"])
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertEqual(
                    json.loads(self.state.read_text(encoding="utf-8")),
                    state_data,
                )

    def test_legacy_state_rejects_nested_managed_wrapper_for_every_action(self):
        original = {"type": "command", "command": "printf original"}
        previous = {
            "type": "command",
            "command": MODULE["managed_claude_command"](original),
        }
        managed = MODULE["managed_claude_command"](previous)
        legacy_state = {
            "previous_statusline_present": True,
            "previous_statusline": previous,
            "managed_command": managed,
        }
        installed = {**previous, "command": managed}

        for action_name in (
                "claude_integration_status",
                "claude_integration_setup",
                "claude_integration_remove"):
            with self.subTest(action=action_name):
                self.settings.write_text(json.dumps({
                    "theme": "dark", "statusLine": installed,
                }), encoding="utf-8")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self.legacy_state.write_text(
                    json.dumps(legacy_state), encoding="utf-8")
                self.state.unlink(missing_ok=True)
                before = self.settings.read_bytes()

                with self.claude_found():
                    result = MODULE[action_name](str(self.profile))

                self.assertEqual(result["state"], "error")
                if action_name != "claude_integration_status":
                    self.assertFalse(result["ok"])
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertTrue(self.legacy_state.exists())
                self.assertFalse(self.state.exists())

    def test_prefixed_and_malformed_self_wrappers_fail_hard(self):
        collector = os.path.abspath(MODULE["CLAUDE_COLLECTOR"])
        commands = {
            "env": "env " + shlex.quote(collector),
            "env option and assignment": (
                "env -i PROFILE=work " + shlex.quote(collector)),
            "shell assignment": "PROFILE=work " + shlex.quote(collector),
            "malformed": "'unterminated",
        }

        for case, command in commands.items():
            with self.subTest(case=case):
                current = {"type": "command", "command": command}
                self.settings.write_text(
                    json.dumps({"theme": "dark", "statusLine": current}),
                    encoding="utf-8",
                )
                self.state.unlink(missing_ok=True)
                self.legacy_state.unlink(missing_ok=True)
                before = self.settings.read_bytes()

                with self.claude_found():
                    status = MODULE["claude_integration_status"](str(self.profile))
                    setup = MODULE["claude_integration_setup"](str(self.profile))

                self.assertEqual(status["state"], "error")
                self.assertFalse(setup["ok"])
                self.assertEqual(setup["state"], "error")
                self.assertEqual(self.settings.read_bytes(), before)
                self.assertFalse(self.state.exists())

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
                result = MODULE["claude_integration_status"](str(self.profile))
            self.assertEqual(result["can_setup"], supported)
            self.assertIn("support", result["message"].lower())

    def test_setup_preserves_options_and_remove_restores_previous_value(self):
        previous = {"type": "command", "command": "printf '%s' ok", "padding": 3}
        self.settings.write_text(json.dumps({"theme": "dark", "statusLine": previous}), encoding="utf-8")
        with self.claude_found():
            setup = MODULE["claude_integration_setup"](str(self.profile))
            installed = json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]
            managed_command = json.loads(self.state.read_text(encoding="utf-8"))["managed_command"]
            self.assertTrue(setup["ok"])
            self.assertEqual(installed["command"], managed_command)
            self.assertNotIn("CLAUDE_CONFIG_DIR", managed_command)
            self.assertEqual(installed["type"], "command")
            self.assertEqual(installed["padding"], 3)
            removed = MODULE["claude_integration_remove"](str(self.profile))
        self.assertTrue(removed["ok"])
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"], previous)

    def test_setup_is_idempotent(self):
        previous = {"type": "command", "command": "old", "padding": 2}
        self.settings.write_text(json.dumps({"statusLine": previous}), encoding="utf-8")
        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"](str(self.profile))["ok"])
            first_state = self.state.read_bytes()
            self.assertTrue(MODULE["claude_integration_setup"](str(self.profile))["ok"])
        self.assertEqual(self.state.read_bytes(), first_state)
        self.assertEqual(json.loads(first_state)["previous_statusline"], previous)

    def test_remove_refuses_user_modified_command(self):
        self.settings.write_text(json.dumps({"statusLine": {"command": "old", "padding": 1}}), encoding="utf-8")
        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"](str(self.profile))["ok"])
            changed = json.loads(self.settings.read_text(encoding="utf-8"))
            changed["statusLine"]["command"] = "user replacement"
            self.settings.write_text(json.dumps(changed), encoding="utf-8")
            before = self.settings.read_bytes()
            result = MODULE["claude_integration_remove"](str(self.profile))
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
            self.assertTrue(MODULE["claude_integration_setup"](str(self.profile))["ok"])
            with mock.patch.object(MODULE["os"], "unlink", side_effect=fail_state_unlink_once):
                first = MODULE["claude_integration_remove"](str(self.profile))
            self.assertFalse(first["ok"])
            self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"], previous)
            self.assertTrue(self.state.exists())
            retry = MODULE["claude_integration_remove"](str(self.profile))
        self.assertTrue(retry["ok"])
        self.assertFalse(self.state.exists())

    def test_integration_state_paths_are_separate_for_separate_settings(self):
        work = self.home / ".claude-work/settings.json"

        private_state = MODULE["claude_integration_state_path"](str(self.settings))
        work_state = MODULE["claude_integration_state_path"](str(work))

        self.assertNotEqual(private_state, work_state)

    def test_invalid_profile_path_is_rejected(self):
        missing = self.home / "missing-profile"

        with self.claude_found():
            result = MODULE["claude_integration_status"](str(missing))

        self.assertEqual(result["state"], "error")
        self.assertFalse(result["can_setup"])
        self.assertIn("profile", result["message"].lower())

    def test_symlink_shared_settings_use_one_management_state(self):
        shared = self.home / "shared-settings.json"
        shared.write_text("{}", encoding="utf-8")
        work_profile = self.home / ".claude-work"
        work_profile.mkdir()
        self.settings.unlink(missing_ok=True)
        self.settings.symlink_to(shared)
        work_settings = work_profile / "settings.json"
        work_settings.symlink_to(shared)

        with self.claude_found():
            setup = MODULE["claude_integration_setup"](str(self.profile))
            work_status = MODULE["claude_integration_status"](str(work_profile))
            removed = MODULE["claude_integration_remove"](str(work_profile))

        self.assertTrue(setup["ok"])
        self.assertEqual(work_status["state"], "configured")
        self.assertTrue(removed["ok"])
        self.assertEqual(
            MODULE["claude_integration_state_path"](str(self.settings)),
            MODULE["claude_integration_state_path"](str(work_settings)),
        )

    def test_setup_records_real_settings_target(self):
        self.settings.write_text("{}", encoding="utf-8")

        with self.claude_found():
            result = MODULE["claude_integration_setup"](str(self.profile))

        self.assertTrue(result["ok"])
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["settings_realpath"], os.path.realpath(self.settings))

    def test_setup_refuses_state_for_a_different_settings_target(self):
        self.settings.write_text("{}", encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.state.write_text(json.dumps({
            "settings_realpath": str(self.home / "different-settings.json"),
            "managed_command": "managed elsewhere",
            "previous_statusline_present": False,
            "previous_statusline": None,
        }), encoding="utf-8")

        with self.claude_found():
            result = MODULE["claude_integration_setup"](str(self.profile))

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "error")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{}")

    def test_setup_and_remove_preserve_settings_parent_and_file_modes(self):
        self.settings.write_text("{}", encoding="utf-8")
        os.chmod(self.profile, 0o751)
        os.chmod(self.settings, 0o640)

        with self.claude_found():
            setup = MODULE["claude_integration_setup"](str(self.profile))
            setup_modes = (
                self.profile.stat().st_mode & 0o777,
                self.settings.stat().st_mode & 0o777,
                self.cache_dir.stat().st_mode & 0o777,
                self.state.stat().st_mode & 0o777,
            )
            removed = MODULE["claude_integration_remove"](str(self.profile))

        self.assertTrue(setup["ok"])
        self.assertTrue(removed["ok"])
        self.assertEqual(setup_modes, (0o751, 0o640, 0o700, 0o600))
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o751)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o640)

    def test_symlink_retarget_between_resolution_and_lock_fails_hard(self):
        first = self.home / "settings-first.json"
        second = self.home / "settings-second.json"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        self.settings.symlink_to(first)
        real_flock = MODULE["fcntl"].flock
        retargeted = False

        def flock_and_retarget(fd, operation):
            nonlocal retargeted
            result = real_flock(fd, operation)
            if not retargeted and operation & fcntl.LOCK_EX:
                retargeted = True
                self.settings.unlink()
                self.settings.symlink_to(second)
            return result

        with self.claude_found(), mock.patch.object(
                MODULE["fcntl"], "flock", side_effect=flock_and_retarget):
            result = MODULE["claude_integration_setup"](str(self.profile))

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "error")
        self.assertIn("changed", result["message"].lower())
        self.assertEqual(first.read_text(encoding="utf-8"), "{}")
        self.assertEqual(second.read_text(encoding="utf-8"), "{}")
        self.assertFalse(self.state.exists())

    def test_remove_does_not_unlink_state_replaced_during_operation(self):
        previous = {"type": "command", "command": "old"}
        self.settings.write_text(json.dumps({"statusLine": previous}), encoding="utf-8")
        replacement_state = {
            "settings_realpath": os.path.realpath(self.settings),
            "managed_command": "replacement command",
            "previous_statusline_present": False,
            "previous_statusline": None,
        }

        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"](str(self.profile))["ok"])

            def replace_state_after_settings_write(path, data):
                MODULE["atomic_write_json"](path, data, os.path.dirname(path))
                self.state.write_text(json.dumps(replacement_state), encoding="utf-8")

            with mock.patch.dict(MODULE["claude_integration_remove"].__globals__, {
                    "atomic_write_settings_json": replace_state_after_settings_write,
            }):
                result = MODULE["claude_integration_remove"](str(self.profile))

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "error")
        self.assertTrue(self.state.exists())
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8")),
                         replacement_state)

    def test_integration_lock_wait_is_bounded_and_private(self):
        self.settings.write_text("{}", encoding="utf-8")
        with mock.patch.dict(MODULE["claude_integration_lock_path"].__globals__, {
                "CACHE_DIR": str(self.cache_dir),
        }):
            lock_path = Path(MODULE["claude_integration_lock_path"](str(self.settings)))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)

        started = time.monotonic()
        with self.claude_found(), mock.patch.dict(
                MODULE["claude_integration_status"].__globals__, {
                    "CLAUDE_INTEGRATION_LOCK_TIMEOUT": 0.02,
                }):
            result = MODULE["claude_integration_status"](str(self.profile))

        self.assertEqual(result["state"], "error")
        self.assertIn("lock timed out", result["message"].lower())
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(lock_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_setup_and_remove_are_serialized_across_processes(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires Linux fork semantics")
        self.settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "old"},
        }), encoding="utf-8")
        with self.claude_found():
            self.assertTrue(MODULE["claude_integration_setup"](str(self.profile))["ok"])

        context = multiprocessing.get_context("fork")
        first_entered = context.Event()
        release_first = context.Event()
        overlap = context.Value("b", False)
        active = context.Value("i", 0)
        results = context.Queue()
        real_load = MODULE["claude_load_integration_state"]

        def tracked_load(paths, settings):
            with active.get_lock():
                active.value += 1
                if active.value > 1:
                    overlap.value = True
                is_first = active.value == 1 and not first_entered.is_set()
            if is_first:
                first_entered.set()
                release_first.wait(timeout=1)
            try:
                return real_load(paths, settings)
            finally:
                with active.get_lock():
                    active.value -= 1

        def worker(action):
            results.put(MODULE[action](str(self.profile)))

        with self.claude_found(), mock.patch.dict(
                MODULE["claude_integration_status"].__globals__, {
                    "claude_load_integration_state": tracked_load,
                }):
            remove_process = context.Process(
                target=worker, args=("claude_integration_remove",))
            setup_process = context.Process(
                target=worker, args=("claude_integration_setup",))
            remove_process.start()
            self.assertTrue(first_entered.wait(timeout=1))
            setup_process.start()
            time.sleep(0.05)
            release_first.set()
            for process in (remove_process, setup_process):
                process.join(3)
                self.assertEqual(process.exitcode, 0)

        returned = [results.get(timeout=1), results.get(timeout=1)]
        self.assertFalse(overlap.value)
        self.assertTrue(all(result["ok"] for result in returned))

    def test_concurrent_legacy_migration_is_serialized(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires Linux fork semantics")
        managed = MODULE["managed_claude_command"](None)
        self.settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": managed},
        }), encoding="utf-8")
        self.cache_dir.mkdir(parents=True)
        self.legacy_state.write_text(json.dumps({
            "previous_statusline_present": False,
            "previous_statusline": None,
            "managed_command": managed,
        }), encoding="utf-8")
        context = multiprocessing.get_context("fork")
        first_entered = context.Event()
        release_first = context.Event()
        overlap = context.Value("b", False)
        active = context.Value("i", 0)
        results = context.Queue()
        real_create = MODULE["atomic_create_json"]

        def tracked_create(path, data, destination_dir):
            with active.get_lock():
                active.value += 1
                if active.value > 1:
                    overlap.value = True
                is_first = active.value == 1 and not first_entered.is_set()
            if is_first:
                first_entered.set()
                release_first.wait(timeout=1)
            try:
                return real_create(path, data, destination_dir)
            finally:
                with active.get_lock():
                    active.value -= 1

        def worker():
            results.put(MODULE["claude_integration_status"](str(self.profile)))

        with self.claude_found(), mock.patch.dict(
                MODULE["claude_integration_status"].__globals__, {
                    "atomic_create_json": tracked_create,
                }):
            first = context.Process(target=worker)
            second = context.Process(target=worker)
            first.start()
            self.assertTrue(first_entered.wait(timeout=1))
            second.start()
            time.sleep(0.05)
            release_first.set()
            for process in (first, second):
                process.join(3)
                self.assertEqual(process.exitcode, 0)

        returned = [results.get(timeout=1), results.get(timeout=1)]
        self.assertFalse(overlap.value)
        self.assertTrue(all(result["state"] == "configured" for result in returned))
        self.assertFalse(self.legacy_state.exists())

    def test_cli_requires_exact_action_and_profile_arguments(self):
        invalid_argv = (
            ["ai-usage-json", "--claude-integration", "status"],
            ["ai-usage-json", "--claude-integration", "unknown", "--profile", str(self.profile)],
            ["ai-usage-json", "--claude-integration", "status", "--profile",
             str(self.profile), "extra"],
        )
        for argv in invalid_argv:
            with self.subTest(argv=argv):
                output = io.StringIO()
                with mock.patch.object(MODULE["sys"], "argv", argv), \
                        mock.patch.object(MODULE["sys"], "stdout", output):
                    MODULE["main"]()
                result = json.loads(output.getvalue())
                self.assertFalse(result["ok"])
                self.assertEqual(result["state"], "error")

    def test_cli_passes_profile_to_requested_action(self):
        output = io.StringIO()
        calls = []
        argv = ["ai-usage-json", "--claude-integration", "status",
                "--profile", str(self.profile)]

        with mock.patch.object(MODULE["sys"], "argv", argv), \
                mock.patch.object(MODULE["sys"], "stdout", output), \
                mock.patch.dict(MODULE["main"].__globals__, {
                    "claude_integration_status": lambda profile: (
                        calls.append(profile) or {"state": "not-configured"}),
                }):
            MODULE["main"]()

        self.assertEqual(calls, [str(self.profile)])
        self.assertEqual(json.loads(output.getvalue())["state"], "not-configured")


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

    def profile(self, canonical_path="/profile"):
        return {
            "id": "claude-test",
            "base_provider": "claude",
            "label": "Claude",
            "path": canonical_path,
            "canonical_path": canonical_path,
            "automatic": True,
            "enabled": True,
        }

    def provider_with(self, collector, oauth=None, now=10_000, cfg=None,
                      http_get=None, profile=None, credentials=mock.DEFAULT,
                      deadline=None, finish=None, claim=None):
        provider_claude = MODULE["provider_claude"]
        paths = {
            "collector_cache": "/collector",
            "oauth_cache": "/oauth",
            "oauth_lock": "/oauth-lock",
            "credentials": "/credentials",
            "projects": "/projects",
            "settings": "/settings",
        }

        def read(path):
            return collector if path == "/collector" else oauth if path == "/oauth" else None

        def read_state(path, _label="JSON file"):
            if path == "/credentials":
                if credentials is mock.DEFAULT:
                    return "missing", None, None
                return "valid", credentials, None
            value = read(path)
            return ("missing", None, None) if value is None else ("valid", value, None)

        replacements = {
            "read_cache": read, "read_json": lambda _path: None,
            "read_json_state": read_state,
            "claude_profile_paths": lambda _profile: paths,
            "claude_claim_oauth_attempt": (
                claim if claim is not None else
                lambda _now, _cache, _lock, deadline=None: (True, oauth or {}, None)),
            "claude_finish_oauth_attempt": (
                finish if finish is not None else lambda *_args, **_kwargs: None),
        }
        if http_get is not None:
            replacements["http_get_json"] = http_get
        with mock.patch.object(provider_claude.__globals__["time"], "time", return_value=now), \
                mock.patch.dict(provider_claude.__globals__, replacements):
            return provider_claude(
                cfg or self.cfg(), profile or self.profile(), deadline=deadline)

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

    def test_fresh_collector_bypasses_malformed_default_credentials(self):
        result = self.provider_with({
            "fetched_at": 9_500,
            "rate_limits": {
                "five_hour": {"used_percentage": 12, "resets_at": 11_000},
            },
        }, credentials=[])

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "statusline")
        self.assertIsNone(result["plan"])

    def test_valid_oauth_cache_survives_malformed_default_credentials_with_warning(self):
        result = self.provider_with(None, {
            "fetched_at": 9_500,
            "last_attempt_at": 9_500,
            "data": {"five_hour": {"utilization": 31, "resets_at": 11_000}},
            "retry_after_until": 0,
        }, credentials=[])

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api-cached")
        self.assertEqual(result["windows"][0]["used_percent"], 31)
        self.assertIn("credentials", result["error"].lower())
        self.assertIn("must contain a JSON object", result["error"])

    def test_local_estimate_survives_malformed_default_credentials_with_warning(self):
        estimate = [MODULE["window"](
            "5h", "5-Hour", None, None, detail="~7 tok (no cap set)")]
        provider = MODULE["provider_claude"]
        with mock.patch.dict(provider.__globals__, {
                "claude_local_estimate": lambda *_args, **_kwargs: estimate,
                }):
            result = self.provider_with(
                None, credentials=[],
                cfg={**self.cfg(), "claude_local_fallback": True})

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "local-estimate")
        self.assertEqual(result["windows"], estimate)
        self.assertIn("credentials", result["error"].lower())
        self.assertIn("must contain a JSON object", result["error"])

    def test_explicit_token_bypasses_malformed_default_credentials(self):
        result = self.provider_with(
            None,
            cfg={**self.cfg(), "claude_token": "explicit-token"},
            credentials=[],
            http_get=lambda _url, token, timeout=None: {
                "five_hour": {"utilization": 12, "resets_at": 11_000},
                "token_seen": token,
            })

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertIsNone(result["plan"])

    def test_configured_token_file_bypasses_malformed_default_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token"
            token_file.write_text("configured-token\n", encoding="utf-8")
            seen = []
            result = self.provider_with(
                None,
                cfg={**self.cfg(), "claude_token_file": str(token_file)},
                credentials=[],
                http_get=lambda _url, token, timeout=None: (
                    seen.append(token) or {
                        "five_hour": {"utilization": 12, "resets_at": 11_000},
                    }))

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertEqual(seen, ["configured-token"])
        self.assertIsNone(result["plan"])

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

    def test_oauth_http_timeout_uses_only_remaining_provider_deadline(self):
        cfg = {**self.cfg(), "claude_token": "test-token"}
        timeouts = []

        result = self.provider_with(
            None, cfg=cfg, deadline=time.monotonic() + 0.05,
            http_get=lambda _url, _token, timeout: (
                timeouts.append(timeout) or
                {"five_hour": {"utilization": 12, "resets_at": 11_000}}))

        self.assertTrue(result["available"])
        self.assertEqual(len(timeouts), 1)
        self.assertGreater(timeouts[0], 0)
        self.assertLessEqual(timeouts[0], 0.05)

    def test_expired_deadline_after_http_failure_skips_local_scan(self):
        cfg = {**self.cfg(), "claude_token": "test-token",
               "claude_local_fallback": True}
        provider = MODULE["provider_claude"]

        def fail_after_deadline(*_args, **_kwargs):
            time.sleep(0.02)
            raise TimeoutError("timed out")

        with mock.patch.dict(provider.__globals__, {
                "claude_local_estimate": lambda *_args, **_kwargs: self.fail(
                    "local scan must not start after the absolute deadline")
                }):
            result = self.provider_with(
                None, cfg=cfg, deadline=time.monotonic() + 0.01,
                http_get=fail_after_deadline)

        self.assertFalse(result["available"])
        self.assertIn("helper deadline exceeded", result["error"])

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
                    "claude_profile_paths": lambda _profile: {
                        "collector_cache": "/collector", "oauth_cache": "/oauth",
                        "oauth_lock": "/oauth-lock", "credentials": "/credentials",
                        "projects": "/projects", "settings": "/settings",
                    },
                    "claude_claim_oauth_attempt": lambda now, _cache, _lock, deadline=None: (
                        writes.append({**old, "last_attempt_at": now}) or
                        (True, {**old, "last_attempt_at": now}, None)),
                }):
            provider(cfg, self.profile())
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
                    "claude_profile_paths": lambda _profile: {
                        "collector_cache": "/collector", "oauth_cache": "/oauth",
                        "oauth_lock": "/oauth-lock", "credentials": "/credentials",
                        "projects": "/projects", "settings": "/settings",
                    },
                    "claude_claim_oauth_attempt": lambda now, _cache, _lock, deadline=None: (
                        writes.append({"fetched_at": None, "last_attempt_at": now,
                                       "data": None, "retry_after_until": 0}) or
                        (True, writes[-1], None)),
                }):
            provider(cfg, self.profile())
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

    def test_live_api_rejects_entire_payload_when_any_inner_block_is_invalid(self):
        stale_data = {
            "five_hour": {"utilization": 31, "resets_at": 11_000},
        }
        oauth = {
            "fetched_at": 9_000,
            "last_attempt_at": 9_000,
            "data": stale_data,
            "retry_after_until": 0,
        }
        invalid_payloads = (
            {
                "five_hour": {"utilization": 12, "resets_at": 11_000},
                "seven_day": {"utilization": "bad", "resets_at": 12_000},
            },
            {
                "five_hour": {"utilization": 12, "resets_at": 11_000},
                "extra_usage": {"is_enabled": True, "monthly_limit": -1},
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                published = []
                calls = []

                def http_get(*_args, **_kwargs):
                    calls.append(1)
                    return payload

                def finish(*_args, **kwargs):
                    published.append(kwargs.get("data"))

                first = self.provider_with(
                    None, oauth, cfg={**self.cfg(), "claude_token": "token"},
                    http_get=http_get, finish=finish)
                second = self.provider_with(
                    None, oauth, cfg={**self.cfg(), "claude_token": "token"},
                    http_get=http_get, finish=finish)

                self.assertEqual(calls, [1, 1])
                self.assertEqual(published, [])
                for result in (first, second):
                    self.assertTrue(result["available"])
                    self.assertEqual(result["source"], "api-cached")
                    self.assertEqual(result["windows"][0]["used_percent"], 31)
                    self.assertIn("usage API data schema error", result["error"])

    def test_invalid_live_api_without_stale_cache_is_explicitly_unavailable(self):
        result = self.provider_with(
            None, cfg={**self.cfg(), "claude_token": "token"},
            http_get=lambda *_args, **_kwargs: {
                "five_hour": {"utilization": 12, "resets_at": 11_000},
                "seven_day": {"utilization": 23, "resets_at": "not-a-date"},
            })

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("usage API data schema error", result["error"])
        self.assertIn("data.seven_day.resets_at", result["error"])

    def test_live_api_schema_allows_verified_empty_and_nullable_blocks(self):
        self.assertIsNone(MODULE["claude_api_data_schema_error"]({
            "five_hour": {},
            "seven_day": None,
            "seven_day_opus": None,
            "extra_usage": None,
        }))

    def test_fully_valid_live_api_payload_is_rendered_and_published(self):
        payload = {
            "five_hour": {"utilization": 12, "resets_at": 11_000},
            "seven_day": {"utilization": 23, "resets_at": 12_000},
            "seven_day_opus": None,
            "seven_day_sonnet": {"utilization": 34, "resets_at": 13_000},
            "extra_usage": {
                "is_enabled": True,
                "utilization": 45,
                "used_credits": 450,
                "monthly_limit": 1000,
                "decimal_places": 0,
                "currency": "USD",
            },
        }
        published = []

        result = self.provider_with(
            None,
            cfg={**self.cfg(), "claude_token": "token",
                 "claude_extra_usage": True},
            http_get=lambda *_args, **_kwargs: payload,
            finish=lambda *_args, **kwargs: published.append(kwargs.get("data")))

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertEqual([item["key"] for item in result["windows"]], [
            "5h", "7d", "7d-sonnet", "extra-usage",
        ])
        self.assertEqual(published, [payload])

    def test_malformed_cached_oauth_container_is_honestly_unavailable(self):
        result = self.provider_with(None, {
            "fetched_at": 9_500, "last_attempt_at": 9_500, "data": ["bad"]})
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])

    def test_present_oauth_container_must_be_an_object(self):
        for oauth in ([], False, ""):
            with self.subTest(oauth=oauth):
                result = self.provider_with(
                    None, credentials={"claudeAiOauth": oauth})

            self.assertFalse(result["available"])
            self.assertIn("invalid OAuth data", result["error"])

    def test_credentials_root_must_be_an_object(self):
        for credentials in (None, [], False, ""):
            with self.subTest(credentials=credentials):
                result = self.provider_with(None, credentials=credentials)

            self.assertFalse(result["available"])
            self.assertIn("must contain a JSON object", result["error"])

    def test_malformed_default_credentials_fail_when_no_earlier_source_exists(self):
        result = self.provider_with(None, credentials=[])

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("must contain a JSON object", result["error"])

    def test_missing_or_null_oauth_container_is_unsigned(self):
        for credentials in ({}, {"claudeAiOauth": None}):
            with self.subTest(credentials=credentials):
                result = self.provider_with(None, credentials=credentials)

            self.assertFalse(result["available"])
            self.assertEqual(result["error"], "no access token for profile")

    def test_present_oauth_fields_require_valid_types(self):
        invalid_oauth = (
            {"accessToken": ""},
            {"accessToken": False},
            {"accessToken": 42},
            {"subscriptionType": False},
            {"subscriptionType": []},
        )
        for oauth in invalid_oauth:
            with self.subTest(oauth=oauth):
                result = self.provider_with(
                    None, credentials={"claudeAiOauth": oauth})

            self.assertFalse(result["available"])
            self.assertIn("invalid OAuth data", result["error"])

    def test_syntactically_valid_invalid_collector_schema_is_corrupt(self):
        invalid_caches = (
            {"fetched_at": True, "rate_limits": {}},
            {"fetched_at": 9_500, "rate_limits": []},
            {"fetched_at": 9_500, "rate_limits": {"five_hour": []}},
            {"fetched_at": 9_500, "rate_limits": {"five_hour": {
                "used_percentage": "12", "resets_at": 11_000,
            }}},
            {"fetched_at": 9_500, "rate_limits": {"five_hour": {
                "used_percentage": 12, "resets_at": "11000",
            }}},
        )
        for cache in invalid_caches:
            with self.subTest(cache=cache):
                result = self.provider_with(cache)

            self.assertFalse(result["available"])
            self.assertIn("Claude collector cache is corrupt", result["error"])

    def test_syntactically_valid_invalid_oauth_cache_schema_is_corrupt(self):
        invalid_caches = (
            {"fetched_at": True},
            {"last_attempt_at": "9500"},
            {"retry_after_until": False},
            {"fetched_at": 9_500, "last_attempt_at": 9_500, "data": []},
            {"fetched_at": 9_500, "last_attempt_at": 9_500,
             "data": {"five_hour": []}},
            {"fetched_at": 9_500, "last_attempt_at": 9_500,
             "data": {"five_hour": {"utilization": "12"}}},
            {"fetched_at": 9_500, "last_attempt_at": 9_500,
             "data": {"five_hour": {"utilization": 12,
                                      "resets_at": "not-a-date"}}},
        )
        for cache in invalid_caches:
            with self.subTest(cache=cache):
                result = self.provider_with(None, cache)

            self.assertFalse(result["available"])
            self.assertIn("Claude OAuth cache is corrupt", result["error"])

    def test_valid_but_unusable_caches_are_not_reported_as_corrupt(self):
        cases = (
            ({"fetched_at": 9_500, "rate_limits": {}}, None),
            ({"fetched_at": 9_099, "rate_limits": {"five_hour": {
                "used_percentage": 12, "resets_at": 11_000,
            }}}, None),
            (None, {"fetched_at": None, "last_attempt_at": 9_500,
                    "data": None, "retry_after_until": 0}),
        )
        for collector, oauth in cases:
            with self.subTest(collector=collector, oauth=oauth):
                result = self.provider_with(collector, oauth)

            self.assertFalse(result["available"])
            self.assertNotIn("corrupt", result["error"])

    def test_malformed_profile_credentials_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile"
            profile_path.mkdir()
            (profile_path / ".credentials.json").write_text("{bad", encoding="utf-8")
            provider = MODULE["provider_claude"]
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {"CACHE_DIR": str(Path(directory) / "cache")}):
                result = provider(self.cfg(), self.profile(str(profile_path)))

        self.assertFalse(result["available"])
        self.assertIn("credentials", result["error"].lower())
        self.assertIn("malformed", result["error"].lower())

    def test_unreadable_profile_credentials_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile"
            profile_path.mkdir()
            (profile_path / ".credentials.json").mkdir()
            provider = MODULE["provider_claude"]
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {"CACHE_DIR": str(Path(directory) / "cache")}):
                result = provider(self.cfg(), self.profile(str(profile_path)))

        self.assertFalse(result["available"])
        self.assertIn("credentials", result["error"].lower())
        self.assertIn("unreadable", result["error"].lower())

    def test_malformed_profile_caches_are_explicit_without_successful_source(self):
        paths = {
            "collector_cache": "/collector", "oauth_cache": "/oauth",
            "oauth_lock": "/oauth-lock", "credentials": "/credentials",
            "projects": "/projects", "settings": "/settings",
        }

        def json_state(path, _label="JSON file"):
            if path == "/credentials":
                return "missing", None, None
            return "invalid", None, "%s cache is malformed" % path.strip("/")

        provider = MODULE["provider_claude"]
        with mock.patch.object(provider.__globals__["time"], "time", return_value=10_000), \
                mock.patch.dict(provider.__globals__, {
                    "claude_profile_paths": lambda _profile: paths,
                    "read_json_state": json_state,
                    "migrate_legacy_claude_caches": lambda *_args: {},
                }):
            result = provider(self.cfg(), self.profile())

        self.assertFalse(result["available"])
        self.assertIn("collector cache is malformed", result["error"])
        self.assertIn("oauth cache is malformed", result["error"])

    def test_successful_live_source_suppresses_corrupt_cache_fallback(self):
        cfg = {**self.cfg(), "claude_token": "token"}
        paths = {
            "collector_cache": "/collector", "oauth_cache": "/oauth",
            "oauth_lock": "/oauth-lock", "credentials": "/credentials",
            "projects": "/projects", "settings": "/settings",
        }

        def json_state(path, _label="JSON file"):
            if path == "/oauth":
                return "invalid", None, "OAuth cache is malformed"
            return "missing", None, None

        provider = MODULE["provider_claude"]
        with mock.patch.object(provider.__globals__["time"], "time", return_value=10_000), \
                mock.patch.dict(provider.__globals__, {
                    "claude_profile_paths": lambda _profile: paths,
                    "read_json_state": json_state,
                    "migrate_legacy_claude_caches": lambda *_args: {},
                    "claude_claim_oauth_attempt": lambda now, _cache, _lock, deadline=None: (
                        True, {"last_attempt_at": now}, None),
                    "claude_finish_oauth_attempt": lambda *_args, **_kwargs: None,
                    "http_get_json": lambda *_args, **_kwargs: {
                        "five_hour": {"utilization": 12, "resets_at": 11_000},
                    },
                }):
            result = provider(cfg, self.profile())

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertNotIn("malformed", (result["error"] or "").lower())

    def test_successful_live_source_replaces_semantically_corrupt_oauth_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile"
            cache_dir = root / "cache"
            profile_path.mkdir()
            cache_dir.mkdir()
            profile = self.profile(str(profile_path))
            paths_for = MODULE["claude_profile_paths"]
            with mock.patch.dict(paths_for.__globals__, {"CACHE_DIR": str(cache_dir)}):
                paths = paths_for(profile)
            Path(paths["oauth_cache"]).write_text(json.dumps({
                "fetched_at": 9_500,
                "last_attempt_at": 9_500,
                "data": None,
                "retry_after_until": "invalid",
            }), encoding="utf-8")
            provider = MODULE["provider_claude"]
            cfg = {**self.cfg(), "claude_token": "token"}
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {
                        "CACHE_DIR": str(cache_dir),
                        "http_get_json": lambda *_args, **_kwargs: {
                            "five_hour": {"utilization": 12, "resets_at": 11_000},
                        },
                    }):
                result = provider(cfg, profile)

            rewritten = json.loads(Path(paths["oauth_cache"]).read_text(
                encoding="utf-8"))

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertNotIn("corrupt", (result["error"] or "").lower())
        self.assertEqual(rewritten["retry_after_until"], 0)
        self.assertIsInstance(rewritten["data"], dict)

    def test_validated_oauth_cache_reader_returns_only_schema_valid_data(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "oauth.json"
            read_state = MODULE["read_claude_oauth_cache_state"]

            cache_path.write_text("[\"not-an-object\"]", encoding="utf-8")
            state, data, error = read_state(str(cache_path))
            self.assertEqual(state, "invalid")
            self.assertIsNone(data)
            self.assertIn("root must be a JSON object", error)

            valid = {
                "fetched_at": 9_500,
                "last_attempt_at": 9_500,
                "data": {"five_hour": {"utilization": 12, "resets_at": 11_000}},
                "retry_after_until": 0,
            }
            cache_path.write_text(json.dumps(valid), encoding="utf-8")
            self.assertEqual(read_state(str(cache_path)), ("valid", valid, None))

    def test_corrupt_oauth_cache_and_lock_open_failure_are_honestly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "cache"
            profile_path = root / "profile"
            cache_dir.mkdir()
            profile_path.mkdir()
            profile = self.profile(str(profile_path))
            paths_for = MODULE["claude_profile_paths"]
            with mock.patch.dict(paths_for.__globals__, {"CACHE_DIR": str(cache_dir)}):
                paths = paths_for(profile)
            Path(paths["oauth_cache"]).write_text(
                json.dumps(["corrupt"]), encoding="utf-8")
            Path(paths["oauth_lock"]).mkdir()
            provider = MODULE["provider_claude"]
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {
                        "CACHE_DIR": str(cache_dir),
                        "http_get_json": lambda *_args, **_kwargs: self.fail(
                            "network must not run without the poll lock"),
                    }):
                result = provider(
                    {**self.cfg(), "claude_token": "token"}, profile)

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("lock failed", result["error"])
        self.assertIn("OAuth cache is corrupt", result["error"])

    def test_corrupt_oauth_cache_and_lock_timeout_are_honestly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "cache"
            profile_path = root / "profile"
            cache_dir.mkdir()
            profile_path.mkdir()
            profile = self.profile(str(profile_path))
            paths_for = MODULE["claude_profile_paths"]
            with mock.patch.dict(paths_for.__globals__, {"CACHE_DIR": str(cache_dir)}):
                paths = paths_for(profile)
            Path(paths["oauth_cache"]).write_text(
                json.dumps(["corrupt"]), encoding="utf-8")
            holder = os.open(paths["oauth_lock"], os.O_CREAT | os.O_RDWR, 0o600)
            self.addCleanup(os.close, holder)
            fcntl.flock(holder, fcntl.LOCK_EX)
            provider = MODULE["provider_claude"]
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {
                        "CACHE_DIR": str(cache_dir),
                        "CLAUDE_OAUTH_LOCK_TIMEOUT": 0.02,
                        "http_get_json": lambda *_args, **_kwargs: self.fail(
                            "network must not run without the poll lock"),
                    }):
                result = provider(
                    {**self.cfg(), "claude_token": "token"}, profile)

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("lock timed out", result["error"])
        self.assertIn("OAuth cache is corrupt", result["error"])

    def test_valid_oauth_cache_is_served_when_lock_open_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "cache"
            profile_path = root / "profile"
            cache_dir.mkdir()
            profile_path.mkdir()
            profile = self.profile(str(profile_path))
            paths_for = MODULE["claude_profile_paths"]
            with mock.patch.dict(paths_for.__globals__, {"CACHE_DIR": str(cache_dir)}):
                paths = paths_for(profile)
            Path(paths["oauth_cache"]).write_text(json.dumps({
                "fetched_at": 9_000,
                "last_attempt_at": 9_000,
                "data": {"five_hour": {"utilization": 31, "resets_at": 11_000}},
                "retry_after_until": 0,
            }), encoding="utf-8")
            Path(paths["oauth_lock"]).mkdir()
            provider = MODULE["provider_claude"]
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {
                        "CACHE_DIR": str(cache_dir),
                        "http_get_json": lambda *_args, **_kwargs: self.fail(
                            "network must not run without the poll lock"),
                    }):
                result = provider(
                    {**self.cfg(), "claude_token": "token"}, profile)

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api-cached")
        self.assertEqual(result["windows"][0]["used_percent"], 31)
        self.assertIn("lock failed", result["error"])

    def test_live_oauth_publish_recovers_when_cache_becomes_non_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "cache"
            profile_path = root / "profile"
            cache_dir.mkdir()
            profile_path.mkdir()
            profile = self.profile(str(profile_path))
            paths_for = MODULE["claude_profile_paths"]
            with mock.patch.dict(paths_for.__globals__, {"CACHE_DIR": str(cache_dir)}):
                paths = paths_for(profile)
            live = {"five_hour": {"utilization": 12, "resets_at": 11_000}}

            def replace_cache_then_respond(*_args, **_kwargs):
                Path(paths["oauth_cache"]).write_text(
                    json.dumps(["replaced"]), encoding="utf-8")
                return live

            provider = MODULE["provider_claude"]
            with mock.patch.object(provider.__globals__["time"], "time",
                                   return_value=10_000), mock.patch.dict(
                    provider.__globals__, {
                        "CACHE_DIR": str(cache_dir),
                        "http_get_json": replace_cache_then_respond,
                    }):
                result = provider(
                    {**self.cfg(), "claude_token": "token"}, profile)
            published = json.loads(Path(paths["oauth_cache"]).read_text(encoding="utf-8"))

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["windows"][0]["used_percent"], 12)
        self.assertEqual(published["data"], live)
        self.assertEqual(published["retry_after_until"], 0)

    def test_live_oauth_response_reports_explicit_cache_publish_error(self):
        result = self.provider_with(
            None, cfg={**self.cfg(), "claude_token": "token"},
            http_get=lambda *_args, **_kwargs: {
                "five_hour": {"utilization": 12, "resets_at": 11_000},
            },
            finish=lambda *_args, **_kwargs: (
                "usage cache publish failed: disk full"))

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["windows"][0]["used_percent"], 12)
        self.assertEqual(result["error"], "usage cache publish failed: disk full")

    def test_api_failure_serves_initial_valid_cache_when_claim_reports_corruption(self):
        initial_cache = {
            "fetched_at": 9_500,
            "last_attempt_at": 9_000,
            "data": {"five_hour": {"utilization": 31, "resets_at": 11_000}},
            "retry_after_until": 0,
        }
        corruption = "Claude OAuth cache is corrupt: retry_after_until must be a number"
        result = self.provider_with(
            None, oauth=initial_cache, cfg={**self.cfg(), "claude_token": "token"},
            claim=lambda now, *_args, **_kwargs: (
                True, {"fetched_at": None, "last_attempt_at": now,
                       "data": None, "retry_after_until": 0}, corruption),
            http_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("offline")))

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api-cached")
        self.assertEqual(result["windows"][0]["used_percent"], 31)
        self.assertIn("usage API failed: offline", result["error"])
        self.assertIn(corruption, result["error"])

    def test_api_failure_without_initial_cache_reports_claim_corruption(self):
        corruption = "Claude OAuth cache is corrupt: retry_after_until must be a number"
        result = self.provider_with(
            None, cfg={**self.cfg(), "claude_token": "token"},
            claim=lambda now, *_args, **_kwargs: (
                True, {"fetched_at": None, "last_attempt_at": now,
                       "data": None, "retry_after_until": 0}, corruption),
            http_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("offline")))

        self.assertFalse(result["available"])
        self.assertEqual(result["windows"], [])
        self.assertIn("usage API failed: offline", result["error"])
        self.assertIn(corruption, result["error"])

    def test_live_response_suppresses_claim_corruption_warning(self):
        corruption = "Claude OAuth cache is corrupt: retry_after_until must be a number"
        result = self.provider_with(
            None, cfg={**self.cfg(), "claude_token": "token"},
            claim=lambda now, *_args, **_kwargs: (
                True, {"fetched_at": None, "last_attempt_at": now,
                       "data": None, "retry_after_until": 0}, corruption),
            http_get=lambda *_args, **_kwargs: {
                "five_hour": {"utilization": 12, "resets_at": 11_000},
            })

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "api")
        self.assertNotIn("corrupt", (result["error"] or "").lower())

    def test_concurrent_processes_make_one_oauth_network_attempt(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires Linux fork semantics")
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            provider = MODULE["provider_claude"]
            calls = context.Value("i", 0)
            start = context.Event()
            results = context.Queue()
            profile = self.profile(str(Path(directory) / "profile"))

            def network(*_args):
                with calls.get_lock():
                    calls.value += 1
                return {"five_hour": {"utilization": 12,
                        "resets_at": "2100-01-01T00:00:00Z"}}

            def worker():
                start.wait()
                results.put(provider({**self.cfg(), "claude_token": "token"}, profile))

            replacements = {
                "CACHE_DIR": directory,
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
                    "claude_profile_paths": lambda _profile: {
                        "collector_cache": "/collector", "oauth_cache": "/oauth",
                        "oauth_lock": "/oauth-lock", "credentials": "/credentials",
                        "projects": "/projects", "settings": "/settings",
                    },
                    "claude_claim_oauth_attempt": lambda _now, _cache, _lock, deadline=None: (
                        False, None, "lock timed out"),
                    "http_get_json": lambda *_a: (_ for _ in ()).throw(AssertionError("network called")),
                }):
            result = provider(cfg, self.profile())
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
            with mock.patch.dict(claim.__globals__, {"CLAUDE_OAUTH_LOCK_TIMEOUT": 0.02}):
                claimed, _cache, error = claim(
                    MODULE["time"].time(), cache_path, lock_path)
            self.assertFalse(claimed)
            self.assertIn("timed out", error)
            self.assertLess(MODULE["time"].monotonic() - started, 0.5)

    def test_oauth_claim_propagates_semantic_cache_error_when_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = str(Path(directory) / "oauth.json")
            lock_path = str(Path(directory) / "oauth.lock")
            claim = MODULE["claude_claim_oauth_attempt"]
            corruption = "Claude OAuth cache is corrupt: retry_after_until must be a number"
            with mock.patch.object(
                    claim.__globals__["time"], "time", return_value=10_000), \
                    mock.patch.dict(claim.__globals__, {
                        "read_claude_oauth_cache_state": lambda _path: (
                            "invalid", None, corruption),
                    }):
                claimed, cache, error = claim(10_000, cache_path, lock_path)

        self.assertTrue(claimed)
        self.assertEqual(cache["last_attempt_at"], 10_000)
        self.assertEqual(error, corruption)

    def test_oauth_claim_lock_wait_obeys_absolute_provider_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / "oauth.lock")
            cache_path = str(Path(directory) / "oauth.json")
            holder = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            self.addCleanup(os.close, holder)
            fcntl.flock(holder, fcntl.LOCK_EX)
            claim = MODULE["claude_claim_oauth_attempt"]
            started = time.monotonic()

            claimed, _cache, error = claim(
                time.time(), cache_path, lock_path, deadline=started + 0.02)

            self.assertFalse(claimed)
            self.assertIn("helper deadline exceeded", error)
            self.assertLess(time.monotonic() - started, 0.1)

    def test_oauth_result_does_not_overwrite_newer_good_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / "oauth.lock")
            cache_path = Path(directory) / "oauth.json"
            newer = {"fetched_at": 20_000, "last_attempt_at": 20_000,
                     "data": {"five_hour": {"utilization": 5}},
                     "retry_after_until": 0}
            cache_path.write_text(json.dumps(newer), encoding="utf-8")
            finish = MODULE["claude_finish_oauth_attempt"]
            finish(10_000, str(cache_path), lock_path,
                   data={"five_hour": {"utilization": 99}})
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), newer)

    def test_oauth_claim_closes_fd_when_fchmod_fails(self):
        claim = MODULE["claude_claim_oauth_attempt"]
        with mock.patch.object(MODULE["os"], "makedirs"), \
                mock.patch.object(MODULE["os"], "chmod"), \
                mock.patch.object(MODULE["os"], "open", return_value=123), \
                mock.patch.object(MODULE["os"], "fchmod", side_effect=OSError("boom")), \
                mock.patch.object(MODULE["os"], "close") as close, \
                mock.patch.dict(claim.__globals__, {"read_cache": lambda _p: None}):
            claimed, _cache, error = claim(10_000, "/cache", "/lock")
        self.assertFalse(claimed)
        self.assertIn("lock failed", error)
        close.assert_called_once_with(123)

    def test_oauth_finish_closes_fd_when_fchmod_fails(self):
        finish = MODULE["claude_finish_oauth_attempt"]
        with mock.patch.object(MODULE["os"], "makedirs"), \
                mock.patch.object(MODULE["os"], "open", return_value=456), \
                mock.patch.object(MODULE["os"], "fchmod", side_effect=OSError("boom")), \
                mock.patch.object(MODULE["os"], "close") as close:
            finish(10_000, "/cache", "/lock", data={"five_hour": {"utilization": 5}})
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
