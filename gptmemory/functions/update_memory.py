from gptmemory.schema import MemoryChangeResult, ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase
from gptmemory.utils import add_xml_group


class UpdateMemoryFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="update_memory",
            description="Uses the current context to parse changes to your memory. Should only be used when specific criteria is met according to your system prompt.",
            parameters=Parameters(properties={})
        )
    )

    async def run(self, arguments: dict) -> dict | str:
        changes: list[MemoryChangeResult] = arguments["changes"]
        if not changes:
            return "<result>Your memory manager did not perform any changes in accordance with its criteria.</result>"
        change_obj = []
        for change in changes:
            change_obj.append({
                "@name": change.name,
                "#text": change.after,
            })
        return {
            "updated_memories": {
                "memory": change_obj
            }
        }
