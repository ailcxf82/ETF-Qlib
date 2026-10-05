"""Deterministic local cross-session index for safe research cards."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from etf_ml.errors import ConfigurationError, ETFError, IntegrityError, QualityError
from etf_ml.utils import atomic_json, content_hash, ensure_within, file_hash


CARD_KEYS = {"schema_version", "factor_id", "formula", "mechanism", "required_fields", "lookback",
             "research_group", "hypothesis", "reason", "status", "reasons", "context_hash",
             "snapshot_id", "protocol_id", "baseline_id", "comparable", "card_hash"}
CARD_KEYS.update({"feedback_summary", "committed_order", "trial_id", "definition_id", "evaluation_id",
                  "evaluation_snapshot_id", "evaluation_protocol_id", "evaluation_baseline_id",
                  "resulting_baseline_id", "resulting_protocol_id", "attempt_outcome", "economic_status",
                  "failure_category", "qualification", "reuse_of_or_null", "changed_dimension_or_null",
                  "retest_reason_or_null", "dedup_decision", "feedback_summary_rebuilt",
                  "evaluation_evidence_hashes", "evaluation_evidence_verified",
                  "evaluation_evidence_reason"})
CARD_KEYS.update({"reuse_package_id", "reuse_context_id", "evaluation_attempted_trials"})
FORBIDDEN_MARKERS = ("holdout", "api_key", "password", "secret", "authorization")


def _safe_card(card: dict) -> dict:
    safe = {key: card[key] for key in CARD_KEYS if key in card}
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if any(marker in encoded.lower() for marker in FORBIDDEN_MARKERS):
        raise QualityError("Research card contains disallowed sensitive or path content")
    return safe


def _memory_feedback_summary(payload: dict, card: dict) -> tuple[dict, bool]:
    """Upgrade legacy summaries in the derived index without rewriting committed trials."""
    summary = card.get("feedback_summary")
    if isinstance(summary, dict) and summary.get("schema_version") == "feedback-summary-v3":
        return summary, False
    feedback = payload.get("feedback") or {}
    if (feedback.get("stage") != "factor_selection" or
            feedback.get("protocol_id") != card.get("evaluation_protocol_id") or
            not card.get("factor_id")):
        return summary if isinstance(summary, dict) else {}, False
    from etf_ml.adapters.rdagent.feedback import feedback_summary
    rebuilt = feedback_summary(feedback)
    candidates = rebuilt.get("by_candidate") or []
    if not any(row.get("factor_id") == card.get("factor_id") for row in candidates):
        return summary if isinstance(summary, dict) else {}, False
    return rebuilt, True


def _evaluation_evidence_hash_projection(card: dict, allowed_root: Path, *, verified=None) -> list[dict]:
    """Expose artifact names and hashes to prompts without disclosing local paths."""
    if verified is None:
        verified, _ = ResearchMemoryIndex._evaluation_evidence_status(card, allowed_root)
    if not verified:
        return []
    return [{"name": Path(item["path"]).name, "sha256": item["sha256"]}
            for item in card.get("evaluation_evidence", [])]


class ResearchMemoryIndex:
    """Index only checkpoint-committed cards; no LLM or numeric ranking."""

    def __init__(self, root: Path, *, reuse_root: Path | None = None):
        self.root = Path(root).resolve()
        self.path = self.root / "research_memory_index.json"
        self.sources_path = self.root / "sources.json"
        from etf_ml.research.reuse_store import ReuseStore
        default_root = self.root.parent.parent if self.root.name == "v2" else self.root.parent
        self.reuse_store = ReuseStore(reuse_root or default_root / "reuse")

    def register_source(self, source: Path, *, allowed_root: Path) -> None:
        """Register an explicit research root; never discover arbitrary artifacts."""
        source, allowed_root = Path(source).resolve(), Path(allowed_root).resolve()
        self.active_source = (source, allowed_root)
        if not source.is_relative_to(allowed_root):
            raise IntegrityError("Research-memory source is outside its artifact root")
        source.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": "research-memory-sources-v2", "sources": []}
        if self.sources_path.exists():
            payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("sources"), list):
            raise IntegrityError("Research-memory source manifest is invalid")
        value = {"root": str(source), "allowed_root": str(allowed_root)}
        values = [item if isinstance(item, dict) else {"root": item, "allowed_root": item}
                  for item in payload["sources"]]
        if value not in values:
            values.append(value)
            values.sort(key=lambda item: item["root"])
            payload["sources"] = values
            atomic_json(self.sources_path, payload)

    def _sources(self) -> list[tuple[Path, Path]]:
        if not self.sources_path.exists():
            # Read-only compatibility for pre-v2 local indexes.  v2 callers
            # always use the explicit source manifest and therefore never
            # broaden discovery merely by changing their memory root.
            if self.root.name == "v2":
                return []
            return [(path, path) for path in [self.root] + [item for item in sorted(self.root.iterdir())
                                  if item.is_dir() and (item / "sessions").is_dir()]]
        payload = json.loads(self.sources_path.read_text(encoding="utf-8"))
        values = payload.get("sources")
        if not isinstance(values, list):
            raise IntegrityError("Research-memory source manifest is invalid")
        result = []
        for item in values:
            item = {"root": item, "allowed_root": item} if isinstance(item, str) else item
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("root", "allowed_root")):
                raise IntegrityError("Research-memory source manifest is invalid")
            result.append((Path(item["root"]).resolve(), Path(item["allowed_root"]).resolve()))
        return result

    @staticmethod
    def _evaluation_evidence_status(card: dict, allowed_root: Path, *, reuse_root=None) -> tuple[bool, str]:
        """Verify a paired evaluation inside the registered artifact root."""
        try:
            evidence = card.get("evaluation_evidence")
            if not isinstance(evidence, list) or not evidence:
                return False, "missing_evaluation_evidence"
            paths = []
            for item in evidence:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                    return False, "invalid_evaluation_evidence"
                path = ensure_within(Path(item["path"]), allowed_root)
                if not path.is_file():
                    return False, "evaluation_evidence_missing"
                if file_hash(path) != item["sha256"]:
                    return False, "evaluation_evidence_hash_mismatch"
                paths.append(path)
            named = {path.name: path for path in paths}
            if not {"evaluation.json", "paired_report.json"}.issubset(named):
                return False, "missing_paired_evaluation_report"
            reports = {path.stem.removesuffix("_report"): json.loads(path.read_text(encoding="utf-8"))
                       for path in paths if path.name.endswith("_report.json") and path.name != "paired_report.json"}
            if not {"baseline", "candidate"}.issubset(reports):
                return False, "missing_baseline_or_candidate_report"
            paired = json.loads(named["paired_report.json"].read_text(encoding="utf-8"))
            evaluation = json.loads(named["evaluation.json"].read_text(encoding="utf-8"))
            if (paired.get("status") != evaluation.get("status") or
                    paired.get("protocol_id") != card.get("evaluation_protocol_id") or
                    evaluation.get("protocol_id") != card.get("evaluation_protocol_id") or
                    paired.get("status") != card.get("economic_status")):
                return False, "evaluation_identity_mismatch"
            if any(report.get("protocol_id") != card.get("evaluation_protocol_id") or
                   report.get("snapshot_id") != card.get("evaluation_snapshot_id")
                   for report in reports.values()):
                return False, "report_identity_mismatch"
            from etf_ml.research.paired import _verify_references
            # Paired reports have a local evaluation parent even when baseline
            # children now reside in an independent shared package.
            output_root = named["paired_report.json"].parent.parent.parent
            _verify_references(reports, output_root, Path(allowed_root) / "models",
                               reuse_root=reuse_root or Path(allowed_root) / "reuse")
        except ConfigurationError:
            return False, "reference_outside_allowed_root"
        except IntegrityError:
            return False, "referenced_artifact_integrity_failure"
        except (ETFError, KeyError, IndexError, OSError, TypeError, ValueError):
            return False, "reference_verification_failed"
        return True, "verified"

    @staticmethod
    def _verify_evaluation(card: dict, allowed_root: Path) -> bool:
        """Compatibility boolean for callers that only need reuse eligibility."""
        return ResearchMemoryIndex._evaluation_evidence_status(card, allowed_root)[0]

    def rebuild(self) -> dict:
        cards, evidence, statuses = [], [], []
        compact_index = self.reuse_store.index()
        compact = {}
        for pid, entry in compact_index["packages"].items():
            payload, _ = self.reuse_store.load(pid)
            key = (entry["source_key"], entry["trial_key"])
            if key in compact and compact[key]["sha256"] != entry["trial_sha256"]:
                raise IntegrityError("Conflicting compact trial receipts")
            compact[key] = {"card": {**payload["card"], "reuse_package_id": pid}, "sha256": entry["trial_sha256"]}
        roots = self._sources()
        seen = set()
        for research_root, allowed_root in roots:
            sessions = research_root / "sessions"
            if not sessions.is_dir() or sessions.resolve() in seen:
                continue
            seen.add(sessions.resolve())
            for checkpoint in sorted(sessions.glob("*/checkpoint.json")):
                session_root = checkpoint.parent
                state = json.loads(checkpoint.read_text(encoding="utf-8"))
                committed = state.get("trials")
                if not isinstance(committed, list):
                    raise IntegrityError("Research checkpoint has invalid committed trial list")
                session_order = int(state.get("created_at_ns", checkpoint.stat().st_mtime_ns))
                for order, item in enumerate(committed):
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                        raise IntegrityError("Research checkpoint trial entry is invalid")
                    trial = ensure_within(session_root / item["path"], session_root)
                    if not trial.is_file() or file_hash(trial) != item["sha256"]:
                        raise IntegrityError("Committed research trial evidence changed")
                    compact_key = (content_hash(str(research_root)), trial.relative_to(research_root).as_posix())
                    if compact_key in compact:
                        if compact[compact_key]["sha256"] != item["sha256"]:
                            raise IntegrityError("Compact trial receipt differs from checkpoint")
                        continue
                    payload = json.loads(trial.read_text(encoding="utf-8"))
                    feedback = payload.get("feedback", {})
                    statuses.append(str(feedback.get("status", payload.get("result", {}).get("status", "unknown"))))
                    source_id = content_hash(str(research_root))[:16]
                    artifact_id = source_id + ":" + trial.relative_to(research_root).as_posix()
                    evidence.append({"artifact_id": artifact_id, "sha256": item["sha256"]})
                    card = payload.get("research_card")
                    if not isinstance(card, dict):
                        continue
                    summary, rebuilt = _memory_feedback_summary(payload, card)
                    safe = _safe_card({**card, "feedback_summary": summary,
                                       "feedback_summary_rebuilt": rebuilt})
                    verified, reason = self._evaluation_evidence_status(card, allowed_root)
                    safe["evaluation_evidence_verified"] = verified
                    safe["evaluation_evidence_reason"] = reason
                    safe["evaluation_evidence_hashes"] = _evaluation_evidence_hash_projection(
                        card, allowed_root, verified=verified)
                    safe["committed_order"] = [session_order, order]
                    safe["evidence_id"] = content_hash({"artifact_id": artifact_id, "hash": item["sha256"]})
                    cards.append(safe)
        for item in compact.values():
            card = item["card"]
            cards.append(card)
            statuses.append(str(card.get("status", "unknown")))
            evidence.append({"artifact_id": card["evidence_id"], "sha256": item["sha256"]})
        cards.sort(key=lambda row: (str(row.get("protocol_id", "")), str(row.get("factor_id", "")),
                                    str(row.get("evidence_id", ""))))
        ledger = {"trial_count": len(statuses), "card_count": len(cards), "by_status": dict(sorted(Counter(statuses).items())),
                  "proposal_count": sum(bool(card.get("formula")) for card in cards),
                  "schema_version": "search-ledger-v1"}
        result = {"schema_version": "research-memory-index-v2", "cards": cards,
                  "evidence": evidence, "ledger": ledger,
                  "reuse_index_identity": compact_index.get("identity"),
                  "sources_hash": file_hash(self.sources_path) if self.sources_path.exists() else None,
                  "identity": content_hash({"cards": cards, "evidence": evidence})}
        atomic_json(self.path, result)
        return result

    def load(self) -> dict:
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("identity") != content_hash({"cards": value.get("cards"), "evidence": value.get("evidence")}):
                raise IntegrityError("Research-memory index changed")
            if (all(card.get("reuse_package_id") for card in value["cards"])
                    and value.get("reuse_index_identity") == self.reuse_store.index().get("identity")
                    and value.get("sources_hash") == (file_hash(self.sources_path) if self.sources_path.exists() else None)):
                return value
        return self.rebuild()

    def query(self, *, snapshot_id: str, protocol_id: str, baseline_id: str,
              fields: list[str] | None = None, research_group: str | None = None,
              reuse_context_id: str | None = None) -> list[dict]:
        fields = set(fields or [])
        result = []
        for card in self.load()["cards"]:
            same_protocol = (card.get("protocol_id") == protocol_id or
                             bool(reuse_context_id and card.get("reuse_context_id") == reuse_context_id))
            same_snapshot = card.get("snapshot_id") == snapshot_id
            same_baseline = card.get("baseline_id") == baseline_id
            overlap = len(fields.intersection(card.get("required_fields", [])))
            group_match = bool(research_group and card.get("research_group") == research_group)
            score = 8 * same_protocol + 4 * same_snapshot + 2 * same_baseline + overlap + group_match
            if not score:
                continue
            if card.get("reuse_package_id"):
                payload, _ = self.reuse_store.load(card["reuse_package_id"])
                if payload["card"] != {k: v for k, v in card.items() if k != "reuse_package_id"}:
                    raise IntegrityError("Cached research card differs from compact package")
            result.append({**card, "comparable": same_protocol and same_snapshot and same_baseline,
                           "match": {"protocol": same_protocol, "snapshot": same_snapshot,
                                     "baseline": same_baseline, "field_overlap": overlap,
                                     "research_group": group_match}, "_score": score})
        result.sort(key=lambda row: (-row["_score"], str(row.get("factor_id", "")), row["evidence_id"]))
        for row in result:
            row.pop("_score")
        return result
