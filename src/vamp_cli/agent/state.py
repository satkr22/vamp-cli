from enum import Enum
from typing import TypedDict, Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from vamp_cli.agent.status import AgentStatus


class AgentState(TypedDict):
    
    messages: Annotated[list[BaseMessage], add_messages]
    task: str
    iteration: int
