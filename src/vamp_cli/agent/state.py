from enum import Enum
from typing import TypedDict, Annotated, Literal

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages



class TodoItem(TypedDict):
    id: str
    description: str
    status: Literal[
        "pending",
        "in_progress",
        "completed",
        "blocked",
    ]

class AgentState(TypedDict):
    
    # messages: Annotated[list[BaseMessage], add_messages]
    planner_messages: Annotated[
        list[BaseMessage],
        add_messages,
    ]
    
    requires_execution: bool

    coder_messages: Annotated[
        list[BaseMessage],
        add_messages,
    ]

    task: str
    mode: Literal["normal", "plan"]
    plan: str # only generated in plan mode
    is_plan_injected: bool
    todo: list[TodoItem]
    iteration: int
