from gptmemory.schema import MemoryChangeResult, ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase


class UpdateMemoryFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="update_memory",
            description="Uses the current context to parse changes to your memory. Should only be used when specific criteria is met according to your system prompt.",
            parameters=Parameters(properties={})
        )
    )

    async def run(self, arguments: dict) -> str:
        changes: list[MemoryChangeResult] = arguments["changes"]
        if not changes:
            return "[Your memory manager did not perform any changes in accordance with its criteria.]"
        return "\n\n".join([f"[[[\n[Updated memory of {change.name}:]\n{change.after}\n]]]" for change in changes])
