# Stage 7 — the self-annealing loop

Code: `src/research_os/adaptation.py`. The full cycle from `adaptation-rules.yaml`:

    detect → classify → log        (Stage 6)
    propose → gate → apply         (this stage, each `run`)
    observe → decide → version     (this stage, `evaluate()` each `run`)

## Where changes live

The loop **never edits `configs/*.yaml`** — those are doctrine, versioned in
git. It writes bounded, reversible deltas to the `policy_versions` table; the
runtime collapses the active ones into an **overlay** applied on top of the
base config (`Memory.active_overlay`). Every applied change is also appended to
`telemetry/adaptation-log.jsonl` (gitignored — `ethics.telemetry_is_local`).

## Levers

| Lever | Auto? | Trigger | Effect |
|---|---|---|---|
| `connector_routing` | **auto** | `connector_failure` ×2 on one connector | that connector sorted last; skipped after 3 consecutive demotions |
| `briefing_length_target` | **auto** | `briefing_failure` ×3 | word target steps down 50 (floor 150), fed to the briefing prompt |
| `domain_weight_adjustment` | operator | `novelty_failure`+`ranking_failure` ×3 | `-0.05` multiplier on the dominant domain's score, within its class bucket |
| `query_family_regeneration` | operator | coverage/novelty stalled ×2 | forces the `adjacent_field` + `disconfirming` angles and rotates the vocabulary seed |
| `class_weight_adjustment` | operator | `ranking_failure` ×8 | `-0.02` on a class weight (deliberately near-impossible to trigger) |

Only the two lowest-risk levers auto-apply. Everything that touches class or
domain weights is recorded `pending_operator` and does nothing until:

    research-os adapt --mission M.yaml                       # list
    research-os adapt --mission M.yaml --approve 2           # activate
    research-os adapt --mission M.yaml --reject 1 --rollback 4

## Gate

Every proposal passes through `gate()` before apply:

- **escalation** — 3 consecutive rollbacks on a lever → refuse, operator must intervene
- **cooldown** — `cooldown_days` since the last change on that lever
- **bounds** — `max_consecutive_demotions` (3), `floor_words` (150),
  `max_cumulative_change` (0.30 domain / 0.10 class)
- **invariants** (checked first) — `primary` weight never < 0.70, `community`
  never > 0.55. Both are in practice unreachable through the bounded levers;
  the cumulative cap protects them first. Belt and suspenders.

`propose()` also skips any lever that already has a `pending_operator` row, so
proposals do not restack every run.

## Observe / decide

`evaluate()` runs each `run`. For every active **auto** version with a full
`window_missions` (5) window of briefings behind it, it compares outcome
quality after the change to the window before:

    quality = 0.40·novelty_yield + 0.35·primary_share + 0.25·disconfirming_share
              (averaged over the window's briefings)

`held or improved` → keep. `declined` → `rolled_back`, logged. Rolled-back /
superseded versions are pruned beyond the last 20 (`rollback_retention_versions`).

## no_silent_changes

The next briefing's footer carries the active overlay in one line, e.g.

    _retrieved 80 · surfaced 8 · novel domains 0 · adaptation applied: arxiv.org -0.05_

## Verified end to end

3 brave runs → `repeat_domain_dominance` ×3 → one `domain_weight_adjustment`
proposal (no restacking) → `adapt --approve 2` → next run reranks with
`arxiv.org -0.05` and says so in the footer. Unit tests cover propose (auto +
operator), gate (cooldown, primary-floor invariant), apply + overlay, and
evaluate's rollback path.

## Next

Stage 8 (learning loop) — the second product hidden in the first. Still no
scheduler (Stage 5) until this loop has run enough real missions to trust.
