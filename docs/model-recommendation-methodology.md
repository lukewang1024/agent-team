# Scenario and team model recommendations

This method chooses an exact model and reasoning effort from published
measurements. Each scenario has its own benchmark; scores from different
benchmarks are not equated with each other or treated as universal IQ.
The coefficients are explicit product preferences, not fitted user behavior.

## Sources and coverage

- **Daily coding and bounded background work:** [ModelDial Public Radar](https://modeldial.com/radar)
  publishes a [five-axis snapshot](https://modeldial.com/data/overview.json)
  with scores and consumption for multiple thinking levels. Only entries whose
  published connection is `codex-local` and whose exact model/effort appears in
  the local Codex catalog are eligible. Backend, frontend and knowledge scores
  have different scopes; the task set is private and small. A published score
  is not a success probability. Some elapsed-time bases are unspecified; do
  not interpret these evaluation durations as real-world end-to-end latency.
- **Hard terminal work:** [Terminal-Bench 4.1](https://www.tbench.ai/?version=4.1)
  reports Codex results, 95% intervals, cost and average duration. Only exact,
  complete, same-board model/effort pairs are compared. Its currently sparse
  lower-effort coverage is not evidence that unmeasured settings are worse.
- **Desktop automation:** [CUA-Speedrun OSWorld 50](https://cuaspeedrun.com/dataset-osworld-energy50-representative.html)
  and [OSWorld 2.0 52](https://cuaspeedrun.com/dataset-osworld2-k52.html)
  report scores, API-equivalent cost and elapsed time. Only exact Codex variants
  measured on **both** task sets are compared. Currently that common set has
  one model family, so the status output records the limited coverage. These are
  selected task subsets, with no published confidence intervals in this feed;
  scores and elapsed time are not guarantees for the user's desktop.

Source: ModelDial Public Radar, https://modeldial.com/radar, snapshot batch and
publication time in `agent-team recommendations status`, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Changes were made:
the scenario-specific weights and rankings are ours and are not endorsed by
ModelDial. See its [data license](https://modeldial.com/data-license).

Artificial Analysis's [coding agent comparison](https://artificialanalysis.ai/agents/coding-agents/comparisons/claude-code-vs-codex)
provides useful independent context, but its coding-agent API requires Pro+
and its [data terms](https://artificialanalysiscdn.com/legal/ProDataPlatformTerms.pdf)
restrict redistribution and certain competing model-selection products. It is
not fetched or embedded in this implementation. A publicly visible benchmark is
not necessarily open for redistribution.

## Calculation

Profiles live in [scenario-profiles.json](../config/scenario-profiles.json).
The selected CLI catalog and the model-family/thinking-level policy filter exact
pairs before ranking. A worker must be at or below the selected lead's combined
level. If the live catalog is unavailable, results are marked unverified and
`--check` cannot validate them. A catalog listing does not prove entitlement.

For ModelDial, let `B`, `F` and `K` be its backend, frontend and knowledge
scores, and let `w` be the selected scenario's axis weights. We calculate
`Q = Σ wᵢ scoreᵢ`. Each axis's cost and duration are for the **whole published
axis evaluation**, not an ordinary task. `C` and `T` are the weighted geometric
means of those axis costs and durations: `C = exp(Σ wᵢ ln Cᵢ)` and likewise for
`T`. This makes a reproducible relative consumption proxy for the selected
mix; it does not estimate a subscription bill or a typical task's cost.

For desktop, `Q` is the equally weighted mean of the two task-set scores, while
`C = sqrt(C₁ C₂)` and `T = sqrt(T₁ T₂)`. The two component measurements and
run IDs are retained. For both sources, remove strictly dominated candidates
on `(Q, C, T)` and calculate:

```text
U = Q − a × log₂(C / 1 USD) − b × log₂(T / 10 minutes)
```

Choose the lowest-cost candidate within one utility point of the highest
utility, then break ties by duration, utility, model and effort. One point is an
explicit stability margin, not a confidence interval or a claim that results
are statistically equivalent. Rankings use unrounded inputs; displayed utility
is rounded to three decimals.

| Scenario | Quality weights B/F/K | Lead a/b | Worker a/b | Source |
| --- | --- | --- | --- | --- |
| Daily coding | 0.55/0.35/0.10 | 2/1 | 5/2 | ModelDial |
| Desktop automation | equal weights on both task sets | 4/2 | 5/2 | CUA-Speedrun |
| Hard terminal work | Terminal-Bench pass rate | 0.25/0.25 | 5/2 using daily B/F/K | Terminal-Bench lead; ModelDial worker |
| Verifiable background subtasks | 0.80/0.20/0 | 12/1 | 18/1.5 | ModelDial |

For the hard lead, the existing Terminal-Bench calculation removes only
confidence-dominated candidates, then applies the same utility form with its
actual per-trial cost and time. It chooses the lowest-cost candidate whose
utility interval overlaps that of the highest-utility candidate. These are
quality-only intervals, not paired tests and not intervals for cost or time.
The hard worker is selected independently from the ModelDial coding profile;
if that source is unavailable, a measured Terminal-Bench worker is used and
identified. Recommendations for other worker subtasks are independently ranked
within the selected lead's ceiling.

The high background cost coefficients intentionally describe **small, bounded
work whose result can be checked**. They do not establish that a low-scoring
model succeeds at unmeasured tasks. Its status records low confidence; the
worker choice is a trial, not a demonstrated success rate for simple tasks.
Verify the output and escalate ambiguous or failed work. The benchmarks do not measure multi-agent coordination, duplicate
exploration, retries, review, or subscription quota usage, and do not establish
that parallelism is beneficial.

## Reproducibility and updates

`agent-team recommendations status` includes the source snapshots, per-source
fetch/error state, model catalog and level policy, complete profile weights,
selected candidates, component data and scoring trace. The individual source
results, their dataset versions and ModelDial's publication identity are kept
separate. An unavailable source does not get a fabricated substitute. Exact
menu snapshots and recent generations preserve an existing selection across a
refresh; an unknown explicit ID fails instead of silently changing settings.

The optional timer checks every five minutes. Each source is refreshed
independently after 24 hours, with a five-minute retry after failure; a failure
retains that source's last successful snapshot. Publication time does not mean
all individual runs are new. Results use normal service tiers, and API-equivalent
cost is not Pro subscription usage. Calibrate recommendations on representative
local tasks, including verification, failed attempts and elapsed time.
