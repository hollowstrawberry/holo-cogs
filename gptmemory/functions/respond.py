from gptmemory.schema import MemoryChangeResult, ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase


class RespondFunctionCall(FunctionCallBase):
    display_name = "respond"
    schema = ToolCall(
        Function(
            name="respond",
            description="Sends your final response in chat.",
            parameters=Parameters(
                properties={
                    "content": {
                        "type": "string",
                        "description": "The chat message content."
                    },
                    "required": ["content"],
                }
            )
        )
    )

    async def run(self, arguments: dict) -> str:
        return arguments.get("content", "")
