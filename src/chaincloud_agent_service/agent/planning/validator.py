from __future__ import annotations

from chaincloud_agent_service.agent.planning.models import Plan


class PlanValidationError(ValueError):
    pass


def validate_plan(plan: Plan, available_tools: set[str]) -> Plan:
    """Validate references and reject cyclic or unusable plans."""

    ids = [step.id for step in plan.steps]
    if len(ids) != len(set(ids)):
        raise PlanValidationError("计划步骤 ID 不能重复")

    known_ids = set(ids)
    for step in plan.steps:
        unknown_dependencies = set(step.depends_on) - known_ids
        if unknown_dependencies:
            raise PlanValidationError(
                f"步骤 {step.id} 引用了不存在的依赖: "
                f"{', '.join(sorted(unknown_dependencies))}"
            )
        if step.id in step.depends_on:
            raise PlanValidationError(f"步骤 {step.id} 不能依赖自身")
        unknown_tools = set(step.suggested_tools) - available_tools
        if unknown_tools:
            raise PlanValidationError(
                f"步骤 {step.id} 引用了不存在的工具: "
                f"{', '.join(sorted(unknown_tools))}"
            )
        unknown_fallbacks = set(step.fallback_tools) - available_tools
        if unknown_fallbacks:
            raise PlanValidationError(
                f"步骤 {step.id} 引用了不存在的 fallback 工具: "
                f"{', '.join(sorted(unknown_fallbacks))}"
            )

    dependencies = {step.id: set(step.depends_on) for step in plan.steps}
    remaining = set(ids)
    resolved: set[str] = set()
    while remaining:
        ready = {step_id for step_id in remaining if dependencies[step_id] <= resolved}
        if not ready:
            raise PlanValidationError("计划步骤存在循环依赖")
        resolved.update(ready)
        remaining -= ready

    return plan
