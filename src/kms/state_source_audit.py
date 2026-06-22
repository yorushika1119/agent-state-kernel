"""State source switch audit for legacy state tables and task-first aliases."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StateSourceMapping:
    new_model: str
    legacy_source: str
    shadow_table: str
    can_switch_primary: bool
    blocking_reason: str
    safe_next_step: str


STATE_SOURCE_MAPPINGS = (
    StateSourceMapping(
        new_model="task_brief",
        legacy_source="intent_states + session.intent_version",
        shadow_table="task_brief_states",
        can_switch_primary=False,
        blocking_reason=(
            "dispatch creation and resume still use session.intent_version as the "
            "authoritative task_brief_version"
        ),
        safe_next_step=(
            "make dispatch creation read task_brief_states first, with "
            "session.intent_version as fallback"
        ),
    ),
    StateSourceMapping(
        new_model="task_flow",
        legacy_source="plan_states + progress_states + execution_actions",
        shadow_table="task_flows",
        can_switch_primary=False,
        blocking_reason=(
            "progress synthesis and blocking state still combine plan/progress "
            "legacy tables"
        ),
        safe_next_step=(
            "move task-local progress reads to task_flows before replacing "
            "plan_states reads"
        ),
    ),
    StateSourceMapping(
        new_model="claim",
        legacy_source="belief_items",
        shadow_table="claim_items",
        can_switch_primary=False,
        blocking_reason="risk building and verification still read belief_items",
        safe_next_step=(
            "switch task-scoped claim reads first, then keep belief_items only as "
            "debug compatibility"
        ),
    ),
    StateSourceMapping(
        new_model="todo",
        legacy_source="commitments",
        shadow_table="todo_obligations",
        can_switch_primary=False,
        blocking_reason=(
            "blocking reason, risks, and sync views still depend on commitments"
        ),
        safe_next_step=(
            "switch open todo/blocking reads to todo_obligations while keeping "
            "commitments as compatibility output"
        ),
    ),
)


class StateSourceAudit:
    """Reports whether task-first state tables are ready to become primary."""

    def __init__(self, mappings=STATE_SOURCE_MAPPINGS):
        self.mappings = tuple(mappings)

    def can_switch_all(self) -> bool:
        return all(mapping.can_switch_primary for mapping in self.mappings)

    def blocking_reasons(self) -> list[str]:
        return [
            f"{mapping.new_model}: {mapping.blocking_reason}"
            for mapping in self.mappings
            if not mapping.can_switch_primary
        ]

    def as_dict(self) -> dict:
        return {
            "can_switch_all": self.can_switch_all(),
            "mappings": [
                {
                    "new_model": mapping.new_model,
                    "legacy_source": mapping.legacy_source,
                    "shadow_table": mapping.shadow_table,
                    "can_switch_primary": mapping.can_switch_primary,
                    "blocking_reason": mapping.blocking_reason,
                    "safe_next_step": mapping.safe_next_step,
                }
                for mapping in self.mappings
            ],
            "blocking_reasons": self.blocking_reasons(),
        }
