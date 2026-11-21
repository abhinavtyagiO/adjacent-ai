"""Content-free accounting for generated fields through validation and index changes."""

from collections import Counter

from app.schemas import BIP


class EvidenceAudit:
    """Count original generated leaves, never anchors or backend-derived labels.

    Aliases track relocations (plans to tiers, scoped claims, offering insights). Each
    original field is counted once even when arrays are pruned or paths are remapped.
    Only paths and fixed reason codes are held in memory; reports/logs contain counts.
    """

    def __init__(self, paths):
        self.generated = set(paths)
        self.accepted = set()
        self.reasons = {}
        self.aliases = {path: {path} for path in self.generated}
        self.retained = {}
        self.ignored_evidence_entries = 0

    def alias(self, original: str, target: str):
        self.aliases.setdefault(target, set()).update(self.aliases.get(original, set()))

    def reject(self, prefix: str, reason: str):
        for path, originals in self.aliases.items():
            if path == prefix or path.startswith(prefix + "/"):
                for original in originals:
                    self.reasons.setdefault(original, reason)

    def accept(self, path: str, retained_path: str):
        originals = self.aliases.get(path, set())
        self.accepted.update(originals)
        self.retained[retained_path] = originals

    def discard(self, retained_path: str, reason: str):
        for original in self.retained.get(retained_path, set()):
            self.accepted.discard(original)
            self.reasons[original] = reason

    @staticmethod
    def section(path):
        root = path.split("/")[1]
        return "offerings" if root == "offering_insights" else root

    def report(self):
        rejected = self.generated - self.accepted
        reasons = {path: self.reasons.get(path, "NOT_RETAINED") for path in rejected}
        sections = {}
        for section in BIP.model_fields:
            fields = {p for p in self.generated if self.section(p) == section}
            sections[section] = {
                "generated": len(fields),
                "accepted": len(fields & self.accepted),
                "rejected": len(fields & rejected),
                "rejection_reasons": dict(Counter(reasons[p] for p in fields & rejected)),
            }
        return {
            "available": True,
            "generated": len(self.generated),
            "accepted": len(self.accepted),
            "rejected": len(rejected),
            "acceptance_rate": len(self.accepted) / len(self.generated) if self.generated else None,
            "rejection_reasons": dict(Counter(reasons.values())),
            "by_section": sections,
            "ignored_evidence_entries": self.ignored_evidence_entries,
        }


def unavailable_evidence():
    """A provider failure is unknown output, not a measured empty valid response."""
    return {
        "available": False,
        "generated": None,
        "accepted": None,
        "rejected": None,
        "acceptance_rate": None,
        "rejection_reasons": {},
        "by_section": {},
        "ignored_evidence_entries": None,
    }
