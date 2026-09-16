"""Persistent research memory (SQLite).

Without memory the system cannot tell discovery from repetition. Schema is the
Stage-2 subset of docs/memory/sqlite-schema.md: missions, queries, results,
briefings. failure_events / adaptation_events arrive with Stage 6-7.

Novelty is decided here, before rerank: a result is `seen_before` if its
content hash or canonical URL is already stored; its domain is `novel_domain`
if this mission has never stored that domain.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Briefing, Mission, Query, Result

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    mission_id      TEXT PRIMARY KEY,
    objective       TEXT NOT NULL,
    success_condition TEXT NOT NULL,
    novelty_requirement TEXT,
    first_run_at    TEXT,
    last_run_at     TEXT
);
CREATE TABLE IF NOT EXISTS queries (
    query_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id   TEXT NOT NULL REFERENCES missions(mission_id),
    angle        TEXT,
    query_text   TEXT NOT NULL,
    executed_at  TEXT,
    connector    TEXT,
    result_count INTEGER
);
CREATE TABLE IF NOT EXISTS results (
    result_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id     TEXT NOT NULL REFERENCES missions(mission_id),
    query_id       INTEGER REFERENCES queries(query_id),
    connector      TEXT,
    title          TEXT,
    url            TEXT,
    canonical_url  TEXT,
    domain         TEXT,
    content_hash   TEXT,
    source_class   TEXT,
    class_signal   TEXT,
    final_score    REAL,
    retrieved_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_results_hash ON results(content_hash);
CREATE INDEX IF NOT EXISTS ix_results_canon ON results(canonical_url);
CREATE INDEX IF NOT EXISTS ix_results_mission_domain ON results(mission_id, domain);
CREATE TABLE IF NOT EXISTS briefings (
    briefing_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      TEXT NOT NULL REFERENCES missions(mission_id),
    generated_at    TEXT,
    body            TEXT,
    retrieved       INTEGER,
    surfaced        INTEGER,
    novel_domains   INTEGER,
    novelty_yield   REAL,
    primary_share   REAL,   -- of the surfaced set (drift: primary_share_floor)
    disconfirming_share REAL, -- of the surfaced set (drift: disconfirming_share_floor)
    usefulness_rating TEXT
);
-- Stage 4: per-mission domain concentration.
CREATE TABLE IF NOT EXISTS mission_domains (
    mission_id  TEXT NOT NULL REFERENCES missions(mission_id),
    domain      TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    PRIMARY KEY (mission_id, domain)
);
-- Stage 4: promoted-vocabulary store. Terms harvested from primary-class
-- results; status starts 'candidate' and only the operator moves it to
-- 'promoted' (query-families.yaml: operator_confirms_before_promotion).
CREATE TABLE IF NOT EXISTS promoted_vocab (
    mission_id       TEXT NOT NULL REFERENCES missions(mission_id),
    term             TEXT NOT NULL,
    distinct_sources INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'candidate',  -- candidate | promoted | rejected
    first_seen       TEXT,
    decided_at       TEXT,
    PRIMARY KEY (mission_id, term)
);
-- Stage 6: structured failure log. Every detected failure is classified into
-- exactly one of the six classes in adaptation-rules.yaml.
CREATE TABLE IF NOT EXISTS failure_events (
    failure_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      TEXT NOT NULL REFERENCES missions(mission_id),
    detected_at     TEXT,
    failure_class   TEXT NOT NULL,   -- connector_failure | ranking_failure | novelty_failure
                                     -- | coverage_failure | identity_failure | briefing_failure
    signal          TEXT,            -- which signal from the taxonomy fired
    severity        TEXT,            -- low | medium | high | critical
    detail          TEXT,
    run_at          TEXT             -- groups failures from one run
);
CREATE INDEX IF NOT EXISTS ix_failure_mission ON failure_events(mission_id, detected_at);
-- Stage 6: drift check history (the weekly cadence check).
CREATE TABLE IF NOT EXISTS drift_checks (
    check_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      TEXT NOT NULL REFERENCES missions(mission_id),
    checked_at      TEXT,
    novelty_yield   REAL,
    domain_concentration REAL,
    primary_share   REAL,
    disconfirming_share  REAL,
    breaches        TEXT             -- comma list of breached thresholds, '' if clean
);
-- Stage 7: versioned policy changes (the self-annealing overlay).
-- The loop never edits configs/*.yaml (doctrine, versioned in git). It records
-- bounded, reversible deltas here; the runtime applies the active ones on top
-- of the base config.
CREATE TABLE IF NOT EXISTS policy_versions (
    version       INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id    TEXT NOT NULL REFERENCES missions(mission_id),
    created_at    TEXT,
    lever         TEXT NOT NULL,     -- connector_routing | briefing_length_target
                                     -- | domain_weight_adjustment | class_weight_adjustment
                                     -- | query_family_regeneration
    trigger       TEXT,              -- failure class + evidence count that justified it
    change_json   TEXT NOT NULL,     -- the delta, as JSON
    status        TEXT NOT NULL DEFAULT 'active',
                                     -- active | pending_operator | rolled_back | superseded
    evidence_window TEXT,            -- 'from..to' run timestamps
    decided_at    TEXT,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_policy_mission ON policy_versions(mission_id, status);
"""

# generic words that are never worth promoting as vocabulary
_VOCAB_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "into",
    "using", "based", "model", "models", "data", "results", "paper", "study",
    "match", "matches", "player", "players", "tennis", "prediction", "predictive",
    "features", "feature", "outcome", "outcomes", "analysis", "approach", "method",
    "methods", "new", "how", "why", "are", "was", "were", "has", "have", "can",
    "which", "their", "our", "its", "such", "also", "more", "most", "than", "then",
    "learning", "machine", "neural", "network", "networks", "training", "test",
    "dataset", "datasets", "accuracy", "performance", "research", "work", "used",
    "different", "various", "propose", "proposed", "show", "shows", "result",
    # function words + prose glue that bigram harvesting otherwise captures
    "they", "them", "when", "where", "while", "about", "into", "over", "under",
    "moreover", "furthermore", "however", "thus", "hence", "here", "there",
    "developed", "extracted", "including", "generated", "suggest", "suggests",
    "identifying", "identify", "surpassing", "improve", "improved", "improvement",
    "alone", "well", "both", "each", "some", "many", "will", "would", "could",
    "one", "two", "three", "first", "second", "may", "might", "must", "been",
    "get", "got", "make", "made", "use", "uses", "via", "per", "not",
}
# bigrams that clear the threshold but carry no domain-specific signal
_VOCAB_STOP_PHRASES = {
    "machine learning", "deep learning", "logistic regression", "linear regression",
    "random forest", "gradient boosting", "cross validation", "feature selection",
    "feature engineering", "data set", "test set", "training set", "related work",
    "state art", "high accuracy", "prediction model", "predictive model",
}


class Memory:
    def __init__(self, path: str | Path = "research-memory.sqlite"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- mission -----------------------------------------------------------
    def upsert_mission(self, m: Mission, now: str) -> None:
        cur = self.conn.execute(
            "SELECT mission_id FROM missions WHERE mission_id = ?", (m.id,)
        )
        if cur.fetchone():
            self.conn.execute(
                "UPDATE missions SET last_run_at = ? WHERE mission_id = ?", (now, m.id)
            )
        else:
            self.conn.execute(
                "INSERT INTO missions (mission_id, objective, success_condition, "
                "novelty_requirement, first_run_at, last_run_at) VALUES (?,?,?,?,?,?)",
                (m.id, m.objective, m.success_condition, m.novelty_requirement, now, now),
            )
        self.conn.commit()

    # -- novelty ---------------------------------------------------------
    def mark_novelty(self, mission_id: str, results: list[Result]) -> list[Result]:
        priors = {
            row["domain"]: row["hits"]
            for row in self.conn.execute(
                "SELECT domain, hits FROM mission_domains WHERE mission_id = ?",
                (mission_id,),
            )
        }
        for r in results:
            row = self.conn.execute(
                "SELECT 1 FROM results WHERE content_hash = ? OR canonical_url = ? LIMIT 1",
                (r.content_hash, r.canonical_url),
            ).fetchone()
            r.seen_before = row is not None
            r.domain_prior_hits = priors.get(r.domain, 0)
            r.novel_domain = r.domain_prior_hits == 0
        return results

    def domain_shares(self, mission_id: str) -> tuple[dict[str, float], float]:
        rows = list(self.conn.execute(
            "SELECT domain, hits FROM mission_domains WHERE mission_id = ?", (mission_id,)
        ))
        total = sum(row["hits"] for row in rows)
        if not total:
            return {}, 0.0
        shares = {row["domain"]: row["hits"] / total for row in rows}
        return shares, max(shares.values())

    def record_domain_hits(self, mission_id: str, results: list[Result], now: str) -> None:
        counts: dict[str, int] = {}
        for r in results:
            if r.domain:
                counts[r.domain] = counts.get(r.domain, 0) + 1
        for domain, n in counts.items():
            self.conn.execute(
                "INSERT INTO mission_domains (mission_id, domain, hits, first_seen, last_seen) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(mission_id, domain) DO UPDATE SET "
                "hits = hits + excluded.hits, last_seen = excluded.last_seen",
                (mission_id, domain, n, now, now),
            )
        self.conn.commit()

    # -- promoted vocabulary -------------------------------------------
    def harvest_vocab(self, mission_id: str, results: list[Result],
                      operator_terms: list[str], now: str, *, threshold: int = 2,
                      cap: int = 10) -> list[str]:
        """Harvest candidate domain terms from the TITLES of primary-class
        results returned by the primary_source_hunt angle (query-families.yaml:
        vocabulary_escalation). A bigram appearing in `threshold`+ distinct
        primary sources becomes a 'candidate' — never auto-promoted. At most
        `cap` new candidates per run, highest source-count first."""
        import re

        have = {t.lower() for t in operator_terms}
        pool = [r for r in results if r.source_class == "primary"]

        # term -> set of distinct primary SOURCES (documents) it appeared in.
        # Titles only: they are short and term-dense; snippet prose floods the
        # harvest with glue bigrams ("they extracted", "moreover gao").
        seen: dict[str, set[str]] = {}
        for r in pool:
            source_id = r.canonical_url or r.url or r.content_hash
            title = re.sub(r"[·|–—-]\s*github.*$", "", r.title.lower())
            words = re.findall(r"[a-z][a-z0-9+-]{2,}", title)
            for a, b in zip(words, words[1:]):
                if a in _VOCAB_STOP or b in _VOCAB_STOP or len(a) < 3 or len(b) < 3:
                    continue
                term = f"{a} {b}"
                if term in have or term in _VOCAB_STOP_PHRASES:
                    continue
                seen.setdefault(term, set()).add(source_id)

        ranked = sorted(
            ((t, s) for t, s in seen.items() if len(s) >= threshold),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )[:cap]

        new_candidates: list[str] = []
        for term, sources in ranked:
            row = self.conn.execute(
                "SELECT status, distinct_sources FROM promoted_vocab "
                "WHERE mission_id = ? AND term = ?", (mission_id, term),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO promoted_vocab (mission_id, term, distinct_sources, "
                    "status, first_seen) VALUES (?,?,?,'candidate',?)",
                    (mission_id, term, len(sources), now),
                )
                new_candidates.append(term)
            elif row["status"] == "candidate":
                self.conn.execute(
                    "UPDATE promoted_vocab SET distinct_sources = MAX(distinct_sources, ?) "
                    "WHERE mission_id = ? AND term = ?", (len(sources), mission_id, term),
                )
        self.conn.commit()
        return new_candidates

    def list_vocab(self, mission_id: str, status: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT term, distinct_sources, status, first_seen, decided_at "
               "FROM promoted_vocab WHERE mission_id = ?")
        args: list = [mission_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY distinct_sources DESC, term"
        return list(self.conn.execute(sql, args))

    def set_vocab_status(self, mission_id: str, term: str, status: str, now: str) -> bool:
        cur = self.conn.execute(
            "UPDATE promoted_vocab SET status = ?, decided_at = ? "
            "WHERE mission_id = ? AND term = ?", (status, now, mission_id, term.lower()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def promoted_terms(self, mission_id: str) -> list[str]:
        return [row["term"] for row in self.conn.execute(
            "SELECT term FROM promoted_vocab WHERE mission_id = ? AND status = 'promoted' "
            "ORDER BY term", (mission_id,),
        )]

    # -- Stage 6: telemetry ------------------------------------------------
    def record_failures(self, mission_id: str, events: list[dict], run_at: str) -> int:
        if not events:
            return 0
        self.conn.executemany(
            "INSERT INTO failure_events (mission_id, detected_at, failure_class, "
            "signal, severity, detail, run_at) VALUES (?,?,?,?,?,?,?)",
            [
                (mission_id, run_at, e["failure_class"], e.get("signal", ""),
                 e.get("severity", ""), e.get("detail", ""), run_at)
                for e in events
            ],
        )
        self.conn.commit()
        return len(events)

    def recent_failures(self, mission_id: str, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT detected_at, failure_class, signal, severity, detail "
            "FROM failure_events WHERE mission_id = ? ORDER BY failure_id DESC LIMIT ?",
            (mission_id, limit),
        ))

    def failure_counts(self, mission_id: str, since: str | None = None) -> dict[str, int]:
        sql = "SELECT failure_class, COUNT(*) n FROM failure_events WHERE mission_id = ?"
        args: list = [mission_id]
        if since:
            sql += " AND detected_at >= ?"
            args.append(since)
        sql += " GROUP BY failure_class"
        return {row["failure_class"]: row["n"] for row in self.conn.execute(sql, args)}

    def recent_novelty_yield(self, mission_id: str, n: int = 3) -> float:
        return self._recent_avg(mission_id, "novelty_yield", n)

    def recent_primary_share(self, mission_id: str, n: int = 3) -> float:
        return self._recent_avg(mission_id, "primary_share", n)

    def recent_disconfirming_share(self, mission_id: str, n: int = 3) -> float:
        return self._recent_avg(mission_id, "disconfirming_share", n)

    def record_drift_check(self, mission_id: str, checked_at: str, metrics: dict,
                           breaches: list[str]) -> int:
        cur = self.conn.execute(
            "INSERT INTO drift_checks (mission_id, checked_at, novelty_yield, "
            "domain_concentration, primary_share, disconfirming_share, breaches) "
            "VALUES (?,?,?,?,?,?,?)",
            (mission_id, checked_at, metrics.get("novelty_yield"),
             metrics.get("domain_concentration"), metrics.get("primary_share"),
             metrics.get("disconfirming_share"), ",".join(breaches)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def drift_history(self, mission_id: str, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT checked_at, novelty_yield, domain_concentration, primary_share, "
            "disconfirming_share, breaches FROM drift_checks WHERE mission_id = ? "
            "ORDER BY check_id DESC LIMIT ?", (mission_id, limit),
        ))

    # -- Stage 7: policy overlay ----------------------------------------
    def add_policy_version(self, mission_id: str, created_at: str, lever: str,
                           trigger: str, change: dict, *, status: str = "active",
                           evidence_window: str = "", note: str = "") -> int:
        import json
        cur = self.conn.execute(
            "INSERT INTO policy_versions (mission_id, created_at, lever, trigger, "
            "change_json, status, evidence_window, note) VALUES (?,?,?,?,?,?,?,?)",
            (mission_id, created_at, lever, trigger, json.dumps(change),
             status, evidence_window, note),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _policy_rows(self, mission_id: str, status: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM policy_versions WHERE mission_id = ?"
        args: list = [mission_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY version"
        return list(self.conn.execute(sql, args))

    def active_overlay(self, mission_id: str) -> dict:
        """Collapse all active policy versions into one overlay dict:
            {connector_demotions: {name: n}, briefing_word_target: int|None,
             domain_weight_delta: {domain: float}, class_weight_delta: {cls: float}}
        Later versions win for scalar levers; deltas accumulate."""
        import json
        overlay: dict = {
            "connector_demotions": {}, "briefing_word_target": None,
            "domain_weight_delta": {}, "class_weight_delta": {},
            "query_regen": False, "versions": [],
        }
        for row in self._policy_rows(mission_id, status="active"):
            change = json.loads(row["change_json"])
            overlay["versions"].append(row["version"])
            if row["lever"] == "query_family_regeneration":
                overlay["query_regen"] = True
            if row["lever"] == "connector_routing":
                for name, n in change.get("demote", {}).items():
                    overlay["connector_demotions"][name] = (
                        overlay["connector_demotions"].get(name, 0) + n
                    )
            elif row["lever"] == "briefing_length_target":
                overlay["briefing_word_target"] = change.get("word_target")
            elif row["lever"] == "domain_weight_adjustment":
                for d, delta in change.get("delta", {}).items():
                    overlay["domain_weight_delta"][d] = (
                        overlay["domain_weight_delta"].get(d, 0.0) + delta
                    )
            elif row["lever"] == "class_weight_adjustment":
                for c, delta in change.get("delta", {}).items():
                    overlay["class_weight_delta"][c] = (
                        overlay["class_weight_delta"].get(c, 0.0) + delta
                    )
        return overlay

    def last_change_for_lever(self, mission_id: str, lever: str) -> sqlite3.Row | None:
        rows = list(self.conn.execute(
            "SELECT * FROM policy_versions WHERE mission_id = ? AND lever = ? "
            "AND status IN ('active','rolled_back') ORDER BY version DESC LIMIT 1",
            (mission_id, lever),
        ))
        return rows[0] if rows else None

    def cumulative_delta_for_lever(self, mission_id: str, lever: str) -> float:
        """Sum of absolute magnitude of active deltas for a bounded lever."""
        import json
        total = 0.0
        for row in self._policy_rows(mission_id, status="active"):
            if row["lever"] != lever:
                continue
            for v in json.loads(row["change_json"]).get("delta", {}).values():
                total += abs(v)
        return round(total, 4)

    def consecutive_rollbacks(self, mission_id: str, lever: str) -> int:
        n = 0
        for row in reversed(self._policy_rows(mission_id)):
            if row["lever"] != lever:
                continue
            if row["status"] == "rolled_back":
                n += 1
            else:
                break
        return n

    def set_policy_status(self, version: int, status: str, now: str, note: str = "") -> bool:
        cur = self.conn.execute(
            "UPDATE policy_versions SET status = ?, decided_at = ?, "
            "note = COALESCE(NULLIF(?, ''), note) WHERE version = ?",
            (status, now, note, version),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_policy(self, mission_id: str) -> list[sqlite3.Row]:
        return self._policy_rows(mission_id)

    def prune_policy(self, mission_id: str, keep: int = 20) -> int:
        rows = self._policy_rows(mission_id)
        stale = [r["version"] for r in rows if r["status"] in ("rolled_back", "superseded")]
        drop = stale[:-keep] if len(stale) > keep else []
        for v in drop:
            self.conn.execute("DELETE FROM policy_versions WHERE version = ?", (v,))
        self.conn.commit()
        return len(drop)

    # -- writes --------------------------------------------------------
    def record_query(self, mission_id: str, q: Query, connector: str,
                     executed_at: str, count: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO queries (mission_id, angle, query_text, executed_at, "
            "connector, result_count) VALUES (?,?,?,?,?,?)",
            (mission_id, q.angle, q.text, executed_at, connector, count),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_results(self, mission_id: str, query_id: int, results: list[Result]) -> None:
        self.conn.executemany(
            "INSERT INTO results (mission_id, query_id, connector, title, url, "
            "canonical_url, domain, content_hash, source_class, class_signal, "
            "final_score, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (mission_id, query_id, r.connector, r.title, r.url, r.canonical_url,
                 r.domain, r.content_hash, r.source_class, r.class_signal,
                 r.final_score, r.retrieved_at)
                for r in results
            ],
        )
        self.conn.commit()

    def record_briefing(self, b: Briefing, novelty_yield: float = 0.0,
                        primary_share: float = 0.0,
                        disconfirming_share: float = 0.0) -> int:
        cur = self.conn.execute(
            "INSERT INTO briefings (mission_id, generated_at, body, retrieved, "
            "surfaced, novel_domains, novelty_yield, primary_share, disconfirming_share) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (b.mission_id, b.generated_at, b.body, b.retrieved, b.surfaced,
             b.novel_domains, novelty_yield, primary_share, disconfirming_share),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _recent_avg(self, mission_id: str, column: str, n: int) -> float:
        rows = list(self.conn.execute(
            f"SELECT {column} v FROM briefings WHERE mission_id = ? "
            f"ORDER BY briefing_id DESC LIMIT ?", (mission_id, n),
        ))
        vals = [r["v"] for r in rows if r["v"] is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0
