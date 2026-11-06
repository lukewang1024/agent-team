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

## CodexRadar recommendations

The Codex Create Agent menu reads the remote station-recommendation cache. It
shows all distinct categories in the source order and keeps the newest model
version within each category. Category names and model/effort pairs remain
remote-driven; an optional `menu_category_limit` can limit the CLI picker.
Codex defaults are used only when no recommendations are available. Fast follows
the selected recommendation when the website marks it `+ Fast`. Override it
with `--no-fast` to save budget or `--fast` to enable it explicitly.

The menu prepares recommendations only when Create Agent → Codex opens. It
parses the homepage's Station Picks section. It fetches synchronously
if no valid cache exists. When a valid cache is at least 24 hours old, it
refreshes in the background and keeps using the active cache; a successful
refresh is promoted on the next menu open. Direct Codex launches read the cache
without fetching.

```sh
agent-team recommendations status
agent-team start codex --recommendation <ID>
agent-team start codex --recommendation <ID> --team
agent-team start codex --recommendation <ID> --team --tmux
agent-team start codex --recommendation <ID> --fast
agent-team start codex --recommendation <ID> --no-fast
agent-team start codex --team --dry-run
```

Use the `id` from `agent-team recommendations status` when selecting a
recommendation directly. Cache files live under `$XDG_CACHE_HOME/agent-team`
(default `~/.cache/agent-team`). If the first fetch fails or returns no usable
recommendations, the menu shows no hard-coded Codex tiers and still allows
Codex to start with its native defaults. A failed refresh is retried after five
minutes on a later menu visit, while retaining any previous recommendations. Missing or stale recommendation data never
blocks agent creation.

Trae retains its daily, hard and batch scenes. `--adjust quick` is a Trae-only
option and selects Astra low. Trae model/effort settings are checked against
`traex debug models` when requested. CodexRadar recommendations can be checked
with `agent-team recommendations status` or refreshed manually with
`agent-team recommendations refresh`. Press `r` in the interactive Codex
recommendation picker to refresh immediately, or `f` to toggle Fast off. The
Workbench Create Agent menu offers the same budget override.

Team defaults to YOLO; `--no-yolo` inherits CLI permissions for the entire team.
For Codex and Trae, `--yolo` explicitly enables YOLO for Single or Team, including
workers, while `--no-yolo` disables it. Single otherwise inherits CLI permissions. Existing
sessions keep their current settings. `--dry-run` prints the resolved config
and argv without starting a model session.

Native mode uses each tool's own delegation: Codex/TraeX agent configuration,
Claude's team-worker subagent definition, or OpenCode's team-worker definition.
The default limit is four concurrent workers; Codex also receives a
configuration cap. Use fewer when work cannot be usefully parallelized.
TraeX/Claude/OpenCode's native cap is instruction-level. In tmux
mode the controller enforces the pane cap and disables native delegation.

Tmux mode creates one dedicated window (or a new session when outside tmux).
The lead is left, workers are stacked right: `main-vertical`, corresponding to
prefix + Alt+4. Workers are actual interactive CLIs, not transcript viewers.
Pane headers keep a fixed `lead |` or `worker |` prefix before the live terminal
title, so application title updates cannot erase the role.
Switch panes to inspect progress or interact. Existing windows and panes are
left unchanged. Native and pane members never mix in the same team. Team management uses
spawn, assign, send, receive, ack, report, wait, status and stop commands; see
[the pane protocol](config/team-pane.md). Messages persist in a local mailbox. They are
read at checkpoints or through a bounded wait, not pushed through a tool-native
notification API. Messages remain available until explicitly acknowledged, and
`receive --all` inspects mailbox history. Teams already running when the CLI is
updated retain their original mailbox and reporting protocol. Assignment and attempt IDs fence late
reports; `assign` creates a follow-up or a retry on a settled worker. Optional
request IDs make repeated send/assign requests idempotent. `send --wake` submits a notice to an idle worker's input;
avoid doing that while a person is typing. Lead exit normally closes worker panes.
Forced terminal/server termination may need manual cleanup of the team window.

For Codex native teams, the main agent can choose a model and thinking level for
each spawn; the configured `worker_model` and `worker_effort` are the default
pair. In `--tmux` mode, choose per worker with
`agent-team spawn <name> --file <brief> --model <model> --effort <effort>`.
The combined level is the model tier followed by the thinking rank: Luna (1),
Terra (2), Sol (3), Astra (4); low (1), medium (2), high (3), xhigh (4), max (5),
ultra (6). For example, Sol max is 3.5 and Astra xhigh is 4.4. A Sol high lead
(3.3) can use Sol high or any lower combined level, but cannot use Sol xhigh or
any Astra model. Defaults offer only the latest model generation configured for
each CLI and tier. Codex also considers newer generations in the active
CodexRadar cache for worker choices, while the selected main-agent recommendation
remains the exact CodexRadar model/effort pair. Model-family suffixes map to the
Luna/Terra/Sol/Astra tiers, including future versioned names such as `gpt-7-sol`.
Trae retains its configured GPT-5.6 Luna default because its current catalog has
no GPT-6 Luna. These mappings are listed in
[model-levels.json](config/model-levels.json). Native Codex expresses this
ceiling as an agent instruction because native spawn calls do not pass through
the `agent-team` controller; tmux spawn requests are rejected by the controller.

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

Because that example uses different Claude models for lead and worker, add their
tiers to `$XDG_CONFIG_HOME/agent-team/model-levels.json`:

```json
{
  "model_tiers": {"opus": 4, "sonnet": 3, "haiku": 1},
  "latest_models": {"claude": ["haiku", "sonnet", "opus"]}
}
```

Thinking ranks come from the built-in policy unless overridden. The combined
worker level must not exceed the lead's model tier and thinking rank.

Supported team keys: `model`, `effort`, `worker_model`, `worker_effort`, `limit`
(1–8, default 4), `yolo` (boolean, default true), `args`, `worker_args` (CLI
argument arrays),
and agent-level `env` defaults. Environment defaults apply only when the
variable is not already present externally. OpenCode models require
provider/model identifiers and reasoning effort is provider-specific. Tool names
and delegation rules are independent of model choices. Other tools have no
assumed budget model: configure a distinct budget combination when desired.
There is no automatic budget switching.

Codex model and effort choices come from the selected CodexRadar recommendation;
Codex scenes are not locally configured. The selected recommendation supplies
the lead pair, and `worker_model` / `worker_effort` remain the default pair for
per-task subagent selection in configured team presets. The lead receives the
launch-time cached scene recommendations that fit its ceiling and available
model descriptions from the local Codex catalog, and should match them to each
subtask. When the catalog advertises supported effort levels for a model,
worker choices omit other levels; a stale catalog is advisory. Unknown and above-ceiling pairs are omitted. These are
advisory, and native routing remains instruction-driven. Set `codex.team.limit`
in the override file to change its worker cap.
To rank a custom model or alias, add its positive integer tier under
`model_tiers` in `$XDG_CONFIG_HOME/agent-team/model-levels.json`. Add a
`model_families` suffix when a provider introduces versioned model names that
share a family tier. Add a `latest_models` list for other tools when their
default choices should use a specific newest model per tier. Codex uses the
newest model per tier from the active CodexRadar cache, with this list as its
fallback. The original flat `{model: tier}` override format remains accepted.
When two different models cannot be ranked, team launch or worker spawn fails
closed.
Trae scene overrides remain available under `traex.scenes.<scene>` in
`$XDG_CONFIG_HOME/agent-team/config.json`.

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

The resolved preset, lead combined level, allowed model/effort pairs and default
worker pair are injected into the lead's instructions. Native Codex receives a
per-task selection policy; its ceiling is instruction-level, not an API-enforced
lock. Tmux worker requests validate the selected pair against the lead level
before creating a pane. Native launches remove inherited AGENT_TEAM_* variables
so a nested native session does not retain pane-controller identity.

For Codex/TraeX pane launches, `-C DIR` / `--cd DIR` are normalized before creating
the team. The lead, workers and state share that directory. Conflicting worker
cwd flags are rejected. The tmux socket path is recorded in team state so shell
tools need not preserve TMUX to find the correct server. Worker names are
lowercase identifiers such as `worker_a`, never `A` or `B`.

Workbench resurrection rebinds the saved team to its new panes, then uses
`resume-pane` to restore each session with its original team instructions,
member model, and supported launch options. Initial prompts are not replayed.
Unknown saved options fail explicitly rather than silently changing the team's
permissions. This requires both the Workbench team sidecar and the original
team state directory.

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
