"""Operator defined media rules.

The printer profile answers "what does this medium map mean on the
device". The rules answer the questions the *job* imposes: letterhead on
page one only, plain stock everywhere else, unknown maps are a failure.

Rules are intentionally small and declarative so an operator can write
them in the Settings tab without touching code. Three scopes exist:

``medium_map``
    Applies to every page whose effective medium map matches. The
    special match value ``__unmapped__`` selects pages where no medium
    map could be resolved at all.

``media_transition``
    Applies to each change of medium map between two pages.

``document``
    Applies once to the whole job.

Every violation carries the page it belongs to, so the UI can jump
straight to the offending page and structured field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from afpstudio.printer_profiles.schema import (
    DOCUMENT_CONSTRAINTS,
    KNOWN_CONSTRAINTS,
    MEDIA_TRANSITION_CONSTRAINTS,
    MEDIUM_MAP_CONSTRAINTS,
    UNMAPPED,
    Rule,
)

__all__ = [
    "RuleViolation",
    "Transition",
    "UNMAPPED",
    "evaluate_rules",
]


class RuleError(ValueError):
    """Raised when a rule cannot be evaluated because it is malformed."""


@dataclass
class RuleViolation:
    """One broken rule."""

    rule_id: str
    scope: str
    severity: str
    message: str
    page: int | None = None
    offset: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "scope": self.scope,
            "severity": self.severity,
            "message": self.message,
            "page": self.page,
            "offset": self.offset,
            "detail": self.detail,
        }


@dataclass
class Transition:
    """A change of medium map between two pages."""

    page_number: int
    from_map: str | None
    to_map: str | None
    offset: int | None = None


def _page_map(page: Any) -> str | None:
    """The effective medium map of a page, or ``None`` when unresolved."""
    medium_map = getattr(page, "medium_map", None)
    if medium_map:
        return medium_map
    requested = getattr(page, "requested", None)
    if requested is not None:
        return requested.medium_map or None
    return None


def _resolved_attr(page: Any, name: str) -> Any:
    resolved = getattr(page, "resolved", None)
    if resolved is None:
        return None
    return getattr(resolved, name, None)


def _is_unmapped(page: Any) -> bool:
    """Is this page's media unresolved, whatever the map is called?"""
    resolved = getattr(page, "resolved", None)
    if resolved is None:
        return True
    return getattr(resolved, "source", "none") == "none"


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleError(f"Constraint {label!r} must be a number.")
    return int(value)


def _check_constraint_keys(rule: Rule) -> None:
    known = KNOWN_CONSTRAINTS.get(rule.scope, set())
    unknown = set(rule.constraints) - known
    if unknown:
        raise RuleError(
            f"Rule {rule.id!r} has unknown constraints for scope "
            f"{rule.scope!r}: {sorted(unknown)}."
        )


def _matches_medium_map(rule: Rule, page: Any) -> bool:
    wanted = rule.match.get("medium_map")
    if wanted is None:
        return True
    if wanted == UNMAPPED:
        return _is_unmapped(page)
    return _page_map(page) == wanted


def _evaluate_medium_map_rule(
    rule: Rule, pages: Sequence[Any]
) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    matched = [p for p in pages if _matches_medium_map(rule, p)]
    constraints = rule.constraints

    # ``match: {"medium_map": "__unmapped__"}`` with no constraints is
    # the common "unknown media must fail" rule. Matching is itself the
    # violation here; an empty constraint set must not mean "never fire".
    if not constraints and rule.match.get("medium_map") == UNMAPPED:
        for page in matched:
            violations.append(
                RuleViolation(
                    rule_id=rule.id,
                    scope=rule.scope,
                    severity=rule.severity,
                    message=(
                        f"Page {page.page_number} has no medium map that the "
                        "printer profile can resolve, but the rule requires "
                        "every page to be mapped."
                    ),
                    page=page.page_number,
                    offset=getattr(page, "offset", None),
                    detail={"constraint": "mapped"},
                )
            )
        return violations

    min_page = constraints.get("min_page_number")
    max_page = constraints.get("max_page_number")
    if min_page is not None or max_page is not None:
        low = _as_int(min_page, "min_page_number") if min_page is not None else None
        high = _as_int(max_page, "max_page_number") if max_page is not None else None
        for page in matched:
            number = page.page_number
            if low is not None and number < low:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {number} uses medium map "
                            f"{_page_map(page)!r} but the rule allows it only "
 f"from page {low} onwards."
                        ),
                        page=number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "min_page_number"},
                    )
                )
            if high is not None and number > high:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {number} uses medium map "
                            f"{_page_map(page)!r} but the rule allows it only "
                            f"up to page {high}."
                        ),
                        page=number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "max_page_number"},
                    )
                )

    stock = constraints.get("stock_name")
    allowed = constraints.get("allowed_stock_names")
    if stock is not None or allowed is not None:
        wanted = {stock.lower()} if stock is not None else set()
        if allowed is not None:
            if not isinstance(allowed, list) or not all(
                isinstance(item, str) for item in allowed
            ):
                raise RuleError(
                    f"Rule {rule.id!r} 'allowed_stock_names' must be a list of text."
                )
            wanted.update(item.lower() for item in allowed)
        for page in matched:
            actual = _resolved_attr(page, "stock_name")
            if actual is None:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} has no resolved paper stock, "
                            "so the rule about which stock may be used cannot be "
            "checked."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "stock_name"},
                    )
                )
            elif actual.lower() not in wanted:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} resolves to stock "
                            f"{actual!r}, which the rule does not allow "
                            f"({sorted(wanted)})."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={
                            "constraint": "stock_name",
                            "actual": actual,
                            "expected": sorted(wanted),
                        },
                    )
                )

    tray = constraints.get("tray")
    if tray is not None:
        for page in matched:
            actual = _resolved_attr(page, "tray")
            if actual is None:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} has no resolved tray, "
                            f"but the rule requires {tray!r}."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "tray"},
                    )
                )
            elif actual != tray:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} resolves to tray "
                            f"{actual!r} but the rule requires {tray!r}."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "tray", "actual": actual},
                    )
                )

    duplex = constraints.get("duplex")
    require_duplex = constraints.get("require_duplex")
    if duplex is not None or require_duplex:
        for page in matched:
            actual = _resolved_attr(page, "duplex")
            if duplex is not None and actual != duplex:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} resolves to duplex "
                            f"{actual!r} but the rule requires {duplex!r}."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "duplex", "actual": actual},
                    )
                )
            if require_duplex and actual in (None, "unknown"):
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} has no determinable duplex "
                            "setting but the rule requires one."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "require_duplex"},
                    )
                )

    min_pages = constraints.get("min_pages")
    max_pages = constraints.get("max_pages")
    if min_pages is not None and len(matched) < _as_int(min_pages, "min_pages"):
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                scope=rule.scope,
                severity=rule.severity,
                message=(
                    f"Medium map {rule.match.get('medium_map')!r} is used on "
                    f"{len(matched)} page(s) but the rule requires at least "
                    f"{int(min_pages)}."
                ),
                detail={"constraint": "min_pages", "actual": len(matched)},
            )
        )
    if max_pages is not None and len(matched) > _as_int(max_pages, "max_pages"):
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                scope=rule.scope,
                severity=rule.severity,
                message=(
                    f"Medium map {rule.match.get('medium_map')!r} is used on "
                    f"{len(matched)} page(s) but the rule allows at most "
                    f"{int(max_pages)}."
                ),
                detail={"constraint": "max_pages", "actual": len(matched)},
            )
        )

    return violations


def _evaluate_transition_rule(
    rule: Rule, transitions: Sequence[Transition]
) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    wanted_from = rule.match.get("from_medium_map")
    wanted_to = rule.match.get("to_medium_map")
    matched = [
        t
        for t in transitions
        if (wanted_from is None or t.from_map == wanted_from)
        and (wanted_to is None or t.to_map == wanted_to)
    ]
    allowed_pages = rule.constraints.get("allowed_page_numbers")
    if allowed_pages is not None:
        if not isinstance(allowed_pages, list):
            raise RuleError(
                f"Rule {rule.id!r} 'allowed_page_numbers' must be a list."
            )
        permitted = {int(item) for item in allowed_pages}
        for transition in matched:
            if transition.page_number not in permitted:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Media change to {transition.to_map!r} on page "
                            f"{transition.page_number} is not one of the allowed "
 f"changes."
                        ),
                        page=transition.page_number,
                        offset=transition.offset,
                        detail={"constraint": "allowed_page_numbers"},
                    )
                )
    max_occurrences = rule.constraints.get("max_occurrences")
    min_occurrences = rule.constraints.get("min_occurrences")
    if max_occurrences is not None and len(matched) > _as_int(
        max_occurrences, "max_occurrences"
    ):
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                scope=rule.scope,
                severity=rule.severity,
                message=(
                    f"Media change {wanted_from!r} -> {wanted_to!r} happens "
                    f"{len(matched)} time(s) but the rule allows at most "
                    f"{int(max_occurrences)}."
                ),
                detail={
                    "constraint": "max_occurrences",
                    "actual": len(matched),
                },
            )
        )
    if min_occurrences is not None and len(matched) < _as_int(
        min_occurrences, "min_occurrences"
    ):
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                scope=rule.scope,
                severity=rule.severity,
                message=(
                    f"Media change {wanted_from!r} -> {wanted_to!r} happens "
                    f"{len(matched)} time(s) but the rule requires at least "
                    f"{int(min_occurrences)}."
                ),
                detail={
                    "constraint": "min_occurrences",
                    "actual": len(matched),
                },
            )
        )
    return violations


def _evaluate_document_rule(
    rule: Rule, pages: Sequence[Any], transitions: Sequence[Transition]
) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    constraints = rule.constraints
    maps = [m for m in (_page_map(p) for p in pages) if m]
    distinct = sorted(set(maps))
    wanted = rule.match.get("medium_map")

    max_distinct = constraints.get("max_distinct_medium_maps")
    if max_distinct is not None and len(distinct) > _as_int(
        max_distinct, "max_distinct_medium_maps"
    ):
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                scope=rule.scope,
                severity=rule.severity,
                message=(
                    f"The job uses {len(distinct)} different medium maps "
                    f"({', '.join(distinct)}) but the rule allows at most "
                    f"{int(max_distinct)}."
                ),
                detail={
                    "constraint": "max_distinct_medium_maps",
                    "actual": distinct,
                },
            )
        )
    min_distinct = constraints.get("min_distinct_medium_maps")
    if min_distinct is not None and len(distinct) < _as_int(
        min_distinct, "min_distinct_medium_maps"
    ):
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                scope=rule.scope,
                severity=rule.severity,
                message=(
                    f"The job uses {len(distinct)} different medium maps "
                    f"({', '.join(distinct) or 'none'}) but the rule requires "
                    f"at least {int(min_distinct)}."
                ),
                detail={
                    "constraint": "min_distinct_medium_maps",
                    "actual": distinct,
                },
            )
        )

    if wanted is not None:
        count = sum(1 for m in maps if m == wanted)
        max_pages = constraints.get("max_pages_for_medium_map")
        min_pages = constraints.get("min_pages_for_medium_map")
        if max_pages is not None and count > _as_int(
            max_pages, "max_pages_for_medium_map"
        ):
            violations.append(
                RuleViolation(
                    rule_id=rule.id,
                    scope=rule.scope,
                    severity=rule.severity,
                    message=(
                        f"Medium map {wanted!r} is used on {count} page(s) but "
                        f"the rule allows at most {int(max_pages)}."
                    ),
                    detail={"constraint": "max_pages_for_medium_map"},
                )
            )
        if min_pages is not None and count < _as_int(
            min_pages, "min_pages_for_medium_map"
        ):
            violations.append(
                RuleViolation(
                    rule_id=rule.id,
                    scope=rule.scope,
                    severity=rule.severity,
                    message=(
                        f"Medium map {wanted!r} is used on {count} page(s) but "
                        f"the rule requires at least {int(min_pages)}."
                    ),
                    detail={"constraint": "min_pages_for_medium_map"},
                )
            )

    if constraints.get("require_all_pages_mapped"):
        for page in pages:
            if _page_map(page) is None:
                violations.append(
                    RuleViolation(
                        rule_id=rule.id,
                        scope=rule.scope,
                        severity=rule.severity,
                        message=(
                            f"Page {page.page_number} has no medium map but the "
                            "rule requires every page to be mapped."
                        ),
                        page=page.page_number,
                        offset=getattr(page, "offset", None),
                        detail={"constraint": "require_all_pages_mapped"},
                    )
                )

    if not transitions and rule.match.get("from_medium_map") is None:
        # Nothing to report; a transition rule with no transition is fine.
        pass
    return violations


def evaluate_rules(
    rules: Iterable[Rule],
    pages: Sequence[Any],
    transitions: Sequence[Transition],
) -> list[RuleViolation]:
    """Evaluate every rule against the analysed job."""
    violations: list[RuleViolation] = []
    for rule in rules:
        _check_constraint_keys(rule)
        if rule.severity == "ignore":
            continue
        if rule.scope == "medium_map":
            violations.extend(_evaluate_medium_map_rule(rule, pages))
        elif rule.scope == "media_transition":
            violations.extend(_evaluate_transition_rule(rule, transitions))
        elif rule.scope == "document":
            violations.extend(_evaluate_document_rule(rule, pages, transitions))
        else:
            raise RuleError(f"Unknown rule scope {rule.scope!r}.")
    return violations
