from enum import Enum

class AgentRole(Enum):
    DEFAULT = "default"
    PLANNER = "planner"
    EXECUTOR = "executor"
    