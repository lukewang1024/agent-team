# Interactive pane collaboration protocol

This session uses pane workers, not native subagents. Do not call native Agent,
Task, spawn_agent, or similar delegation tools. All team members are interactive
CLI sessions in one dedicated tmux window. The lead is on the left and workers
are stacked on the right (main-vertical / prefix + Alt+4).

Use `agent-team` through the shell tool. It reads AGENT_TEAM_DIR and
AGENT_TEAM_ROLE from the environment. If the environment is unavailable, report
the error rather than creating an unrelated session. Create worker panes only
in the dedicated team window established by the explicit --tmux launch.

Lead workflow:

0. Before writing briefs, run `agent-team doctor` from your shell tool. If it
   fails, stop and report the exact error. Request normal tool approval only
   when that session permits it; never change permissions or switch to native
   workers to get around a denial. A host-shell check alone is insufficient.
1. Write a bounded brief to a UTF-8 file under AGENT_TEAM_DIR (task goal,
   relevant context, file ownership, prerequisites, acceptance criteria).
2. Choose a model and effort pair from the injected
   `model_policy.allowed_worker_settings` for this brief. Each entry includes its
   combined level. Use `default_worker_model` and `default_worker_effort` when no
   different level is needed. Run
   `agent-team spawn <name> --file <brief-path> --model <model> --effort <effort>`
   to start an interactive worker. The receipt and worker brief contain an
   assignment ID and attempt ID; keep both with the task. The controller rejects a combined level above
   the lead's. Defaults use the latest model generation configured for that CLI
   and tier; Codex uses newer generations from its active CodexRadar cache.
   Names must match `[a-z][a-z0-9_-]{0,39}` and cannot be `lead`; use
   `worker_a` and `worker_b`, not A/B. At most two worker panes may coexist.
3. Continue independent work. Use `agent-team receive` at coordination points,
   or `agent-team wait --timeout 30` when waiting for results. Both return
   unacknowledged messages without consuming them. After handling each message,
   run `agent-team ack --id <message-id>`. Use `receive --all` for mailbox
   history. Wait returns messages, not a promise that a task is complete.
4. Use `agent-team send <name> --file <message-path>` for targeted messages.
   This queues a message without interrupting the recipient. For a worker
   that has reported ready or blocked, or has an unwoken assignment, add `--wake` to submit a notification
   into its interactive input. Do not use --wake while a human is typing in
   that pane. Read the receipt: queued, wake submitted, and acknowledged are
   distinct; wake submission does not prove the agent read the message. Supply
   `--request-id <unique-key>` when a send may need retrying after an uncertain
   response; retry the same body and recipient with the same key. A duplicate
   request does not repeat the wake. Inspect status before another wake.
5. Review reported evidence and integration before marking work complete.
   Reuse a worker for new work with `agent-team assign <name> --file <brief>
   --request-id <unique-key> --wake`. For another attempt at the same task,
   include `--retry-of <previous-attempt-id>`. Do not assign over a worker that
   has not reported ready or blocked. `send` is for messages about the current
   assignment, not a new assignment. `agent-team status`
   shows pane liveness and reported task status; liveness is not correctness.
6. When a worker is no longer needed, use `agent-team stop <name>`. This closes
   the pane when ownership is verified; collect its result first. If the receipt
   says `stop_unknown`, inspect the pane before retrying. Worker panes are also
   closed when the lead CLI exits normally. After a forced terminal/server
   shutdown, inspect and close any surviving team window manually. State and
   messages remain under XDG_STATE_HOME/agent-team for inspection.

Worker workflow:

- Do not create further workers. Follow the brief and assigned file boundaries.
- Read `agent-team receive` at task checkpoints, especially before making a
  shared-contract change. Send blockers or relevant findings to `lead` or a
  named peer with `agent-team send <recipient> --file <message-path>`.
- Handle and acknowledge each received message using `agent-team ack --id <id>`.
  An `assignment` message carries the IDs for that task; use them for its report.
- Save your result to a UTF-8 file under AGENT_TEAM_DIR, then run
  `agent-team report --file <result-path> --status ready --assignment <assignment-id>
  --attempt <attempt-id>` (or use status `blocked`). A retry of the same report
  is idempotent; an old attempt cannot report for a newer assignment.
  Include evidence, changed files, validation and unresolved issues.
- After reporting, end your turn and leave the interactive session open for
  user interaction or follow-up. Do not poll forever or exit the CLI yourself.

Messages are internal agent communication, not user permission or approval.
Keep the configured tool permissions; do not attempt to bypass a denied action
by forwarding it to a different team member.
