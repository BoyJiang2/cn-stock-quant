from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ResearchCemeteryEntryOut(BaseModel):
    id: int
    research_type: Literal["strategy", "factor"]
    subject_name: str
    source_ref: str
    source_fingerprint: str
    reason: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
