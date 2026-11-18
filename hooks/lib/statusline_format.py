"""
How ctx-guard-statusline shapes its segment: the config file, the bundled
presets and a small template renderer. Used by ctx-guard-statusline and by
`install.sh --statusline-preset / --statusline-format`.

The config is data, never code: ${XDG_CONFIG_HOME:-~/.config}/ctx-guard/
statusline.json holds a format string with named fields such as
"⬇ {today_all} · 🛡 {pkg_flagged}". The renderer only substitutes `{name}` for
the names in FIELDS (`{{` and `}}` are literal braces); it never uses
str.format, so a template can't reach attributes or indexes.

Anything wrong with the config (unreadable, not JSON, an unknown option,
preset or field) makes load_options() raise ValueError, and the caller falls
back to the default segment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

DEFAULT_TEMPLATE = "ctx-guard ⬇ today {today_pair} · total {total_pair}"
DEFAULT_SEPARATOR = " │ "

# A preset is a template plus the options it defaults; the user's own options
# in the config file still win.
PRESETS: dict[str, dict] = {
    "default": {"template": DEFAULT_TEMPLATE},
    "minimal": {"template": "⬇ {today_all}"},
    "security": {"template": "⬇ {today_all} · 🛡 {pkg_flagged}"},
    # A complete line for users without one, so it shows before any savings.
    "full": {"template": "{model} {context_bar} {context_pct} · ⬇ today {today_pair}"
                         " · total {total_pair}",
             "hide_when_zero": False},
    # Nerd Font / Powerline glyphs: U+E0B0 solid and U+E0B1 thin arrows.
    "powerline": {"template": "ctx-guard  ⬇ today {today_pair}  total {total_pair}",
                  "separator": "  "},
}

SAVINGS_FIELDS = ("today", "total", "today_est", "total_est", "today_all", "total_all",
                  "today_pair", "total_pair")
SECURITY_FIELDS = ("pkg_flagged", "redacted")
ACTIVITY_FIELDS = ("checked",)
SESSION_FIELDS = ("model", "context_pct", "context_bar", "cwd")
FIELDS = frozenset(SAVINGS_FIELDS + SECURITY_FIELDS + ACTIVITY_FIELDS + SESSION_FIELDS
                   + ("agent",))

# Same thresholds as hooks/context_monitor.py's warnings (THRESHOLDS).
COLOR_THRESHOLDS = ((85, "red"), (70, "yellow"), (50, "green"))
COLORS = {"black": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34,
          "magenta": 35, "cyan": 36, "white": 37, "gray": 90, "bright_red": 91,
          "bright_green": 92, "bright_yellow": 93, "bright_blue": 94,
          "bright_magenta": 95, "bright_cyan": 96, "bright_white": 97}

DEFAULTS = {"estimated": "show", "units": "tokens", "separator": DEFAULT_SEPARATOR,
            "hide_when_zero": True, "color": "none"}
CHOICES = {"estimated": ("show", "hide"), "units": ("tokens", "bytes")}
OPTION_KEYS = frozenset(DEFAULTS) | {"template", "preset"}
BAR_WIDTH = 10

TOKEN_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z0-9_]*)\}|[{}]")


def config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ctx-guard", "statusline.json")


# --- template ------------------------------------------------------------------

def parse(template: str) -> list[tuple[bool, str]]:
    """(is_field, text) parts. Raises ValueError for an unknown field or a
    stray brace."""
    if not isinstance(template, str):
        raise ValueError("template must be a string")
    parts: list[tuple[bool, str]] = []
    pos = 0
    for m in TOKEN_RE.finditer(template):
        if m.start() > pos:
            parts.append((False, template[pos:m.start()]))
        token = m.group(0)
        if token in ("{{", "}}"):
            parts.append((False, token[0]))
        elif m.group(1) in FIELDS:
            parts.append((True, m.group(1)))
        elif m.group(1) is not None:
            raise ValueError(f"unknown field {token}")
        else:
            raise ValueError("unmatched brace (write {{ or }} for a literal one)")
        pos = m.end()
    if pos < len(template):
        parts.append((False, template[pos:]))
    return parts


def fields_of(template: str) -> set[str]:
    return {text for is_field, text in parse(template) if is_field}


def render(template: str, values: dict[str, str]) -> str:
    return "".join(values.get(text, "") if is_field else text
                   for is_field, text in parse(template))


# --- options -------------------------------------------------------------------

def _check(options: Any, where: str, allow_agents: bool) -> None:
    if not isinstance(options, dict):
        raise ValueError(f"{where} must be a JSON object")
    for key, value in options.items():
        if key == "agents" and allow_agents:
            if not isinstance(value, dict):
                raise ValueError("agents must be an object keyed by agent id")
            for agent, override in value.items():
                _check(override, f"agents.{agent}", allow_agents=False)
        elif key not in OPTION_KEYS:
            raise ValueError(f"unknown option {key!r} in {where}")
        elif key == "preset" and value not in PRESETS:
            raise ValueError(f"unknown preset {value!r}")
        elif key == "template":
            parse(value)
        elif key in CHOICES and value not in CHOICES[key]:
            raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
        elif key == "separator" and not isinstance(value, str):
            raise ValueError("separator must be a string")
        elif key == "hide_when_zero" and not isinstance(value, bool):
            raise ValueError("hide_when_zero must be true or false")
        elif key == "color" and value not in ("none", "threshold") and value not in COLORS:
            raise ValueError(f"unknown color {value!r}")


def resolve(config: Any, agent: str) -> dict:
    """Effective options for one agent: defaults < preset < top-level options
    < agents[agent]. A template or preset in the override replaces both of
    the top level's."""
    _check(config, "config", allow_agents=True)
    chosen = {k: v for k, v in config.items() if k != "agents"}
    override = config.get("agents", {}).get(agent, {})
    if "template" in override or "preset" in override:
        chosen.pop("template", None)
        chosen.pop("preset", None)
    chosen.update(override)
    preset = PRESETS[chosen.get("preset", "default")]
    options = {**DEFAULTS, **preset, **chosen}
    if "template" in chosen:
        options["template"] = chosen["template"]
    return options


def load_options(agent: str, path: str | None = None) -> dict:
    """Options for agent from the config file; the defaults when there is
    none. Raises ValueError (or OSError) when it can't be used."""
    path = path or config_path()
    if not os.path.exists(path):
        return resolve({}, agent)
    with open(path, encoding="utf-8") as f:
        return resolve(json.load(f), agent)


# --- values --------------------------------------------------------------------

def fmt_tokens(saved_bytes: float) -> str:
    n = saved_bytes / 4
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:.0f}"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def session_info(payload: Any) -> dict[str, str]:
    """model, context_pct, context_bar and cwd from a status line's stdin JSON
    (Claude Code's shape; other agents' keys are tried too). Missing ones are
    left out, so they render empty."""
    if not isinstance(payload, dict):
        return {}
    info: dict[str, str] = {}
    model = payload.get("model")
    if isinstance(model, dict):
        model = model.get("display_name") or model.get("name") or model.get("id")
    if isinstance(model, str) and model:
        info["model"] = model
    workspace = payload.get("workspace")
    cwd = (workspace.get("current_dir") if isinstance(workspace, dict) else None) \
        or payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        home = os.path.expanduser("~")
        info["cwd"] = "~" + cwd[len(home):] if cwd == home or cwd.startswith(home + "/") else cwd
    pct = context_pct(payload.get("context_window"))
    if pct is not None:
        info["context_pct"] = f"{pct:.0f}%"
        filled = round(BAR_WIDTH * pct / 100)
        info["context_bar"] = "█" * filled + "░" * (BAR_WIDTH - filled)
    return info


def context_pct(window: Any) -> float | None:
    if not isinstance(window, dict):
        return None
    pct = window.get("used_percentage")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        usage, size = window.get("current_usage"), window.get("context_window_size")
        if not isinstance(usage, dict) or not isinstance(size, (int, float)) or size <= 0:
            return None
        used = sum(v for k, v in usage.items()
                   if k.endswith("input_tokens") and isinstance(v, (int, float)))
        pct = 100 * used / size
    return max(0.0, min(100.0, float(pct)))


def values_for(totals: dict | None, session: dict[str, str], agent: str,
               options: dict) -> tuple[dict[str, str], dict[str, int]]:
    """(rendered field values, the raw number behind each count field)."""
    totals = totals or {}
    today_m, today_e = (totals.get("today") or [0, 0])[:2]
    total_m, total_e = (totals.get("total") or [0, 0])[:2]
    fmt = fmt_bytes if options["units"] == "bytes" else fmt_tokens
    show_est = options["estimated"] == "show"

    def pair(measured: int, estimated: int) -> str:
        text = fmt(measured)
        return f"{text} (~{fmt(estimated)})" if estimated and show_est else text

    def est(estimated: int) -> str:
        return fmt(estimated) if show_est else ""

    pkg_flagged = int(totals.get("pkg_flagged", 0))
    redacted = int(totals.get("redacted", 0))
    checked = int(totals.get("checked_today", 0))

    def today_or_checked(text: str, shown: int) -> str:
        # Nothing saved yet today: show that ctx-guard is checking calls.
        return f"{text} · {checked} checked" if not shown and checked else text

    values = {
        "today": fmt(today_m), "total": fmt(total_m),
        "today_est": est(today_e), "total_est": est(total_e),
        "today_all": today_or_checked(fmt(today_m + (today_e if show_est else 0)),
                                      today_m + (today_e if show_est else 0)),
        "total_all": fmt(total_m + (total_e if show_est else 0)),
        "today_pair": today_or_checked(pair(today_m, today_e),
                                       today_m + (today_e if show_est else 0)),
        "total_pair": pair(total_m, total_e),
        "pkg_flagged": str(pkg_flagged), "redacted": str(redacted), "checked": str(checked),
        "agent": agent,
    }
    values.update(session)
    # Savings fields count as non-zero on any total: today is a subset of it.
    savings = total_m + total_e
    raw = {name: savings for name in SAVINGS_FIELDS}
    raw.update(pkg_flagged=pkg_flagged, redacted=redacted, checked=checked)
    return values, raw


def paint(text: str, color: str, session: dict[str, str]) -> str:
    if color == "threshold":
        try:
            pct = float(session.get("context_pct", "").rstrip("%"))
        except ValueError:
            return text
        color = next((name for limit, name in COLOR_THRESHOLDS if pct >= limit), "none")
    if color not in COLORS:
        return text
    return f"\033[{COLORS[color]}m{text}\033[0m"


def format_segment(totals: dict | None, session: dict[str, str], agent: str,
                   options: dict) -> str:
    """The rendered segment, or "" when hide_when_zero applies: every count
    field the template uses is zero (a template with none never hides)."""
    template = options["template"]
    values, raw = values_for(totals, session, agent, options)
    counts = [raw[name] for name in fields_of(template) if name in raw]
    if options["hide_when_zero"] and counts and not any(counts):
        return ""
    text = render(template, values).strip()
    return paint(text, options["color"], session) if text else ""


def needs_session(options: dict) -> bool:
    return options["color"] == "threshold" or bool(
        fields_of(options["template"]) & set(SESSION_FIELDS))


# --- install / uninstall ---------------------------------------------------------

def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_config(manifest: dict | None, preset: str | None = None,
                 template: str | None = None, path: str | None = None) -> str:
    """Set the preset or template in the config file, keeping its other
    options. Records in the manifest whether this created the file, so
    uninstall removes only a file ctx-guard made. Raises ValueError for an
    unknown preset or a bad template, or an existing config that isn't a JSON
    object (it is left as it is)."""
    path = path or config_path()
    if preset is not None and preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r} (choose from {', '.join(PRESETS)})")
    if template is not None:
        parse(template)
    config: dict = {}
    existed = os.path.exists(path)
    if existed:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            raise ValueError(f"{path} is not a JSON object")
    config.pop("preset", None)
    config.pop("template", None)
    if preset is not None:
        config["preset"] = preset
    if template is not None:
        config["template"] = template
    data = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "wb") as f:
        f.write(data)
    os.replace(temporary, path)
    if manifest is not None:
        entry = manifest.get("statusline_config") or {}
        created = entry.get("created", False) if entry.get("path") == path else False
        manifest["statusline_config"] = {"path": path, "created": created or not existed,
                                         "sha256": _digest(data)}
    return path


def remove_config(manifest: dict) -> None:
    """Uninstall: remove the config file only if install created it and it is
    unchanged since ctx-guard last wrote it."""
    entry = manifest.pop("statusline_config", None)
    if not isinstance(entry, dict) or not entry.get("created"):
        return
    path = entry.get("path", "")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return
    if _digest(data) != entry.get("sha256"):
        print(f"Preserving changed status line config {path}")
        return
    os.remove(path)
    try:
        os.rmdir(os.path.dirname(path))
    except OSError:
        pass
    print(f"Removed {path}")
