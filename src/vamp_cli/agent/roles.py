from enum import Enum

class AgentRole(Enum):
    DEFAULT = "default"
    DEFAULT_PLANNER = "default_planner"
    PLANNER = "planner"
    CODER = "coder"
    