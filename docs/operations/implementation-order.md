# Implementation Order

The governing principle: **establish judgment before automation.** If automation comes first, the system scales confusion. If source rules and identity control come first, the system scales discernment.

## Stage 0 — Repository and archive (complete when pushed)

- Repo initialized, structure in place, first commit pushed
- Skill file in `skills/` as the behavioral constitution
- Source thread link recorded in `archives/README.md`

Nothing here is intellectual work. It exists so the thinking stops living in a chat window.

## Stage 1 — Judgment made machine-readable (current)

- `configs/source-classes.yaml` — trust tiers, penalties, bonuses
- `configs/query-families.yaml` — missions and angle sets
- `configs/adaptation-rules.yaml` — bounds, invariants, failure taxonomy

These three files are the product. Everything downstream reads from them. Review them against your own instincts and edit before writing any code — disagreeing with a weight now costs a minute, later it costs a refactor.

## Stage 2 — One vertical slice

Pick a single real mission you actually care about and make it work end to end, ugly:

1. One search connector
2. URL canonicalization and dedupe
3. Source classification and reranking from `source-classes.yaml`
4. SQLite write
5. One briefing rendered through `prompts/briefing-agent.md`

Success test: the briefing tells you something you did not know, from a source you would not have found, in under 400 words.

Do not build five modules in parallel. Five half-finished modules produce no output you can evaluate.

## Stage 3 — Widen retrieval

Second and third connectors. Normalize into one result schema. Measure whether multi-engine collection actually raises novelty yield, or merely raises volume. If it only raises volume, the reranker is the problem, not the connector count.

## Stage 4 — Memory and novelty

Content hashing, seen-before detection, per-mission domain concentration tracking, promoted vocabulary store. Novelty scoring is meaningless before memory exists.

## Stage 5 — Scheduler

Small number of recurring missions. Cap the count deliberately — mission sprawl is how a research OS becomes another feed.

## Stage 6 — Telemetry and drift detection

Structured failure logs. Weekly drift check against the thresholds in `adaptation-rules.yaml`. You need this before annealing, because annealing without measurement is guessing.

## Stage 7 — Self-annealing

The full closed loop: detect, classify, log, propose, gate, apply, observe, decide, version. Start with the two lowest-risk levers only — connector routing and briefing length. Domain and class weights come last, under operator review.

## Stage 8 — Learning loop

Checkpoints, spaced revisits, contrast cases, trajectory movement reporting. This is the second product hidden inside the first, and it is what makes the category defensible.

## Stage 9 — Validation before selling

Five to ten design partners from one beachhead niche. Measure source quality, novelty yield, time to signal, and operator-rated decision usefulness against their normal search. You need this evidence before any acquisition spend.

## Stage 10 — Packaging

Diagnostic offer, implementation offer, recurring subscription. Priced against decisions improved, not links delivered.

## The trap

Skipping to Stage 5 because scheduling feels like progress. A scheduler pointed at unvalidated ranking logic produces a reliable stream of mediocre briefings, which is worse than nothing — it manufactures the feeling of an information advantage without the substance.
