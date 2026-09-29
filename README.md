# agent-team

Select a coding agent, configure primary/worker model combinations, and run
native subagents or an interactive tmux team.

## Project boundary

This repository owns reusable coding-agent adapters, configurable model presets,
collaboration instructions and the native/tmux execution modes. It does not
require dotfiles, tmux-agent-workbench, a particular checkout location, or a
specific GitHub account at runtime. Native mode does not require tmux.

A terminal workbench can offer launch entries that invoke these commands.
Personal model overrides, proxy environment and approval preferences belong in
the user's configuration or dotfiles. The CLI does not infer them from shell
functions. Team launches explicitly default to YOLO, as described below.

## Install

Requires Python 3.11+, at least one supported coding CLI, and tmux for pane mode.
Clone the repository and install the entrypoints:

```sh
git clone https://github.com/lukewang1024/agent-team.git
cd agent-team
python3 install.py
```

The installer links the commands into `~/.local/bin`; ensure it is on `PATH`.
The installer refuses to overwrite unrelated files. Keep the checkout in place.
It does not change authentication, global CLI configuration, or global permission defaults.

## Solo presets

`codex-budget` and `codex-expert` are installed CLI wrappers for ordinary Codex
sessions. They set only the model and reasoning effort: GPT-6 Luna Max for
bounded, low-cost work and GPT-6 Astra Medium for demanding work. They do not
enable team delegation or YOLO. Plain `codex` keeps its local default model.
These wrappers explicitly use embedded mode because their one-run settings
require `-c`.

The wrappers use the `codex.solo` presets in [teams.json](config/teams.json).
Override either preset under `codex.solo.budget` or `codex.solo.expert` in
`$XDG_CONFIG_HOME/agent-team/config.json`. Run
`agent-team solo codex budget --solo-dry-run` to inspect the command without
starting Codex. CLI wrappers cannot call a shell's `codex` function; shell
integrations that track session history can wrap these entrypoints separately.

`traex-budget` is the same kind of CLI wrapper for ordinary TraeX sessions. It
uses `traex.solo.budget` in [teams.json](config/teams.json) and preserves the
existing GPT-5.6 Luna High and YOLO settings. Override that preset in
`$XDG_CONFIG_HOME/agent-team/config.json`, or inspect its launch command with
`agent-team solo traex budget --solo-dry-run`.

## Usage


`agent-team` opens a CLI picker for Codex, TraeX, OpenCode, or Claude, followed
by a preset picker. Codex offers expert, team, and budget; the other tools offer
team and budget. The default uses native subagents. Add
`--tmux` to select interactive pane workers instead, for the whole session.

```sh
agent-team                         # pick agent and preset; native subagents
agent-team --tmux                  # pick agent and preset; interactive panes
agent-team codex                   # select directly
agent-team claude --tmux
agent-team opencode --team-budget --tmux
codex-team                         # shortcut for agent-team codex
codex-team-expert                  # Astra Medium lead + Sol High workers
codex-team-budget --tmux           # budget shortcut
```

Equivalent `traex-team`, `opencode-team`, and `claude-team` shortcuts and their
`-budget` forms and `codex-team-expert` are installed by `python3 install.py`.
All launch the same controller.

`codex-yolo`, `traex-yolo`, `claude-yolo`, and `opencode-yolo` only enable each
CLI's permissive execution mode and forward the remaining arguments. They use
the CLI's normal model settings and do not start a team.

`agent-team run <tool>` starts one CLI with the selected team preset's model;
`--budget` uses its worker model and effort when configured. This path defaults
to YOLO and supports `--no-yolo`. It does not enable delegation. The separate
`agent-team solo` presets above keep their own model and permission settings.
Python 3.11+ is required. tmux is required only for pane mode. These are executable wrappers,
so shell-only aliases/functions and their permission defaults do not apply;
Team and Team Budget default to YOLO for the lead and workers in both native
and tmux modes. Codex/TraeX receive `--dangerously-bypass-approvals-and-sandbox`;
Claude receives `--dangerously-skip-permissions`; OpenCode receives scoped
`allow` permissions. Worker delegation restrictions remain enforced.

Use `agent-team codex --no-yolo` (also with `--tmux` / `--team-budget`) to inherit
CLI permission settings for the whole team, or set `"yolo": false` in a preset.
When supplying restricted permission/profile arguments, also use `--no-yolo`.
This changes newly launched sessions; existing sessions keep their permissions.

Native mode uses each tool's own delegation: Codex/TraeX agent configuration,
Claude's team-worker subagent definition, or OpenCode's team-worker definition.
The collaboration policy has a two-worker limit; Codex also receives a
configuration cap. TraeX/Claude/OpenCode's native cap is instruction-level. In tmux
mode the controller enforces the pane cap and disables native delegation.

Tmux mode creates one dedicated window (or a new session when outside tmux).
The lead is left, workers are stacked right: `main-vertical`, corresponding to
prefix + Alt+4. Workers are actual interactive CLIs, not transcript viewers.
Pane headers keep a fixed `lead |` or `worker |` prefix before the live terminal
title, so application title updates cannot erase the role.
Switch panes to inspect progress or interact. Existing windows and panes are
left unchanged. Native and pane members never mix in the same team. Team management uses
spawn, send, receive, report, wait, status and stop commands; see
[the pane protocol](config/team-pane.md). Messages persist in a local mailbox. They are
read at checkpoints or through a bounded wait, not pushed through a tool-native
notification API. `send --wake` submits a notice to an idle worker's input;
avoid doing that while a person is typing. Lead exit normally closes worker panes.
Forced terminal/server termination may need manual cleanup of the team window.

Customize combinations in `$XDG_CONFIG_HOME/agent-team/config.json`
(default `~/.config/agent-team/config.json`). This is a partial override of
[teams.json](config/teams.json); omitted values use the CLI's existing defaults.
For example (replace model identifiers with ones your provider supports):

```json
{
  "claude": {
    "team": {"model": "opus", "worker_model": "sonnet", "worker_effort": "high"},
    "budget": {"model": "sonnet", "worker_model": "haiku"}
  }
}
```

Supported team keys: `model`, `effort`, `worker_model`, `worker_effort`, `limit` (1 or
2), `yolo` (boolean, default true), `args`, `worker_args` (CLI argument arrays),
and agent-level `env` defaults. Environment defaults apply only when the
variable is not already present externally. OpenCode models require
provider/model identifiers and reasoning effort is provider-specific. Tool names
and delegation rules are independent of model choices. Other tools have no
assumed budget model: configure a distinct budget combination when desired.
There is no automatic budget switching.

Codex additionally reads the existing `team.config.toml`, `team-budget.config.toml`,
or `team-expert.config.toml`
profiles in CODEX_HOME (default `~/.codex`), when present. No personal profile
is required. The bundled Codex team presets are:

| Command | Lead | Workers |
| --- | --- | --- |
| `codex-team-expert` | GPT-6 Astra, medium | GPT-6 Sol, high |
| `codex-team` | GPT-6 Sol, high | GPT-6 Sol, low |
| `codex-team-budget` | GPT-6 Sol, high | GPT-6 Luna, max |

The equivalent direct launch option is `agent-team codex --team-expert`.
Solo presets above remain separate from team presets.

The JSON override, when present,
takes precedence for team settings. Command-line options passed to the lead only
affect the lead; use worker_args or worker_model/worker_effort for child settings.
The controller injects [shared team rules](config/team-rules.md) into each supported tool, including
TraeX. Existing tool configuration/auth remains in place. Codex/TraeX team mode
overrides developer_instructions; Claude appends to its system prompt; OpenCode
adds scoped agent definitions through OPENCODE_CONFIG_CONTENT.

State, task briefs and internal messages live under `$XDG_STATE_HOME/agent-team`
(default `~/.local/state/agent-team`), with private per-team directories. These
are local coordination data, not permission grants. User permissions still apply
in every pane. Model access, login and provider support remain tool-specific.
Use `--team-dry-run` to inspect a launch plan without model calls. Adapter and
simulated tmux tests do not certify model quality or every provider's support.

## Development

Run `python3 -B -m unittest discover -s tests -v`. Tests use an isolated tmux
server and mock interactive CLIs; they do not send model requests.

## Runtime validation and permissions

The resolved preset and worker model/effort are injected into the lead's
instructions. Native workers must use those values explicitly and report an
unavailable combination instead of substituting another model. This is a native
agent instruction, not an API-level model lock; verify actual child session
metadata in live acceptance runs. Native launches remove inherited AGENT_TEAM_*
variables so a nested native session does not retain pane-controller identity.

For Codex/TraeX pane launches, `-C DIR` / `--cd DIR` are normalized before creating
the team. The lead, workers and state share that directory. Conflicting worker
cwd flags are rejected. The tmux socket path is recorded in team state so shell
tools need not preserve TMUX to find the correct server. Worker names are
lowercase identifiers such as `worker_a`, never `A` or `B`.

Run `agent-team doctor` from the agent's shell execution context before writing
briefs. It verifies socket access and pane ownership. Spawn, stop and wake also
check the control channel before performing changes. A check in an ordinary
host shell does not establish that a sandboxed shell has the same access.

With YOLO disabled, for Codex with an explicit `-P` / `--permission-profile`, or explicit read-only /
workspace-write `--sandbox`, the pane runner also performs a model-free
`codex sandbox` probe before starting the interactive CLI. It preserves managed
requirements and writes `preflight-<role>.json`. With explicit approval policy
`never`, a failed probe stops before inference. Otherwise the diagnostic is
shown and the CLI starts so the normal approval flow remains available. This requires a Codex version
supporting named sandbox permission profiles. For inherited/default permissions,
other sandbox overrides and other CLIs, the in-session doctor is required; the
launcher does not guess the effective permissions. Extra writable directories
are not socket permissions.

A restricted Linux sandbox can deny tmux socket connections. In that case the
team stops delegation with the exact error; it does not silently fall back to
native workers or change sandbox settings. Use the coding CLI's normal approval
mechanism when permitted, or an explicitly authorized permission profile that
can access tmux. Profiles with approval disabled and socket access denied cannot
run pane workers. When YOLO is disabled, the wrapper adds no permission bypass. The team state
directory must also be writable by each member to exchange reports; authorize
that directory with the CLI's normal writable-root configuration when needed.

See [the regression report](docs/validation-2026-09-21.md) for the distinction
between automated checks and live acceptance evidence.
