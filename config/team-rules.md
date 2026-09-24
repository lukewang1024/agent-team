# Team collaboration rules


When session instructions state "Team delegation mode is enabled" (the
`agent-team` and its tool-specific shortcuts enable it), proactively delegate bounded,
independent subtasks whenever parallel work or a separate context materially
improves speed or quality. Do not wait for the user to request subagents again.
Simple tasks stay with the primary agent. Without this opt-in marker, this
section does not enable delegation.

The primary agent owns decomposition, important decisions, integration, and
final verification. Use at most two concurrent subagents (or a lower configured
limit) and keep delegation one level deep: subagents do not spawn further agents.
The main session selects one execution mode at startup. By default use native
subagents only, without tmux panes. With explicit `--tmux`, use only interactive
pane workers through `agent-team`, following the injected pane protocol. Do not
mix the two modes or migrate running members between them. Opt-in pane mode
uses a dedicated team window and leaves existing windows and panes unchanged.

Use the configured subagent model and reasoning effort, independently of the
primary agent's settings. These combinations are user-customizable; no model
choice is implied by the entrypoint name. Do not switch budget profiles or
silently raise model or reasoning settings automatically.

### Assign bounded work

For multi-part work, maintain a lightweight task list in the primary agent's
plan or available task tool: task, owner, dependencies, file ownership, acceptance
criteria, and status (pending, running, blocked, ready for review, or complete).
Do not create a separate task service or persistent task files by default. Only
start dependent work when its prerequisites are available. Assign disjoint files
for parallel edits; shared-file changes belong to one owner or run sequentially.
Do not duplicate work already assigned to another agent.

Give each subagent a concise brief containing the goal, confirmed facts, relevant
paths, edit boundaries, dependencies, constraints, and observable acceptance
criteria. Prefer a fresh context with that brief; use partial history when it
adds necessary information. Avoid full-history inheritance when it forces the
primary agent's model settings or copies unrelated exploration and logs.

For unclear root causes, consider assigning distinct hypotheses or review angles
to independent investigators. Ask for evidence that supports or refutes each
hypothesis, then compare the results before choosing a fix. Keep tightly coupled
decisions with the primary agent. For complex changes, ask for a short investigation
or proposed approach first and assess it before assigning implementation; routine
tasks do not need an extra planning round or user approval.

### Coordinate and reuse agents

Reuse an existing agent for follow-up work in the same area when its context is
still relevant. Start a new agent when the task needs a different scope or fresh
context, rather than spawning one for every small step. The primary agent should
continue useful independent work while delegated tasks run, using native result
notifications or waits instead of repeatedly polling.

Once useful independent work is exhausted and completion depends on live
subagents, the primary agent must use the runtime's native agent-wait mechanism.
Treat collecting required delegated results, integration, and final verification
as critical-path work: do not end the turn or return an idle prompt merely because
the remaining work is delegated. If a wait times out while required agents are
still live, wait again unless new user input arrives or another meaningful task
can be advanced. This intentional wait also keeps the host's native working or
"waiting for agents" status visible to the user.

Subagents promptly report blockers, conflicting assumptions, interface changes,
and evidence that affects another task. Include the affected task, concrete
evidence, and the decision or input needed. Use a targeted message to the relevant
agent when supported and keep the primary agent informed of coordination changes;
otherwise route through the primary agent. Avoid routine broadcast updates.
Changes to shared contracts or file ownership require primary-agent coordination.
When blocked, stop dependent edits and report the issue rather than guessing.

### Verify before completing

A subagent returns a concise result with findings, evidence or file references,
changed files, validation performed and its outcome, and unresolved issues. Its
completion notification means the work is ready for review, not automatically
accepted. The primary agent checks the result against the acceptance criteria,
reviews relevant changes, and performs appropriate integration verification.
Reuse existing validation evidence when sufficient; do not rerun checks without
a reason. A claim of success without evidence is not enough to mark a task done.

If verification fails, give the existing agent a specific correction or take over.
After a failed attempt, reassess the approach rather than repeatedly spawning
retries. Reconcile conflicting findings using source evidence. Before declaring
the overall task complete, collect and assess all required delegated results,
resolve or explicitly report blockers, and stop work that is no longer needed.
