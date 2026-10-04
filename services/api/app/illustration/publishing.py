"""Publication evidence shared by historical and current image pipelines."""
from collections.abc import Mapping


def publish_gate_passed(source) -> bool:
    gates = source.get("review_gates", {}) if isinstance(source, Mapping) else getattr(source, "review_gates", {})
    if not isinstance(gates, Mapping) or gates.get("machine") != "passed":
        return False
    # Presence of the new gate identifies a combined-review artifact. A failed
    # combined audit can never be overridden by unrelated historical fields.
    if "combined" in gates:
        return gates["combined"] == "passed"
    return gates.get("visual") == "passed" and gates.get("joint") == "passed"
