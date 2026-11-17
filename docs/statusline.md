# Status line

```bash
bash install.sh --statusline
```

Adds a live savings segment to the Claude Code and Copilot CLI status lines,
for the agent you are in:

```
ctx-guard ⬇ today 12.4k (~3.1k) · total 1.2M (~240k)
```

Tokens (bytes/4). Plain numbers are MEASURED, `(~…)` ESTIMATED; "today" is
since local midnight. It sets `statusLine` in `~/.claude/settings.json` and
`~/.copilot/settings.json` to `ctx-guard-statusline <agent>`; a status line you
already had is recorded in the install manifest, run first with the same
stdin, and the segment is appended after ` │ `. `ctx-guard-uninstall` restores
the original (or removes ours) unless you changed it since. To stay fast on
every refresh it keeps an incremental rollup, `stats.rollup.json`, next to
`stats.jsonl` (mode `600`, rebuilt if the log is replaced or truncated), and it
does not drain sandbox spools, so it can trail `ctx-guard-stats` by one tool
call. With no stats or no savings yet the segment is empty.

In Copilot CLI the segment shows in the footer's `custom` item; if you don't
see it, turn that item on in Copilot's footer settings. The agents and their
config files are listed in one place, `TARGETS` in
`hooks/lib/statusline_config.py`, which install and uninstall both read.

## Presets and templates

Pick a bundled preset, or write your own template. Both only write
`${XDG_CONFIG_HOME:-~/.config}/ctx-guard/statusline.json` and change nothing
else; see them all with your own numbers first:

```bash
ctx-guard-statusline --preview
bash install.sh --statusline-preset security
bash install.sh --statusline-format '⬇ {today_all} · 🛡 {pkg_flagged}'
```

| Preset | Example |
|---|---|
| `default` | `ctx-guard ⬇ today 2.0k (~1.0k) · total 13.0k (~1.0k)` |
| `minimal` | `⬇ 3.0k` |
| `security` | `⬇ 3.0k · 🛡 5` |
| `full` | `Opus ███████░░░ 72% · ⬇ today 2.0k (~1.0k) · total 13.0k (~1.0k)` |
| `powerline` | `ctx-guard  ⬇ today 2.0k (~1.0k)  total 13.0k (~1.0k)`, after a `` separator (needs a Nerd Font) |

`full` is a complete line for users without one, so it shows before there are
any savings.

A template is data, not code: ctx-guard substitutes `{name}` for these fields
only (`{{` and `}}` are literal braces), so a shared template never runs
anything.

| Field | Value |
|---|---|
| `{today}` `{total}` | measured savings |
| `{today_est}` `{total_est}` | estimated savings |
| `{today_all}` `{total_all}` | measured plus estimated |
| `{today_pair}` `{total_pair}` | `measured (~estimated)`, as in the default segment |
| `{pkg_flagged}` | package installs warned or blocked |
| `{redacted}` | archived outputs that had secrets masked |
| `{model}` `{context_pct}` `{context_bar}` `{cwd}` | from the status line's stdin; empty when the agent doesn't send them |
| `{agent}` | `claude-code`, `copilot-cli`, ... |

The config file takes these options:

```json
{
  "preset": "security",
  "estimated": "show",
  "units": "tokens",
  "separator": " │ ",
  "hide_when_zero": true,
  "color": "threshold",
  "agents": {"copilot-cli": {"preset": "minimal", "color": "none"}}
}
```

- `template` or `preset` (a template wins; default `default`).
- `estimated`: `show` or `hide` the `(~…)` estimates.
- `units`: `tokens` (bytes/4) or `bytes`.
- `separator`: between your own status line and ctx-guard's.
- `hide_when_zero`: print nothing while every count the template uses is zero.
- `color`: `none`, an ANSI name (`red`, `cyan`, `bright_green`, ...), or
  `threshold`: green, yellow and red from 50, 70 and 85% context, the same
  thresholds as ctx-guard's warnings.
- `agents`: overrides for one agent, keyed by agent id.

A missing or invalid file, an unknown preset or an unknown field falls back to
the default segment; `--preview` says what is wrong. `ctx-guard-uninstall`
removes the file only if the installer created it and you haven't edited it.

## With statusline.sh or your own script

Community status lines (such as [statusline.sh](https://statusline.sh/community))
replace `statusLine` when installed. Add ctx-guard to them with one line:

```bash
# in your status line script
printf ' %s' "$(ctx-guard-statusline claude-code --segment)"
```

`--segment` prints only ctx-guard's segment and never chains. If you later
rerun `install.sh --statusline`, ctx-guard chains your script and sets
`CTX_GUARD_STATUSLINE_CHAINING=1` while it runs; the embedded call then prints
nothing, so the segment still shows once and nothing loops. When another tool
has replaced the status line and it no longer shows ctx-guard,
`ctx-guard-stats` prints a one-line hint.
