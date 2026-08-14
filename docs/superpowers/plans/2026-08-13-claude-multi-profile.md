# Claude Multi-Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover any number of Claude Code profiles, let users add, rename, and hide them, and report each profile's real usage independently in the Plasma widget.

**Architecture:** The Python helper owns profile discovery, normalization, stable identity, data collection, and cache isolation. KConfig stores one JSON document containing manual paths and per-path UI overrides; QML edits that document and passes it to the helper, while each normalized provider result carries `base_provider: "claude"` for shared presentation. The statusline collector and its management commands use the same canonical-path hash as the helper so profile data cannot cross cache boundaries.

**Tech Stack:** Python 3 standard library, `unittest`, QML/Qt Quick, KDE Kirigami/KConfig, Plasma executable data source, XML KConfig schema.

## Global Constraints

- Keep provider-specific disk reads, API calls, parsing, cache handling, and JSON normalization in `package/contents/code/ai-usage-json`.
- Keep both Python helpers Python 3 stdlib-only; add no pip dependency.
- Never fabricate usage: unavailable profiles use `available: false`; missing percentages use `used_percent: null`.
- Never store credentials in KConfig or expose raw tokens on the helper command line.
- Canonicalize and deduplicate paths before assigning profile identity or reading data.
- Isolate OAuth caches, locks, retry state, collector caches, and integration state by canonical profile path.
- Preserve Codex and Antigravity behavior.
- Do not commit, tag, push, or open a pull request unless the user explicitly requests it. The checkpoints below stop after verification and `git diff` review.

## File map

- Modify `package/contents/code/ai-usage-json`: profile schema, discovery, overrides, cache paths, profile-aware Claude provider, management CLI, normalized output.
- Modify `package/contents/code/claude-usage-collector`: derive active profile and write its isolated cache.
- Create `package/contents/ui/config/ClaudeProfilesEditor.qml`: focused dynamic profile-list editor.
- Modify `package/contents/ui/config/ConfigGeneral.qml`: discover profiles, host the editor, and invoke profile-aware collector management.
- Modify `package/contents/ui/main.qml`: pass serialized profile configuration to the helper.
- Modify `package/contents/ui/ProviderIcon.qml`: select assets through `baseProvider`.
- Modify `package/contents/ui/CompactRepresentation.qml` and `package/contents/ui/FullRepresentation.qml`: pass the normalized base provider to the icon.
- Modify `package/contents/config/main.xml`: persist the profile JSON document.
- Modify `tests/test_ai_usage_json.py`: discovery, configuration, provider isolation, management, and main-output regression tests.
- Modify `tests/test_claude_usage_collector.py`: collector cache routing tests.
- Modify `README.md`: explain discovery, overrides, and profile-specific collection.

---

### Task 1: Profile discovery and normalized configuration

**Files:**
- Modify: `package/contents/code/ai-usage-json:64-90,1246-1303`
- Test: `tests/test_ai_usage_json.py`

**Interfaces:**
- Produces: `claude_profile_id(path: str) -> str`.
- Produces: `discover_claude_profiles(raw_config: object, home: str | None = None) -> list[dict]`.
- Produces profile dictionaries with exact keys `id`, `base_provider`, `label`, `path`, `canonical_path`, `automatic`, and `enabled`.
- Produces CLI `ai-usage-json --claude-profiles`, returning `{"profiles": [...]}` without querying usage APIs.
- Produces config key `claude_profiles` from `AI_USAGE_CLAUDE_PROFILES_JSON`.

- [ ] **Step 1: Add failing discovery and configuration tests**

Add a `ClaudeProfileDiscoveryTests` class using `tempfile.TemporaryDirectory()`. Create marker files/directories explicitly and assert exact behavior:

```python
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

    def test_rejects_similarly_named_directory_without_profile_marker(self):
        (self.home / ".claude-not-a-profile").mkdir()
        profiles = MODULE["discover_claude_profiles"]({}, str(self.home))
        self.assertNotIn(str(self.home / ".claude-not-a-profile"),
                         [p["canonical_path"] for p in profiles])

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

    def test_stable_id_does_not_contain_path(self):
        profile_id = MODULE["claude_profile_id"](str(self.home / ".claude"))
        self.assertRegex(profile_id, r"^claude-[0-9a-f]{12}$")
        self.assertNotIn(self.home.name, profile_id)
```

Also test `load_config()` with valid JSON, malformed JSON, and a non-object JSON value. Malformed/non-object configuration must produce a top-level explicit configuration error in `main()` rather than silently reverting to defaults.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_ai_usage_json.ClaudeProfileDiscoveryTests -v
```

Expected: failures because `discover_claude_profiles` and `claude_profile_id` do not exist.

- [ ] **Step 3: Implement deterministic profile normalization**

Add `hashlib` and implement these exact rules:

```python
CLAUDE_PROFILE_MARKERS = (".credentials.json", "settings.json", "projects")

def claude_profile_id(path):
    digest = hashlib.sha256(os.path.realpath(path).encode("utf-8")).hexdigest()[:12]
    return "claude-" + digest

def default_claude_profile_label(path, home):
    name = os.path.basename(path)
    if os.path.realpath(path) == os.path.realpath(os.path.join(home, ".claude")):
        return "Claude"
    suffix = name[len(".claude-"):] if name.startswith(".claude-") else name
    words = re.split(r"[-_]+", suffix)
    return "Claude " + " ".join(word.capitalize() for word in words if word)
```

`discover_claude_profiles` must:

1. Validate that configuration is a dictionary containing only list-like `manual` and dictionary-like `overrides` values; raise `ValueError` for invalid types.
2. Consider the default directory plus sorted `glob.glob(os.path.join(home, ".claude-*"))` automatic candidates.
3. Accept automatic candidates only when they are directories and contain at least one marker.
4. Add every manual path, including invalid paths; expand `~`, make relative paths absolute, then compare `os.path.realpath` values.
5. Let an automatic entry win the `automatic` flag when a manual entry resolves to it.
6. Validate overrides: `label` must be a non-empty string after trimming and `enabled` must be a boolean; invalid values raise `ValueError`.
7. Attach `profile_error` only to invalid manual entries.
8. Return automatic profiles first in sorted canonical-path order, then manual-only profiles in their configured order.

Parse `AI_USAGE_CLAUDE_PROFILES_JSON` in `load_config`; preserve the parse/validation exception as `claude_profiles_error`. Implement `--claude-profiles` so the settings UI can request discovery without network calls. Its invalid-config response must be `{"ok": false, "error": "invalid Claude profile configuration: ...", "profiles": []}`.

- [ ] **Step 4: Run focused and existing tests**

Run:

```bash
python3 -m unittest tests.test_ai_usage_json.ClaudeProfileDiscoveryTests -v
python3 -m unittest tests.test_ai_usage_json -v
```

Expected: all tests pass.

- [ ] **Step 5: Review the task diff without committing**

Run `git diff --check` and `git diff -- package/contents/code/ai-usage-json tests/test_ai_usage_json.py`. Expected: no whitespace errors; changes are limited to discovery/configuration behavior.

---

### Task 2: Profile-aware Claude provider and isolated OAuth state

**Files:**
- Modify: `package/contents/code/ai-usage-json:489-789,1246-1303`
- Test: `tests/test_ai_usage_json.py`

**Interfaces:**
- Consumes: normalized profile dictionaries from Task 1.
- Produces: `claude_profile_paths(profile: dict) -> dict[str, str]` with `credentials`, `projects`, `settings`, `oauth_cache`, `oauth_lock`, and `collector_cache`.
- Changes: `claude_local_estimate(projects_path, cap_5h, cap_7d)`.
- Changes: `claude_claim_oauth_attempt(now, cache_path, lock_path)` and `claude_finish_oauth_attempt(attempt_at, cache_path, lock_path, data=None, retry_after_until=None)`.
- Changes: `provider_claude(cfg: dict, profile: dict) -> dict`.
- Provider results include `base_provider: "claude"` and `profile_path` but never credentials or tokens.

- [ ] **Step 1: Add failing provider-isolation tests**

Create two temporary profile directories with different credential plans and patch `http_get_json` to return different utilization based on token:

```python
def test_profiles_use_separate_credentials_labels_and_ids(self):
    private = self.make_profile(".claude", "private-token", "max")
    work = self.make_profile(".claude-work", "work-token", "team")
    calls = []

    def fake_get(url, token):
        calls.append(token)
        utilization = 11 if token == "private-token" else 22
        return {"five_hour": {"utilization": utilization, "resets_at": 200}}

    with mock.patch.dict(MODULE["provider_claude"].__globals__,
                         {"http_get_json": fake_get}), \
         mock.patch.object(MODULE["provider_claude"].__globals__["time"],
                           "time", return_value=100):
        private_result = MODULE["provider_claude"](self.cfg, private)
        work_result = MODULE["provider_claude"](self.cfg, work)

    self.assertEqual(calls, ["private-token", "work-token"])
    self.assertEqual(private_result["windows"][0]["used_percent"], 11)
    self.assertEqual(work_result["windows"][0]["used_percent"], 22)
    self.assertEqual(private_result["base_provider"], "claude")
    self.assertNotEqual(private_result["id"], work_result["id"])
```

Add tests that assert:

- `claude_profile_paths(private)["oauth_cache"] != claude_profile_paths(work)["oauth_cache"]` for every cache/lock/state key;
- a cache written for private is never returned for work;
- `claude_local_estimate` reads only the supplied profile's `projects/` directory;
- an invalid manual profile returns `available: false`, empty `windows`, its configured label, and its `profile_error`;
- existing legacy `claude-usage.json` and `claude-statusline.json` are migrated only when the default profile's hashed cache is absent;
- `main()` expands one enabled provider name `claude` into every enabled normalized Claude profile, while disabled profiles are omitted and Codex/Antigravity retain one entry each.

Add the `ClaudeMultiProfileMainTests` end-to-end case from Task 5 at this point, before changing `main()`, so provider expansion and `base_provider` metadata are observed failing before implementation.

- [ ] **Step 2: Run the new provider tests and verify RED**

Run `python3 -m unittest tests.test_ai_usage_json.ClaudeMultiProfileProviderTests -v`.

Expected: signature errors from `provider_claude(cfg, profile)` and missing `claude_profile_paths`.

- [ ] **Step 3: Parameterize all Claude paths and results**

Implement a single path constructor and pass its products explicitly:

```python
def claude_profile_paths(profile):
    suffix = profile["id"].removeprefix("claude-")
    return {
        "credentials": os.path.join(profile["canonical_path"], ".credentials.json"),
        "projects": os.path.join(profile["canonical_path"], "projects"),
        "settings": os.path.join(profile["canonical_path"], "settings.json"),
        "oauth_cache": os.path.join(CACHE_DIR, "claude-usage-%s.json" % suffix),
        "oauth_lock": os.path.join(CACHE_DIR, "claude-oauth-%s.lock" % suffix),
        "collector_cache": os.path.join(CACHE_DIR, "claude-statusline-%s.json" % suffix),
    }
```

Do not retain mutable per-profile paths in module globals. Extend `provider()` with an optional `base_provider=None` argument and emit it only when supplied. In `provider_claude`, build the result from `profile["id"]`, `profile["label"]`, `profile["canonical_path"]`, and `base_provider="claude"`.

Change token precedence to: explicit `AI_USAGE_CLAUDE_TOKEN`, existing global token-file override for backward compatibility, then this profile's `.credentials.json`. Keep configured-but-unreadable token files as a hard error. Make every cache read, cache write, lock claim, retry update, and local fallback call use values from `claude_profile_paths(profile)`.

For one-time migration, only the profile whose canonical path equals `~/.claude` may copy a valid legacy cache to its missing hashed destination. Use the existing atomic JSON writer and preserve permissions; do not delete the legacy file.

Update `main()` so `claude` is expanded via `discover_claude_profiles`; append one explicit unavailable Claude provider if profile configuration is invalid or no valid/explicit profiles exist. Call the unchanged functions for `codex` and `antigravity` exactly once.

- [ ] **Step 4: Run provider and full helper tests**

Run:

```bash
python3 -m unittest tests.test_ai_usage_json.ClaudeMultiProfileProviderTests -v
python3 -m unittest tests.test_ai_usage_json -v
```

Expected: all tests pass; existing Codex and Antigravity tests remain green.

- [ ] **Step 5: Review the task diff without committing**

Run `git diff --check` and inspect the helper/test diff. Confirm by search that `provider_claude` no longer reads `CLAUDE_CREDENTIALS`, `CLAUDE_PROJECTS`, `CLAUDE_CACHE`, `CLAUDE_OAUTH_LOCK`, or `CLAUDE_STATUSLINE_CACHE` directly.

---

### Task 3: Profile-aware statusline collector and integration management

**Files:**
- Modify: `package/contents/code/claude-usage-collector:1-82`
- Modify: `package/contents/code/ai-usage-json:273-384,1275-1290`
- Test: `tests/test_claude_usage_collector.py`
- Test: `tests/test_ai_usage_json.py`

**Interfaces:**
- Consumes: `claude_profile_id` and `claude_profile_paths` semantics from Tasks 1–2.
- Produces in collector: `active_profile_dir(environ=None, home=None) -> str` and `profile_cache_path(profile_dir: str) -> str`.
- Produces in helper: `claude_integration_state_path(settings_path: str) -> str`, hashed from the real settings-file path so symlink-sharing profiles share one management record.
- Changes management functions to `claude_integration_status(profile_dir)`, `claude_integration_setup(profile_dir)`, and `claude_integration_remove(profile_dir)`.
- Produces CLI: `ai-usage-json --claude-integration <status|setup|remove> --profile <directory>`.

- [ ] **Step 1: Add failing collector routing tests**

Add exact cache-routing assertions:

```python
def test_default_profile_cache_when_config_dir_is_unset(self):
    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch.object(MODULE, "HOME", str(self.home)):
        profile = MODULE.active_profile_dir(os.environ, str(self.home))
    self.assertEqual(profile, str((self.home / ".claude").resolve()))

def test_work_profile_cache_when_config_dir_is_set(self):
    work = self.home / ".claude-work"
    work.mkdir()
    profile = MODULE.active_profile_dir(
        {"CLAUDE_CONFIG_DIR": str(work)}, str(self.home))
    self.assertEqual(profile, str(work.resolve()))
    self.assertNotEqual(MODULE.profile_cache_path(profile),
                        MODULE.profile_cache_path(str((self.home / ".claude").resolve())))
```

Adapt the collector test loader if necessary so module functions are imported without running `main()`. Add a test that calls `cache_payload` twice with different patched profile cache paths and confirms each file retains its own percentage.

In `ClaudeIntegrationTests`, change calls to pass a profile directory and add tests for separate state paths, an invalid profile path, and two profiles whose `settings.json` paths resolve to the same symlink target.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_claude_usage_collector -v
python3 -m unittest tests.test_ai_usage_json.ClaudeIntegrationTests -v
```

Expected: failures for missing routing functions and old management signatures.

- [ ] **Step 3: Route collector output by canonical active profile**

Implement the same SHA-256/12-hex suffix algorithm in the standalone collector; duplication is intentional because the executable must remain independent:

```python
def active_profile_dir(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = os.path.expanduser("~") if home is None else home
    configured = environ.get("CLAUDE_CONFIG_DIR")
    path = configured if configured else os.path.join(home, ".claude")
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))

def profile_cache_path(profile_dir):
    digest = hashlib.sha256(os.path.realpath(profile_dir).encode("utf-8")).hexdigest()[:12]
    return os.path.join(CACHE_DIR, "claude-statusline-%s.json" % digest)
```

Make `write_cache(rate_limits, cache_path)` explicit and have `main()` compute the path once from the current environment. Preserve directory mode `0700`, file mode `0600`, validation, atomic replacement, and previous-statusline chaining.

- [ ] **Step 4: Parameterize integration management safely**

Resolve the requested directory with the same normalization rules and reject missing/non-directory paths before reading settings. Use `claude_profile_paths` for the settings location and `claude_integration_state_path(settings_path)` for state. The managed command must not set `CLAUDE_CONFIG_DIR`: the collector inherits the active value from each Claude process, allowing profiles that share one symlinked settings file to route their payloads to different usage caches:

```python
def managed_claude_command(previous_statusline):
    previous_command = previous_statusline.get("command") \
        if isinstance(previous_statusline, dict) else None
    parts = []
    if previous_command:
        parts.append("AI_USAGE_PREVIOUS_STATUSLINE=" + shlex.quote(previous_command))
    parts.append(shlex.quote(os.path.abspath(CLAUDE_COLLECTOR)))
    return " ".join(parts)
```

Before setup/remove, compare `os.path.realpath(settings_path)` with the settings target recorded in integration state. Store `settings_realpath`, previous value, and managed command. Two profiles resolving to the same settings target therefore see the same configured state and never overwrite each other. Refuse to overwrite user-modified statusline data exactly as today. Parse CLI arguments with `argparse` or a small exact parser; missing `--profile`, unknown action, and extra arguments return `{ok: false, state: "error", ...}` and a non-success semantic result without a traceback.

- [ ] **Step 5: Run collector, integration, and full tests**

Run:

```bash
python3 -m unittest tests.test_claude_usage_collector -v
python3 -m unittest tests.test_ai_usage_json.ClaudeIntegrationTests -v
python3 -m unittest discover -s tests -v
python3 -m py_compile package/contents/code/ai-usage-json package/contents/code/claude-usage-collector
```

Expected: all tests pass and both helpers compile silently.

- [ ] **Step 6: Review the task diff without committing**

Run `git diff --check`. Confirm both executables use identical canonical-path hashing and that no command string contains credentials.

---

### Task 4: Persist and edit dynamic profiles in Plasma settings

**Files:**
- Modify: `package/contents/config/main.xml:39-59`
- Create: `package/contents/ui/config/ClaudeProfilesEditor.qml`
- Modify: `package/contents/ui/config/ConfigGeneral.qml:8-290`
- Modify: `package/contents/ui/main.qml:54-84`

**Interfaces:**
- Consumes helper CLI `--claude-profiles` and management CLI from Tasks 1 and 3.
- Produces KConfig string `claudeProfilesJson` with schema `{"manual":[{"path":"..."}],"overrides":{"<canonical path>":{"label":"...","enabled":true}}}`.
- `ClaudeProfilesEditor` properties: `profiles: var`, `configurationJson: string`; signals: `configurationEdited(string)` and `managementRequested(string action, string profilePath)`.
- `main.qml` exports the JSON as shell-quoted `AI_USAGE_CLAUDE_PROFILES_JSON`.

- [ ] **Step 1: Add the KConfig entry and QML property contract**

Add to `main.xml`:

```xml
<entry name="claudeProfilesJson" type="String">
  <default>{"manual":[],"overrides":{}}</default>
</entry>
```

In `ConfigGeneral.qml`, declare `property string cfg_claudeProfilesJson: "{\"manual\":[],\"overrides\":{}}"`. Use this property directly rather than a hidden text field. Add state for the discovered profile array, discovery error, selected management profile, and the existing request lifecycle.

- [ ] **Step 2: Create the focused profile editor component**

Implement `ClaudeProfilesEditor.qml` as a `ColumnLayout` containing a `Repeater` over `profiles`. Each delegate must contain:

- a `Controls.CheckBox` bound to `modelData.enabled`;
- a `Controls.TextField` initialized from `modelData.label`;
- a read-only/elided path label and automatic/manual origin label;
- setup/remove collector actions emitting `managementRequested(action, canonical_path)`;
- a remove button visible only when `!modelData.automatic`;
- a path input and Add button below the list.

Every edit must parse `configurationJson`, modify only `manual` or the override keyed by `canonical_path`, serialize with `JSON.stringify`, set `configurationJson`, and emit `configurationEdited(configurationJson)`. Reject empty labels and empty manual paths in the UI; keep helper-side validation authoritative. Removing a manual entry must remove only its `manual` element, retaining an override if the same path is also automatically discovered.

- [ ] **Step 3: Wire discovery and profile-specific management**

Add a second executable request path or generalize the existing data source. The discovery command is:

```qml
function discoveryCommand() {
    return "AI_USAGE_CLAUDE_PROFILES_JSON=" + shellQuote(cfg_claudeProfilesJson)
        + " python3 " + shellQuote(helperPath) + " --claude-profiles";
}
```

Validate that discovery JSON contains an array `profiles`; otherwise show an explicit settings error and an empty list. Rerun discovery after `configurationEdited` so canonicalization and deduplication remain owned by Python.

Change management construction to:

```qml
function managementCommand(action, profilePath, requestId) {
    return "AI_USAGE_MANAGEMENT_REQUEST_ID=" + requestId
        + " python3 " + shellQuote(helperPath)
        + " --claude-integration " + shellQuote(action)
        + " --profile " + shellQuote(profilePath);
}
```

Track status per canonical profile path rather than in one page-global collector state. Keep the 30-second watchdog and stale-response rejection. Do not display successful status from one profile on another row.

- [ ] **Step 4: Pass configuration to normal helper refreshes**

In `main.qml::buildCommand()`, always append:

```qml
env.push("AI_USAGE_CLAUDE_PROFILES_JSON="
         + shellQuote(Plasmoid.configuration.claudeProfilesJson));
```

Retain `showClaude` as the global master switch. Existing fallback, extra-usage, cap, and token-file settings continue to apply to all enabled Claude profiles.

- [ ] **Step 5: Run QML and schema checks**

Run:

```bash
python3 -c 'import xml.etree.ElementTree as E; E.parse("package/contents/config/main.xml")'
/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml
```

Expected: XML parses successfully. `qmllint` exits zero or reports only the repository-documented unresolved Plasma/Kirigami/i18n warnings; any `error:` line or non-zero exit caused by the edited QML must be fixed.

- [ ] **Step 6: Exercise helper commands produced by QML**

Run a shell-equivalent smoke test with no secrets:

```bash
AI_USAGE_CLAUDE_PROFILES_JSON='{"manual":[],"overrides":{}}' \
python3 package/contents/code/ai-usage-json --claude-profiles | python3 -m json.tool
```

Expected: valid JSON listing at least the locally present `~/.claude` and `~/.claude-work` profiles, with no credential fields.

- [ ] **Step 7: Review the task diff without committing**

Run `git diff --check` and inspect all QML/XML changes. Confirm shell quoting is applied to the JSON string and every profile path.

---

### Task 5: Multi-profile presentation, documentation, and end-to-end verification

**Files:**
- Modify: `package/contents/ui/ProviderIcon.qml:9-43`
- Modify: `package/contents/ui/CompactRepresentation.qml:45-79`
- Modify: `package/contents/ui/FullRepresentation.qml:48-75`
- Modify: `README.md:24-30,60-68,100-140`
- Test: `tests/test_ai_usage_json.py`

**Interfaces:**
- Consumes provider field `base_provider` from Task 2.
- Adds `ProviderIcon.baseProvider: string`; asset/color/initial lookup uses `baseProvider` when non-empty, otherwise `providerId` for backward compatibility.

- [ ] **Step 1: Confirm the end-to-end helper output regression test from Task 2**

The test added before Task 2 implementation invokes the helper through `main()` with a patched home containing private/work profiles and patched providers. Confirm it still asserts:

```python
self.assertEqual(
    [(p["base_provider"], p["label"]) for p in report["providers"][:2]],
    [("claude", "Privat"), ("claude", "Arbeit")],
)
self.assertEqual(len({p["id"] for p in report["providers"]}),
                 len(report["providers"]))
self.assertNotIn("accessToken", json.dumps(report))
```

It must also assert a failing work profile leaves a successful private profile available and does not alter the following Codex/Antigravity entries. If presentation work needs additional metadata, extend the test before changing QML.

- [ ] **Step 2: Run the regression test before presentation changes**

Run `python3 -m unittest tests.test_ai_usage_json.ClaudeMultiProfileMainTests -v`.

Expected: PASS from Task 2. Any newly added assertion must first be observed failing for the intended missing behavior before implementation.

- [ ] **Step 3: Make icon selection use the base provider**

Add to `ProviderIcon.qml`:

```qml
property string baseProvider: ""
readonly property string assetProvider: baseProvider.length > 0
    ? baseProvider : providerId
```

Use `assetProvider` for the SVG filename, `Lib.providerColor`, and `Lib.providerInitial`. In compact and full delegates pass:

```qml
baseProvider: section.modelData.base_provider
    ? section.modelData.base_provider : section.modelData.id
```

Use the corresponding delegate ID (`chip` in compact representation). Do not change threshold, percentage, reset, stale-cache, or tooltip behavior; provider labels already distinguish profiles in the popup and tooltip.

- [ ] **Step 4: Document user-visible behavior**

Update README provider/source and settings sections to state:

- automatic discovery covers `~/.claude` and valid `~/.claude-*` directories;
- arbitrary directories can be added manually;
- profiles can be renamed and hidden;
- credentials, local history, OAuth caches, and collector caches are isolated per profile;
- `CLAUDE_CONFIG_DIR` routes statusline data;
- the JSON environment variable is an internal widget/helper interface and contains paths/preferences only, never tokens.

Replace singular wording that claims the helper always reads only `~/.claude/.credentials.json`.

- [ ] **Step 5: Run complete verification from a clean command invocation**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile package/contents/code/ai-usage-json package/contents/code/claude-usage-collector
AI_USAGE_PROVIDERS=claude,codex,antigravity \
AI_USAGE_CLAUDE_PROFILES_JSON='{"manual":[],"overrides":{}}' \
python3 package/contents/code/ai-usage-json | python3 -m json.tool
python3 -c 'import xml.etree.ElementTree as E; E.parse("package/contents/config/main.xml")'
/usr/lib/qt6/bin/qmllint package/contents/ui/*.qml package/contents/ui/config/*.qml
git diff --check
git status --short
```

Expected: all Python tests pass; both helpers compile; helper output is valid JSON with separate local Claude profiles and no fabricated values; XML parses; QML has no real errors; diff check is clean. If network-backed usage is unavailable, each affected provider must report `available: false` or a clearly marked real cache/local estimate as specified—never zero-filled usage.

- [ ] **Step 6: Perform the pre-completion audit**

Inspect the final diff and confirm:

- every profile-specific read/write uses the canonical profile path or its hash;
- no raw credential/token enters output, KConfig, logs, or command lines;
- invalid configuration fails explicitly;
- `used_percent: null` semantics remain intact;
- unrelated user changes are untouched;
- no commit, tag, push, or pull request was created.
