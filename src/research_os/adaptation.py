"""Stage 7 — the self-annealing closed loop.

detect / classify / log are Stage 6. This module does the rest of the cycle
from adaptation-rules.yaml:

    propose  -> candidate remediations, least-destructive first
    gate     -> invariants, bounds, evidence count, cooldown
    apply    -> the least-destructive remediation that passes
    observe  -> an evaluation window of `window_missions` runs
    decide   -> keep, or roll back
    version  -> monotonic policy version, diff retained

Only the two lowest-risk levers auto-apply: `connector_routing` and
`briefing_length_target`. Anything touching source-class or domain weights is
recorded `pending_operator` (requires_operator_review) and does nothing until
`research-os adapt --approve <version>`.

Every applied change is appended to telemetry/adaptation-log.jsonl and shows
in the next briefing footer (no_silent_changes).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import adaptation_rules, class_weight, repo_root
from .models import Mission

_AUTO_LEVERS = {"connector_routing", "briefing_length_target"}
_DEFAULT_WORD_TARGET = 400  # briefing-agent.md body target


@dataclass
class Proposal:
    lever: str
    change: dict
    trigger: str
    auto: bool
    rationale: str


# --------------------------------------------------------------------------
# propose
# --------------------------------------------------------------------------

def _rules():
    r = adaptation_rules()
    return r.get("levers", {}), r.get("evaluation", {}), r.get("failure_classes", {})


def propose(mission: Mission, mem, *, window: int = 10) -> list[Proposal]:
    levers, _, _ = _rules()
    counts = _windowed_failure_counts(mem, mission.id, window)
    conn_fails = _windowed_connector_failures(mem, mission.id, window)
    # Don't restack a lever that already has an open instance. domain/class
    # weight adjustments are the exception: they are meant to accrue in small
    # bounded steps over time (gate() enforces cooldown + max_cumulative_change
    # on those), so a second one is legitimate once the cooldown has passed.
    # The other three levers are one-shot/flag-like with no cooldown of their
    # own — without this they restack indefinitely once evidence exists.
    _CUMULATIVE = {"domain_weight_adjustment", "class_weight_adjustment"}
    open_levers = {
        r["lever"] for r in mem.list_policy(mission.id)
        if r["status"] in ("pending_operator", "active")
        and r["lever"] not in _CUMULATIVE
    }
    out: list[Proposal] = []

    def add(p: Proposal) -> None:
        if p.lever not in open_levers:
            out.append(p)

    # 1. connector_routing (auto) — a specific connector keeps failing
    L = levers.get("connector_routing", {})
    need = int(L.get("evidence_required", 2))
    for name, n in conn_fails.items():
        if n >= need:
            add(Proposal(
                "connector_routing", {"demote": {name: 1}},
                f"connector_failure x{n} on {name}", auto=True,
                rationale=f"{name} failed {n} times in the last {window} runs; "
                          f"deprioritise and retry with backoff",
            ))

    # 2. briefing_length_target (auto) — briefings keep coming back too long
    L = levers.get("briefing_length_target", {})
    need = int(L.get("evidence_required", 3))
    if counts.get("briefing_failure", 0) >= need:
        step = int(L.get("step_words", 50))
        floor = int(L.get("floor_words", 150))
        cur = mem.active_overlay(mission.id).get("briefing_word_target") or _DEFAULT_WORD_TARGET
        target = max(floor, cur - step)
        if target < cur:
            add(Proposal(
                "briefing_length_target", {"word_target": target},
                f"briefing_failure x{counts['briefing_failure']}", auto=True,
                rationale=f"briefings ran long {counts['briefing_failure']}x; "
                          f"tighten target {cur} -> {target} words",
            ))

    # 3. domain_weight_adjustment (operator) — one domain dominates the surface
    L = levers.get("domain_weight_adjustment", {})
    need = int(L.get("evidence_required", 3))
    if counts.get("novelty_failure", 0) + counts.get("ranking_failure", 0) >= need:
        dom = _dominant_failure_domain(mem, mission.id, window)
        if dom:
            step = float(L.get("step", 0.05))
            add(Proposal(
                "domain_weight_adjustment", {"delta": {dom: -step}},
                f"repeat_domain_dominance x{counts.get('novelty_failure', 0)}", auto=False,
                rationale=f"{dom} keeps dominating the surfaced set; "
                          f"shave its weight by {step} (operator review)",
            ))

    # 4. query_family_regeneration (operator) — coverage / novelty stalled
    L = levers.get("query_family_regeneration", {})
    need = int(L.get("evidence_required", 2))
    if counts.get("coverage_failure", 0) + counts.get("novelty_failure", 0) >= need:
        add(Proposal(
            "query_family_regeneration",
            {"force_angles": ["adjacent_field", "disconfirming"], "reseed": True},
            f"coverage/novelty stalled x{counts.get('coverage_failure', 0) + counts.get('novelty_failure', 0)}",
            auto=False,
            rationale="regenerate the query family with a fresh vocabulary seed and "
                      "the two uncomfortable angles forced (operator review)",
        ))

    # 5. class_weight_adjustment (operator) — deliberately hard to trigger
    L = levers.get("class_weight_adjustment", {})
    need = int(L.get("evidence_required", 8))
    if counts.get("ranking_failure", 0) >= need:
        step = float(L.get("step", 0.02))
        add(Proposal(
            "class_weight_adjustment", {"delta": {"aggregator": -step}},
            f"ranking_failure x{counts['ranking_failure']}", auto=False,
            rationale=f"persistent ranking failures; nudge aggregator weight by "
                      f"-{step} (operator review — class weights are doctrine)",
        ))

    return out


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------

def gate(p: Proposal, mission_id: str, mem, *, now_days: float) -> tuple[bool, str]:
    levers, evaluation, _ = _rules()
    L = levers.get(p.lever, {})

    if mem.consecutive_rollbacks(mission_id, p.lever) >= 3:
        return False, f"escalated: 3 consecutive rollbacks on {p.lever} — operator must intervene"

    last = mem.last_change_for_lever(mission_id, p.lever)
    cooldown = float(L.get("cooldown_days", 0))
    if last and cooldown:
        # created_at is ISO; caller passes now_days as days-since-epoch for both
        from datetime import datetime, timezone
        age = now_days - (
            datetime.fromisoformat(last["created_at"]).timestamp() / 86400.0
        )
        if age < cooldown:
            return False, f"cooldown: {p.lever} changed {age:.1f}d ago, needs {cooldown}d"

    if p.lever == "connector_routing":
        name = next(iter(p.change["demote"]))
        cur = mem.active_overlay(mission_id)["connector_demotions"].get(name, 0)
        cap = int(L.get("max_consecutive_demotions", 3))
        if cur + 1 > cap:
            return False, f"bound: {name} already demoted {cur}x (cap {cap})"

    if p.lever == "briefing_length_target":
        floor = int(L.get("floor_words", 150))
        if p.change["word_target"] < floor:
            return False, f"bound: word_target {p.change['word_target']} below floor {floor}"

    if p.lever in ("domain_weight_adjustment", "class_weight_adjustment"):
        # invariants first — the hardest constraint (adaptation-rules.yaml)
        for tgt, delta in p.change["delta"].items():
            if p.lever == "class_weight_adjustment":
                new = (class_weight(tgt)
                       + mem.active_overlay(mission_id)["class_weight_delta"].get(tgt, 0.0)
                       + delta)
                if tgt == "primary" and new < 0.70:
                    return False, "invariant: primary class weight may not fall below 0.70"
                if tgt == "community" and new > 0.55:
                    return False, "invariant: community class weight may not rise above 0.55"
        maxc = float(L.get("max_cumulative_change", 0.30))
        proposed = sum(abs(v) for v in p.change["delta"].values())
        if mem.cumulative_delta_for_lever(mission_id, p.lever) + proposed > maxc + 1e-9:
            return False, f"bound: cumulative change would exceed {maxc}"

    return True, "ok"


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def apply(p: Proposal, mission_id: str, mem, now: str, evidence_window: str = "") -> int:
    status = "active" if (p.auto and p.lever in _AUTO_LEVERS) else "pending_operator"
    version = mem.add_policy_version(
        mission_id, now, p.lever, p.trigger, p.change,
        status=status, evidence_window=evidence_window, note=p.rationale,
    )
    _append_log({
        "ts": now, "mission": mission_id, "version": version, "lever": p.lever,
        "status": status, "trigger": p.trigger, "change": p.change,
        "rationale": p.rationale,
    })
    return version


# --------------------------------------------------------------------------
# observe + decide
# --------------------------------------------------------------------------

def _composite(rows) -> float:
    if not rows:
        return 0.0
    ny = sum((r["novelty_yield"] or 0) for r in rows) / len(rows)
    ps = sum((r["primary_share"] or 0) for r in rows) / len(rows)
    ds = sum((r["disconfirming_share"] or 0) for r in rows) / len(rows)
    return round(0.4 * ny + 0.35 * ps + 0.25 * ds, 4)


def evaluate(mission_id: str, mem, now: str) -> list[dict]:
    """For each active auto version with a full evaluation window behind it,
    keep if outcome quality held or improved, else roll back."""
    _, evaluation, _ = _rules()
    win = int(evaluation.get("window_missions", 5))
    decisions: list[dict] = []

    briefs = list(mem.conn.execute(
        "SELECT generated_at, novelty_yield, primary_share, disconfirming_share "
        "FROM briefings WHERE mission_id = ? ORDER BY briefing_id", (mission_id,),
    ))
    for row in mem.list_policy(mission_id):
        if row["status"] != "active" or row["lever"] not in _AUTO_LEVERS:
            continue
        after = [b for b in briefs if b["generated_at"] >= row["created_at"]]
        before = [b for b in briefs if b["generated_at"] < row["created_at"]]
        if len(after) < win:
            continue
        q_after = _composite(after[:win])
        q_before = _composite(before[-win:]) if before else q_after
        if q_after + 1e-6 >= q_before:
            decisions.append({"version": row["version"], "lever": row["lever"],
                              "decision": "keep", "q_before": q_before, "q_after": q_after})
        else:
            mem.set_policy_status(row["version"], "rolled_back", now,
                                  f"quality {q_before} -> {q_after} over {win} runs")
            _append_log({"ts": now, "mission": mission_id, "version": row["version"],
                         "lever": row["lever"], "status": "rolled_back",
                         "q_before": q_before, "q_after": q_after})
            decisions.append({"version": row["version"], "lever": row["lever"],
                              "decision": "rolled_back", "q_before": q_before, "q_after": q_after})
    mem.prune_policy(mission_id, keep=20)
    return decisions


# --------------------------------------------------------------------------
# one-call cycle used by `run`
# --------------------------------------------------------------------------

def run_cycle(mission: Mission, mem, now: str) -> dict:
    from datetime import datetime, timezone
    now_days = datetime.now(timezone.utc).timestamp() / 86400.0
    applied, blocked, pending = [], [], []
    for p in propose(mission, mem):
        ok, reason = gate(p, mission.id, mem, now_days=now_days)
        if not ok:
            blocked.append({"lever": p.lever, "reason": reason})
            continue
        v = apply(p, mission.id, mem, now)
        (pending if not (p.auto and p.lever in _AUTO_LEVERS) else applied).append(
            {"version": v, "lever": p.lever, "rationale": p.rationale}
        )
    decisions = evaluate(mission.id, mem, now)
    return {"applied": applied, "pending": pending, "blocked": blocked, "decisions": decisions}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _windowed_failure_counts(mem, mission_id: str, window: int) -> dict[str, int]:
    rows = list(mem.conn.execute(
        "SELECT failure_class, run_at FROM failure_events WHERE mission_id = ? "
        "ORDER BY failure_id DESC", (mission_id,),
    ))
    recent_runs = []
    for r in rows:
        if r["run_at"] not in recent_runs:
            recent_runs.append(r["run_at"])
        if len(recent_runs) > window:
            break
    keep = set(recent_runs[:window])
    out: dict[str, int] = {}
    for r in rows:
        if r["run_at"] in keep:
            out[r["failure_class"]] = out.get(r["failure_class"], 0) + 1
    return out


def _windowed_connector_failures(mem, mission_id: str, window: int) -> dict[str, int]:
    rows = list(mem.conn.execute(
        "SELECT detail, run_at FROM failure_events WHERE mission_id = ? "
        "AND failure_class = 'connector_failure' ORDER BY failure_id DESC LIMIT ?",
        (mission_id, window * 4),
    ))
    out: dict[str, int] = {}
    for r in rows:
        name = (r["detail"] or "").split(":", 1)[0].strip()
        if name and name != "no results from any connector":
            out[name] = out.get(name, 0) + 1
    return out


def _dominant_failure_domain(mem, mission_id: str, window: int) -> str | None:
    rows = list(mem.conn.execute(
        "SELECT detail FROM failure_events WHERE mission_id = ? "
        "AND signal = 'repeat_domain_dominance' ORDER BY failure_id DESC LIMIT ?",
        (mission_id, window),
    ))
    tally: dict[str, int] = {}
    for r in rows:
        # detail: "<domain> is n/m of the surfaced set"
        dom = (r["detail"] or "").split(" is ", 1)[0].strip()
        if dom:
            tally[dom] = tally.get(dom, 0) + 1
    return max(tally, key=tally.get) if tally else None


def _log_path() -> Path:
    p = repo_root() / "telemetry"
    p.mkdir(exist_ok=True)
    return p / "adaptation-log.jsonl"


def _append_log(entry: dict) -> None:
    with _log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
