from pydantic import BaseModel, Field
from typing import Optional

class ClusterDiscussionStatusResponse(BaseModel):
    cluster_id: int
    cycle_id: int
    status: str

class ClusterSerializer(BaseModel):
    id: int
    cycle_id: int
    # other fields omitted for brevity
    discussion_status: Optional[str] = Field(default="pending")

    class Config:
        orm_mode = True
