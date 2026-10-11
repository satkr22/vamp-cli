from enum import Enum
from typing import Optional
from pydantic import BaseModel


class AgentStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"


class FeedbackResponse(BaseModel):
    approved: bool
    reason: Optional[str] = None
