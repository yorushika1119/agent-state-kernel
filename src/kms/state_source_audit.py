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
        legacy_source="intent_states + session.intent_version compatibility",
        shadow_table="task_brief_states",
        can_switch_primary=True,
        blocking_reason="",
        safe_next_step=(
            "move reducer write ownership to task_brief_states and keep "
            "intent_states as compatibility output"
        ),
    ),
    StateSourceMapping(
        new_model="task_flow",
        legacy_source="plan_states compatibility + progress_states + execution_actions",
        shadow_table="task_flows",
        can_switch_primary=True,
        blocking_reason="",
        safe_next_step=(
            "move reducer write ownership to task_flows and keep plan_states as "
            "compatibility output"
        ),
    ),
    StateSourceMapping(
        new_model="claim",
        legacy_source="belief_items compatibility",
        shadow_table="claim_items",
        can_switch_primary=True,
        blocking_reason="",
        safe_next_step=(
            "move reducer write ownership to claim_items and keep belief_items "
            "as compatibility output"
        ),
    ),
    StateSourceMapping(
        new_model="todo",
        legacy_source="commitments compatibility",
        shadow_table="todo_obligations",
        can_switch_primary=True,
        blocking_reason="",
        safe_next_step=(
            "move reducer write ownership to todo_obligations and keep "
            "commitments as compatibility output"
        ),
    ),
)

REMAINING_COMPAT_GETTER_FILES = (
    "src/kernel/engine.py",
    "src/kms/pipeline.py",
    "src/stores/sqlite_store.py",
)


class StateSourceAudit:
    """Reports whether task-first state tables are ready to become primary."""

    def __init__(self, mappings=STATE_SOURCE_MAPPINGS):
        self.mappings = tuple(mappings)
        self.legacy_direct_sql_frozen = True

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
            "legacy_direct_sql_frozen": self.legacy_direct_sql_frozen,
            "legacy_tables_removable": False,
            "remaining_compat_getter_files": list(REMAINING_COMPAT_GETTER_FILES),
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
