# Runtime fixes and acceptance status

The initial real audit at commit 678a898 completed native delegation, but both
budget workers used Astra Low instead of Luna High. The interactive tmux audit
could not connect to the socket inside a workspace-write, approval-never sandbox.

This change injects the resolved worker combination into native instructions,
clears inherited pane identities in native launches, normalizes Codex/TraeX cwd
flags, persists the exact tmux socket, documents valid worker names, timestamps
messages, and adds an execution-context doctor and Codex sandbox preflight.

Validation:

- Standard-library unit and isolated tmux integration tests cover configured
  model instructions, identity cleanup, cwd handling, socket diagnostics,
  interactive pane layout, cap, mailbox, reuse and cleanup. Coding CLIs are fake
  in these automated tests; no model calls are made.
- A manual real Codex sandbox probe reproduces the socket denial and reports it
  before inference when approval is explicitly disabled. It does not grant
  permission or switch delegation modes.
- A manual real native budget retry on 2026-09-21 used a two-worker arithmetic
  task to minimize unrelated context. The provider connection repeatedly reset,
  then remained offline after HTTPS fallback. It produced no worker sessions
  before the observer stopped it. This is not a passing model-routing test.

Remaining acceptance gates:

1. Rerun native team and budget tasks when the provider connection recovers;
   inspect actual child model/effort metadata, not just the prompt or argv.
2. Run interactive tmux workers with an explicitly authorized socket-access
   configuration or normal tool approval. Collect two real reports, verify cwd,
   inspect the layout, and reuse an idle worker. A socket-denied, approval-never
   configuration cannot do this; the launcher must not silently relax it.

The native model rule is instruction-level, not a hard API-level restriction.
The current report does not establish model quality or cost savings. Other
coding agents have adapter tests, not new real-model acceptance results.

## Diagnosed causes and controlled confirmation (22:37)

The earlier connection failures were caused by the test process environment,
not established provider downtime. The parent Codex process has an existing
localhost HTTP proxy, but its shell_environment_policy.inherit=core removes
proxy variables before starting agent-team. The launcher copies that already
filtered environment. A direct unauthenticated HTTPS probe timed out; the same
probe through the existing proxy completed TLS and returned the expected 401.

Restoring only those existing proxy variables for the diagnostic subprocess
made the native budget test finish in about 33 seconds, exit 0. Actual child
session metadata confirmed Astra Medium for the lead and Luna High for both
fresh-context workers. Answers 323 and 667 were correct. This validates basic
budget routing; it is not a coding-quality or cost comparison. No global proxy
configuration was changed.

The tmux failure is independent: a minimal AF_UNIX connection to the same socket
with UID 1001 and mode 0660 succeeds outside the sandbox (connect_ex=0) and fails
in Codex :workspace (connect_ex=1, EPERM). Filesystem permissions and cwd are not
the cause. The previous test explicitly selected approval never, preventing the
normal approval route. Restoring HTTP proxies does not grant socket access.
The user's interactive zsh codex function also adds --approve-for-me by default;
agent-team invokes the executable directly and does not inherit shell functions.

Next steps should preserve the existing environment deliberately for child CLI
launches, and validate tmux with an explicitly chosen normal approval policy for
both lead and worker sessions. Preflight is diagnostic; it does not make a
socket-denied, approval-never policy compatible with pane workers. No silent
sandbox relaxation or unapproved fallback should be added.
