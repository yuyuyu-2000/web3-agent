from __future__ import annotations

from typing import Any

from .models import FaultSpec


class FaultInjectingTool:
    """Deterministic tool proxy; inject it when constructing a test-only tool registry."""

    def __init__(self, tool: Any, specs: list[FaultSpec]) -> None:
        self.tool, self.name = tool, str(getattr(tool, "name", tool.__class__.__name__))
        self.specs = [s for s in specs if s.tool == self.name]
        self.total_calls = 0

    def invoke(self, args: dict[str, Any]) -> Any:
        self.total_calls += 1
        occurrence = self.total_calls
        for spec in self.specs:
            if spec.occurrence <= occurrence < spec.occurrence + spec.times:
                if spec.error == "timeout":
                    raise TimeoutError("eval injected timeout")
                if spec.error == "429":
                    raise ConnectionError("429 rate limit (eval injected)")
                if spec.error == "argument_error":
                    raise ValueError("invalid argument (eval injected)")
                if spec.error == "permission_denied":
                    raise PermissionError("permission denied (eval injected)")
                raise RuntimeError("fallback tool failure (eval injected)")
        return self.tool.invoke(args)


def wrap_tools(tools: list[Any], specs: list[FaultSpec]) -> list[Any]:
    return [
        FaultInjectingTool(tool, specs)
        if any(s.tool == getattr(tool, "name", None) for s in specs)
        else tool
        for tool in tools
    ]
