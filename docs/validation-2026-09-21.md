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
