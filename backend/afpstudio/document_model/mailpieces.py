"""Candidate mailpiece boundaries are an attested customer rule, not BNG fact."""
from collections import Counter

from .structure import build_structure_index


def build_mailpiece_index(document, rule=None):
    result = {"items": [], "count": None, "complete": False, "status": "unresolved",
              "basis": "unknown", "actual_mailpieces": None, "warnings": []}
    if not rule:
        result["warnings"].append("No customer boundary rule: page groups are not assumed to be mailpieces.")
        return result
    structure = build_structure_index(document.path, document.index)
    result["basis"] = "calculated_from_operator_attested_boundary_rule"
    result["rule_evidence"] = dict(rule)
    groups = [item for item in structure["page_groups"] if item["name"] == rule["page_group_name"]]
    covered = 0
    for group in sorted(groups, key=lambda item: item["offset"]):
        first, last = group["first_page"], group["last_page"]
        if first is None or last is None or first != covered + 1 or last < first:
            result["warnings"].append("Rule produced empty, overlapping or incomplete page coverage.")
            return result
        pages = document.preflight.pages[first - 1:last]
        usage = Counter(page.medium_map or "unresolved" for page in pages)
        result["items"].append({"candidate_number": len(result["items"]) + 1,
            "document_id": group["document_id"], "offset": group["offset"],
            "first_page": first, "last_page": last, "page_count": last - first + 1,
            "medium_map_usage": dict(usage), "basis": result["basis"]})
        covered = last
    if not structure["complete"] or not document.parse_complete or covered != document.index.page_count or not groups:
        result["warnings"].append("Index/rule does not completely cover all parsed pages.")
        return result
    result.update(count=len(result["items"]), complete=True, status="calculated")
    return result
