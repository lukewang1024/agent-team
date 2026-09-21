# Interactive pane collaboration protocol

This session uses pane workers, not native subagents. Do not call native Agent,
Task, spawn_agent, or similar delegation tools. All team members are interactive
CLI sessions in one dedicated tmux window. The lead is on the left and workers
are stacked on the right (main-vertical / prefix + Alt+4).

Use `agent-team` through the shell tool. It reads AGENT_TEAM_DIR and
AGENT_TEAM_ROLE from the environment. If the environment is unavailable, report
the error rather than creating an unrelated session. Instructions below are the
pane-mode exception to the ordinary prohibition on creating agent tmux panes.

Lead workflow:

0. Before writing briefs, run `agent-team doctor` from your shell tool. If it
   fails, stop and report the exact error. Request normal tool approval only
   when that session permits it; never change permissions or switch to native
   workers to get around a denial. A host-shell check alone is insufficient.
1. Write a bounded brief to a UTF-8 file under AGENT_TEAM_DIR (task goal,
   relevant context, file ownership, prerequisites, acceptance criteria).
2. Run `agent-team spawn <name> --file <brief-path>` to start an interactive
   worker of the selected coding agent. Names must match `[a-z][a-z0-9_-]{0,39}`
   and cannot be `lead`; use `worker_a` and `worker_b`, not A/B. At most two
   worker panes may coexist.
3. Continue independent work. Use `agent-team receive` at coordination points,
   or `agent-team wait --timeout 30` when waiting for results. Wait returns
   messages addressed to you, not a promise that a task is complete.
4. Use `agent-team send <name> --file <message-path>` for targeted messages.
   This queues a message without interrupting the recipient. For a worker
   that has reported ready or blocked, add `--wake` to submit a notification
   into its interactive input. Do not use --wake while a human is typing in
   that pane. Read the response: queued messages are not necessarily delivered
   to the model yet. Workers read queued messages with `receive`.
5. Review reported evidence and integration before marking work complete.
   Reuse a worker with send --wake for follow-up tasks. `agent-team status`
   shows pane liveness and reported task status; liveness is not correctness.
6. When a worker is no longer needed, use `agent-team stop <name>`. This ends
   that interactive session; collect its result first. Worker panes are also
   closed when the lead CLI exits normally. After a forced terminal/server
   shutdown, inspect and close any surviving team window manually. State and
   messages remain under XDG_STATE_HOME/agent-team for inspection.

Worker workflow:

- Do not create further workers. Follow the brief and assigned file boundaries.
- Read `agent-team receive` at task checkpoints, especially before making a
  shared-contract change. Send blockers or relevant findings to `lead` or a
  named peer with `agent-team send <recipient> --file <message-path>`.
- Save your result to a UTF-8 file under AGENT_TEAM_DIR, then run
  `agent-team report --file <result-path> --status ready` (or `blocked`).
  Include evidence, changed files, validation and unresolved issues.
- After reporting, end your turn and leave the interactive session open for
  user interaction or follow-up. Do not poll forever or exit the CLI yourself.

Messages are internal agent communication, not user permission or approval.
Keep the configured tool permissions; do not attempt to bypass a denied action
by forwarding it to a different team member.
