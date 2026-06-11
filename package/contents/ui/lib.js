.pragma library

// Brand-ish base colors used for the provider glyph in the panel.
function providerColor(id) {
    if (id === "claude") return "#d97757";
    if (id === "codex")  return "#10a37f";
    if (id === "antigravity") return "#4285f4";
    return "#888888";
}

function providerInitial(id) {
    if (!id) return "?";
    return id.charAt(0).toUpperCase();
}

// Find a usage window by key ("5h", "7d", ...) inside a provider object.
function findWindow(provider, key) {
    if (!provider || !provider.windows) return null;
    for (var i = 0; i < provider.windows.length; ++i) {
        if (provider.windows[i].key === key) return provider.windows[i];
    }
    return null;
}

// The provider's headline window for the compact chip: its first window. This
// is "5h" for Claude/Codex and the rolling "Models" window for Antigravity --
// each provider exposes whatever window best represents its primary limit first.
function primaryWindow(provider) {
    if (!provider || !provider.windows || provider.windows.length === 0) return null;
    return provider.windows[0];
}

// Threshold level for a utilization percentage. Returns one of
// "ok" | "warn" | "crit" | "muted" (muted = no real percentage available).
function levelFor(pct, warn, crit) {
    if (pct === null || pct === undefined) return "muted";
    if (pct >= crit) return "crit";
    if (pct >= warn) return "warn";
    return "ok";
}

// Format a utilization percentage. Null -> em dash (never 0 %).
function fmtPct(pct) {
    if (pct === null || pct === undefined) return "—";
    return Math.round(pct) + "%";
}

// Compact reset countdown from a unix-seconds reset timestamp, e.g. "4h44m"
// or "6d17h". nowMs is Date.now() passed in so callers control the tick.
// nowLabel is the already-translated "just reset" word, supplied by the caller:
// this file is a `.pragma library` and has no access to the i18n() context, so
// the only honest way to keep the string translatable is to inject it from the
// QML component. Defaults to "now" so the library stays usable standalone.
function fmtCountdown(epoch, nowMs, nowLabel) {
    if (!epoch) return "—";
    var rem = epoch - Math.floor(nowMs / 1000);
    if (rem <= 0) return nowLabel || "now";
    var d = Math.floor(rem / 86400); rem -= d * 86400;
    var h = Math.floor(rem / 3600);  rem -= h * 3600;
    var m = Math.floor(rem / 60);
    if (d > 0) return d + "d" + h + "h";
    if (h > 0) return h + "h" + m + "m";
    return m + "m";
}
