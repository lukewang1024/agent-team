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

## Claude and OpenCode

These tools use a simple Single / Team launch flow and their default models.
Single adds no model, reasoning or permission overrides. Team retains the
existing delegation backend and permission policy; explicit personal team
settings remain supported. Native CLI arguments such as `--model` are forwarded.

```sh
agent-team start claude
agent-team start opencode --team
agent-team start claude --team --tmux
```

For uniform callers, explicit `--scene`, `--adjust quick`, `--fast` and
`--no-fast` are accepted but ignored with one notice on stderr. They never
select a model or reasoning level for these tools. `--team` and `--tmux`
remain effective; `--tmux` requires Team. The interactive picker asks only
Single or Team. Older explicit budget commands remain available for existing
scripts, but are not offered in the picker or Workbench menu.

## Trae scenes

Trae uses the same `daily`, `hard`, and `batch` scene interface. All roles use
`xhigh`, the highest reasoning level advertised by its current model catalog.

| Scene | Single | Team lead | Team worker |
| --- | --- | --- | --- |
| daily | Sol 6.1 · xhigh | Sol 6.1 · xhigh | Sol 6.1 · xhigh |
| hard | Astra 6 · xhigh | Astra 6 · xhigh | Sol 6.1 · xhigh |
| batch | Luna 5.6 · xhigh | Sol 6.1 · xhigh | Luna 5.6 · xhigh |

```sh
agent-team start traex --scene daily
agent-team start traex --scene hard --team
agent-team start traex --scene batch --team --tmux
```

Trae currently has no Luna 6 entry; batch explicitly configures Luna 5.6.
There is no Fast tier applied. Max Mode is a separate provider feature and
is not enabled by the scene launcher. `--check` validates model and effort
against `traex debug models` without making an inference request.
`--adjust quick` explicitly switches Single/the lead to Astra low.

## Codex scenes

Codex has three scenes, each available as Single or Team. Configuration is owned
by `codex.scenes` in [teams.json](config/teams.json); Single and Team share the
same scene, with a lead override only where needed.

| Scene | Single | Team lead | Team worker | Fast |
| --- | --- | --- | --- | --- |
| daily · 日常开发 | GPT-6.1 Sol · max | GPT-6.1 Sol · max | GPT-6.1 Sol · max | on |
| hard · 复杂攻坚 | GPT-6 Astra · xhigh | GPT-6 Astra · xhigh | GPT-6.1 Sol · max | off |
| batch · 批量执行 | GPT-6 Luna · max | GPT-6.1 Sol · high | GPT-6 Luna · max | off |

```sh
agent-team start codex --scene daily
agent-team start codex --scene hard --team
agent-team start codex --scene batch --team --tmux
agent-team start codex --scene daily --adjust quick
agent-team start codex --scene batch --team --dry-run
```

The default is daily Single. `--adjust quick` temporarily selects Astra low for
Single/the lead; it is not a fourth scene and retains the selected scene's
Worker and Fast settings. Fast applies to the entire scene. Native subagents
have no independently configurable speed switch; tmux workers use the same
scene policy. The launcher validates model, reasoning effort and Fast support
against Codex's local `models_cache.json`. Missing configuration or unsupported
combinations fail explicitly; no automatic model or effort fallback occurs.
Refresh Codex's model list first when the catalog is missing.

`codex-team` selects daily Team and accepts `--scene`. `codex-yolo` selects daily
Single with explicit YOLO. `codex-budget`, `codex-expert`, `codex-team-budget`
and `codex-team-expert` are removed, including installer-owned symlinks; there
are no compatibility aliases. Old Codex profile files are not read by scene
launches. Old personal `codex.team`, `codex.budget`, `codex.expert` and
`codex.solo` overrides must be migrated to `codex.scenes`.

For Codex and Trae, the terminal picker follows Agent → Scene → Single / Team.
Claude and OpenCode skip the scene step. `--tmux` uses interactive pane workers
for Team. Explicit legacy non-Codex budget commands remain available, including
`traex-budget` for a single TraeX session.

Team defaults to YOLO; `--no-yolo` inherits CLI permissions for the entire team.
Single inherits CLI permissions unless the caller explicitly opts in. Existing
sessions keep their current settings. `--dry-run` prints the resolved config
and argv without starting a model session.

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

Machine-local scene overrides belong in `codex.scenes.<scene>` in
`$XDG_CONFIG_HOME/agent-team/config.json`. Daily and hard use the same model and
effort for Single and lead. Batch adds `lead_model` and `lead_effort`. Team
adds `worker_model`, `worker_effort` and `limit`; `fast` is scene-wide.

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
