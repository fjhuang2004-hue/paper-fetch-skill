"""Provider availability rule ownership for HTML full-text checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AvailabilityPolicy:
    """Provider-owned availability rules kept separate from cleanup policy."""

    name: str
    site_rule_overrides: Mapping[str, Any] = field(default_factory=dict)
    positive_signals: Callable[[str], tuple[list[str], list[str], list[str]]] | None = (
        None
    )
    blocking_fallback_signals: Callable[[str], list[str]] | None = None
    availability_overrides: Callable[..., tuple[list[str], list[str], list[str]]] | None = (
        None
    )
    access_block_text_tokens: tuple[str, ...] = ()
