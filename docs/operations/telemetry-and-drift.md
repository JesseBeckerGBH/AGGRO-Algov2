# Stage 6 — telemetry and drift detection

"You need this before annealing, because annealing without measurement is
guessing." Stage 6 does the measuring; it never changes behaviour (that is
Stage 7). Code: `src/research_os/telemetry.py`.

## Failure log

Every run, `detect_failures()` classifies problems into **exactly one** of the
six classes from `adaptation-rules.yaml` and writes them to the
`failure_events` table with the taxonomy's severity.

| Class | Detected from | Signal |
|---|---|---|
| `connector_failure` | a connector raised, or the whole pool was empty | `<exception>` / `empty_result_set` |
| `coverage_failure` | no primary-class result in the surfaced set | `no_primary_source_found` |
| `novelty_failure` | one domain is > 50% of a surfaced set of ≥ 4 | `repeat_domain_dominance` |
| `ranking_failure` | a `low_trust` / `blocked` result in the top 3 | `low_value_item_ranked_top_three` |
| `briefing_failure` | body exceeds the `ceiling_words` lever by > 15% | `too_long` |
| `identity_failure` | — not detectable in this architecture (no browser identity layer); noted, not logged |

Connector failures are non-fatal: a dead connector is logged and the run
continues on the others.

    research-os failures --mission M.yaml

## Drift check

`check_drift()` compares four mission-level metrics to the
`drift_detection` thresholds. The share metrics are measured over the
**surfaced set of the last 3 briefings** (stored on the `briefings` row), not
over everything retrieved — 30% primary is a statement about output quality,
not about the open web.

| Metric | Threshold | Source |
|---|---|---|
| novelty yield | `>= 0.20` | avg `briefings.novelty_yield`, last 3 |
| domain concentration | `<= 0.35` | top share in `mission_domains` |
| primary share | `>= 0.30` | avg `briefings.primary_share`, last 3 |
| disconfirming share | `>= 0.10` | avg `briefings.disconfirming_share`, last 3 |

Each check is written to `drift_checks`. `run` prints `drift: clean` or
`DRIFT: <breaches>`; the standalone command exits non-zero on a breach:

    research-os drift --mission M.yaml

On the example mission a fresh run is clean (novelty 100%, concentration 12%,
primary 100%, disconfirming 50%). A warm re-run breaches `novelty_yield` (0%)
— the intended "this mission surfaced nothing new" signal.

`adaptation-rules.yaml` `on_breach` (raise alert → force query-family
regeneration → require operator acknowledgement) is **Stage 7**. Stage 6 only
raises the alert.

## Standing finding

`novelty_failure: repeat_domain_dominance` fires most runs — arXiv is ~5/8 of
the surfaced set. Class-first ranking plus arXiv being the densest primary
source concentrates the briefing. Stage 7's lowest-risk lever
(`domain_weight_adjustment`, step 0.05) is aimed exactly at this.
