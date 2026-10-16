from pydantic import BaseModel
from abc import ABC
from typing import Any
import asyncio



class InputSchema(BaseModel):
    type: str
    properties: dict[str, Any]
    required: list[str]
    additionalProperties: bool



class Tools(ABC):
    name: str
    description: str
    input_schema: InputSchema
    parallel_safe: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_tool(self) -> dict[str, Any]:
        """OpenAI function-calling shape accepted by ChatLiteLLM.bind_tools()."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema.model_dump(),
            },
        }

    def run(self, **kwargs: Any) -> str:
        raise NotImplementedError
    
    async def arun(self, **kwargs: Any) -> str:
        return await asyncio.to_thread(self.run, **kwargs)