# Lite sweep (13 to 20 November 2026): what the tool traces say

The Lite sweep ran every arm and model on the 17 SREGym-Lite problems, three attempts each (1,512 attempts; 1,494 scored). The tables and the Tool use and Gaps tabs are computed from the same traces; these notes record the readings that need a human sentence.

## Headline
- Every arm with kubectl diagnosed 88 to 100% of attempts for every model. The ClickStack-only arms went from 49 to 82% at tier 0 to 78 to 98% at tier 3; the step from tier 0 to tier 1 (object snapshots and changes) is the largest single gain for every model.
- Fable 5.2 at tier 3 (98%) and gpt-6.1-sol at tier 3 (94%) matched their own kubectl arms. Opus 5.5 stayed below its kubectl arm at every ClickStack-only tier.
- With kubectl present the agents barely used ClickStack: under one ClickStack call per attempt for Opus, a handful for Fable and Codex. The kubectl + ClickStack arm therefore measures "does the extra tool hurt", not "does it help"; it did not hurt.

## Why the ClickStack-only tier 3 attempts failed (22 rejected attempts, read one by one)
- 6 stopped at the first plausible cause: the agent found a real symptom (a crash loop, a failing probe, an OOM kill) and submitted it without asking what caused it. The tier 4 investigation rules were written against this reading.
- 8 named the victim rather than the origin: the pod that failed instead of the webhook, quota, selector or network policy that made it fail. The data for the origin was in the object snapshots and changes but the agent did not look across kinds.
- 2 needed a live probe: the answer came from running a command inside the cluster (an in-pod DNS lookup, a connection attempt) that no telemetry stream records.
- 5 were judge-definition misses: the diagnosis described the right failure in different words or at a different level than the fault specification, and the checklist scored it below 0.70.
- 1 produced no cause at all.

## Saturation
- Under the 95% / 67% definition and all arms together, only namespace_memory_limit is saturated. Restricted to the kubectl arms, 13 to 14 of the 17 problems are saturated; restricted to Opus 5.5 across all arms, 5 are. The telemetry-only arms are what keep the Lite set discriminating.

## Data caveats
- The managed ClickStack MCP server renamed one tool and added another on 18 November; a strict preflight failed eight Codex pairs at launch and they were re-run. Each attempt's detail row names the server version it saw.
- edge_request_filter_cpu_saturation's injection check is flaky, so some attempts could not be deployed (yellow deploy marks); the lite-sweep-fill campaign re-ran them.
