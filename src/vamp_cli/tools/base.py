from pydantic import BaseModel
from abc import ABC
from typing import Any, Callable, Awaitable

class InputSchema(BaseModel):
    type: str
    properties: dict[str, Any]
    required: list[str]
    additionalProperties: bool


class Tools(ABC):
    name: str
    description: str
    input_schema: InputSchema
    
    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
    
    def run(self, **kwargs) -> str:
        ...