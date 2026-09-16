"""CLI entry point.

    python -m research_os run   --mission missions/example-tennis-features.yaml
    python -m research_os run   --mission M.yaml --connector brave,marginalia --brief llm
    python -m research_os vocab --mission M.yaml                 # review harvested terms
    python -m research_os vocab --mission M.yaml --promote "serve rates" --reject "grand slam"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from .llm import LLMError, LLMKeyMissing
from .memory import Memory
from .pipeline import load_mission, run_mission

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # briefings use em dashes
    except (AttributeError, ValueError):
        pass


def _cmd_run(args: argparse.Namespace) -> int:
    mission = load_mission(args.mission)
    try:
        out = run_mission(
            mission,
            connector=args.connector,
            limit=args.limit,
            top=args.top,
            db_path=args.db,
            brief_mode=args.brief,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            adapt=not args.no_adapt,
        )
    except LLMKeyMissing as e:
        print(f"error: {e}\nhint: cp .env.example .env and add a key, or use "
              f"--brief render / --brief auto", file=sys.stderr)
        return 2
    except LLMError as e:
        print(f"error: LLM synthesis failed: {e}", file=sys.stderr)
        return 2
    print(out.briefing.body)
    print()

    surfaced_domains = {r.domain for r in out.reranked[: out.briefing.surfaced]}
    pool_domains = {r.domain for r in out.reranked}
    per_conn = ", ".join(f"{k}={v}" for k, v in (out.per_connector or {}).items())
    lines = [
        f"[{out.mission.id}] retrieved {out.retrieved} ({per_conn}) -> "
        f"{out.briefing.surfaced} surfaced",
        f"  domains: {len(surfaced_domains)} in briefing / {len(pool_domains)} in pool"
        f"  |  top-domain share (memory): {out.domain_top_share:.0%}",
        f"  novelty yield: {out.novelty_yield:.0%}"
        + ("   [below 20% floor]" if out.novelty_yield < 0.20 else ""),
        f"  memory: {args.db}",
    ]
    if out.new_vocab:
        shown = ", ".join(f'"{t}"' for t in out.new_vocab[:6])
        more = f" (+{len(out.new_vocab) - 6} more)" if len(out.new_vocab) > 6 else ""
        lines.append(f"  {len(out.new_vocab)} new vocab candidate(s): {shown}{more}")
        lines.append(f"  review: research-os vocab --mission {args.mission} --db {args.db}")

    if out.failures:
        by_class: dict[str, int] = {}
        for f in out.failures:
            by_class[f["failure_class"]] = by_class.get(f["failure_class"], 0) + 1
        lines.append("  failures: " + ", ".join(f"{k}×{v}" for k, v in by_class.items()))
    if out.drift and out.drift.breaches:
        lines.append("  DRIFT: " + "; ".join(f"{n} ({m})" for n, m in out.drift.breaches))
    elif out.drift:
        lines.append("  drift: clean")

    a = out.adaptation or {}
    for x in a.get("applied", []):
        lines.append(f"  adapt applied v{x['version']}: {x['lever']} — {x['rationale']}")
    for x in a.get("pending", []):
        lines.append(f"  adapt PENDING v{x['version']}: {x['lever']} — {x['rationale']}")
        lines.append(f"    approve: research-os adapt --mission {args.mission} "
                     f"--db {args.db} --approve {x['version']}")
    for x in a.get("decisions", []):
        if x["decision"] != "keep":
            lines.append(f"  adapt {x['decision']} v{x['version']}: {x['lever']} "
                         f"(q {x['q_before']}->{x['q_after']})")
    print("\n".join(lines), file=sys.stderr)
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    from .telemetry import check_drift
    mission = load_mission(args.mission)
    with Memory(args.db) as mem:
        rep = check_drift(mission.id, mem)
    print(f"drift check — {mission.id}")
    for k, v in rep.metrics.items():
        print(f"  {k:<22} {v:.0%}")
    if rep.clean:
        print("\nclean — no thresholds breached")
        return 0
    print("\nBREACHES:")
    for name, msg in rep.breaches:
        print(f"  {name}: {msg}")
    print("\nadaptation-rules.yaml on_breach: raise alert, force query-family "
          "regeneration on affected missions, require operator acknowledgement "
          "before the next scheduled run (Stage 7).")
    return 1


def _cmd_adapt(args: argparse.Namespace) -> int:
    import json
    from datetime import datetime, timezone
    from . import adaptation
    mission = load_mission(args.mission)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with Memory(args.db) as mem:
        for v in args.approve or []:
            ok = mem.set_policy_status(int(v), "active", now, "operator-approved")
            print(f"{'approved' if ok else 'not found'}: v{v}", file=sys.stderr)
        for v in args.reject or []:
            ok = mem.set_policy_status(int(v), "rolled_back", now, "operator-rejected")
            print(f"{'rejected' if ok else 'not found'}: v{v}", file=sys.stderr)
        for v in args.rollback or []:
            ok = mem.set_policy_status(int(v), "rolled_back", now, "operator-rollback")
            print(f"{'rolled back' if ok else 'not found'}: v{v}", file=sys.stderr)
        if args.run_cycle:
            res = adaptation.run_cycle(mission, mem, now)
            print(json.dumps(res, indent=2))

        rows = mem.list_policy(mission.id)
        if not rows:
            print("(no policy versions for this mission)")
            return 0
        print(f"{'VER':>4} {'STATUS':<16} {'LEVER':<26} TRIGGER / NOTE")
        for r in rows:
            print(f"{r['version']:>4} {r['status']:<16} {r['lever']:<26} "
                  f"{r['trigger']} — {r['note']}")
    return 0


def _cmd_failures(args: argparse.Namespace) -> int:
    mission = load_mission(args.mission)
    with Memory(args.db) as mem:
        rows = mem.recent_failures(mission.id, limit=args.limit)
    if not rows:
        print("(no failure events recorded for this mission)")
        return 0
    print(f"{'WHEN':<21} {'SEVERITY':<9} {'CLASS':<20} SIGNAL / DETAIL")
    for r in rows:
        print(f"{r['detected_at']:<21} {r['severity']:<9} {r['failure_class']:<20} "
              f"{r['signal']} — {r['detail']}")
    return 0


def _cmd_vocab(args: argparse.Namespace) -> int:
    mission = load_mission(args.mission)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with Memory(args.db) as mem:
        for term in args.promote or []:
            ok = mem.set_vocab_status(mission.id, term, "promoted", now)
            print(f"{'promoted' if ok else 'not found'}: {term!r}", file=sys.stderr)
        for term in args.reject or []:
            ok = mem.set_vocab_status(mission.id, term, "rejected", now)
            print(f"{'rejected' if ok else 'not found'}: {term!r}", file=sys.stderr)

        rows = mem.list_vocab(mission.id, status=args.status)
        if not rows:
            print("(no vocabulary terms recorded for this mission yet)")
            return 0
        print(f"{'STATUS':<10} {'SRCS':>4}  TERM")
        for r in rows:
            print(f"{r['status']:<10} {r['distinct_sources']:>4}  {r['term']}")
        promoted = [r["term"] for r in rows if r["status"] == "promoted"]
        if promoted:
            print(f"\nfed as vocabulary_seed on the next run: {', '.join(promoted)}",
                  file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="research-os", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one mission end to end")
    run.add_argument("--mission", required=True, help="path to a mission YAML")
    run.add_argument("--connector", default="fixture",
                     help="one or a comma-list: fixture (offline), brave, marginalia "
                          "— e.g. --connector brave,marginalia")
    run.add_argument("--limit", type=int, default=10, help="results per query")
    run.add_argument("--top", type=int, default=6, help="results surfaced in the briefing")
    run.add_argument("--db", default="research-memory.sqlite", help="SQLite memory path")
    run.add_argument("--brief", choices=("render", "llm", "auto"), default="render",
                     help="render = deterministic (default); llm = LLM synthesis; "
                          "auto = llm if a key is configured, else render")
    run.add_argument("--llm-provider", default=None,
                     help="gemini | anthropic | openai (else $LLM_PROVIDER, else gemini)")
    run.add_argument("--llm-model", default=None, help="override the provider default model")
    run.add_argument("--no-adapt", action="store_true",
                     help="skip the Stage 7 self-annealing cycle this run")
    run.set_defaults(func=_cmd_run)

    voc = sub.add_parser("vocab", help="review / promote harvested vocabulary")
    voc.add_argument("--mission", required=True, help="path to the mission YAML")
    voc.add_argument("--db", default="research-memory.sqlite", help="SQLite memory path")
    voc.add_argument("--status", choices=("candidate", "promoted", "rejected"),
                     default=None, help="filter the listing")
    voc.add_argument("--promote", action="append", metavar="TERM",
                     help="mark a term promoted (repeatable) — fed as vocabulary_seed next run")
    voc.add_argument("--reject", action="append", metavar="TERM",
                     help="mark a term rejected (repeatable)")
    voc.set_defaults(func=_cmd_vocab)

    dft = sub.add_parser("drift", help="run the drift check against adaptation-rules.yaml")
    dft.add_argument("--mission", required=True, help="path to the mission YAML")
    dft.add_argument("--db", default="research-memory.sqlite", help="SQLite memory path")
    dft.set_defaults(func=_cmd_drift)

    fail = sub.add_parser("failures", help="list recent logged failure events")
    fail.add_argument("--mission", required=True, help="path to the mission YAML")
    fail.add_argument("--db", default="research-memory.sqlite", help="SQLite memory path")
    fail.add_argument("--limit", type=int, default=20)
    fail.set_defaults(func=_cmd_failures)

    adp = sub.add_parser("adapt", help="review / approve / roll back policy versions")
    adp.add_argument("--mission", required=True, help="path to the mission YAML")
    adp.add_argument("--db", default="research-memory.sqlite", help="SQLite memory path")
    adp.add_argument("--approve", action="append", metavar="VER",
                     help="activate a pending_operator policy version (repeatable)")
    adp.add_argument("--reject", action="append", metavar="VER",
                     help="reject a pending policy version (repeatable)")
    adp.add_argument("--rollback", action="append", metavar="VER",
                     help="roll back an active policy version (repeatable)")
    adp.add_argument("--run-cycle", action="store_true",
                     help="run propose->gate->apply->evaluate now (also runs each `run`)")
    adp.set_defaults(func=_cmd_adapt)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
