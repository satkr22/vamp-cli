from enum import Enum
import operator
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
    
    plan: str | None # only generated in plan mode
    
    todo: list[TodoItem]
    
    iteration: int
    
    # tool-approval pipeline
    _pending_tool_channel:   str | None
    _pending_tool_calls:     list[dict]
    _dangerous_tool_call_ids: list[str]
    _approved_tool_call_ids:  list[str]
    _denied_tool_call_ids:    list[str]
    
    
    
    
