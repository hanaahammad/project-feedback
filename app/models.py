from sqlalchemy import Column, Integer, String, ForeignKey, Enum
from sqlalchemy.orm import relationship
from .db import Base
import enum

class DiscussionStatusEnum(str, enum.Enum):
    pending = "pending"
    discussed = "discussed"
    skipped = "skipped"
    deferred = "deferred"

class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, ForeignKey("cycles.id"), nullable=False)
    # Existing columns...
    # Add discussion status column
    discussion_status = Column(
        String,
        nullable=True,
        default=DiscussionStatusEnum.pending.value,
        comment="Discussion status of the cluster within a cycle",
    )

    cycle = relationship("Cycle", back_populates="clusters")
