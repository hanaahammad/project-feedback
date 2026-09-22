import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class CycleStatus(str, enum.Enum):
    OPEN = "open"
    REVEALED = "revealed"
    CLOSED = "closed"


class CardCategory(str, enum.Enum):
    START = "start"
    STOP = "stop"
    CONTINUE = "continue"


class ActionStatus(str, enum.Enum):
    OPEN = "open"
    DONE = "done"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tokens: Mapped[list["AuthToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="tokens")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cycles: Mapped[list["FeedbackCycle"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class FeedbackCycle(Base):
    __tablename__ = "feedback_cycles"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[CycleStatus] = mapped_column(Enum(CycleStatus), default=CycleStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="cycles")
    cards: Mapped[list["FeedbackCard"]] = relationship(back_populates="cycle", cascade="all, delete-orphan")
    clusters: Mapped[list["Cluster"]] = relationship(back_populates="cycle", cascade="all, delete-orphan")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="cycle", cascade="all, delete-orphan")
    action_items: Mapped[list["ActionItem"]] = relationship(back_populates="cycle", cascade="all, delete-orphan")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("feedback_cycles.id"))
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cycle: Mapped["FeedbackCycle"] = relationship(back_populates="clusters")
    cards: Mapped[list["FeedbackCard"]] = relationship(back_populates="cluster")
    votes: Mapped[list["Vote"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")
    notes: Mapped[list["DiscussionNote"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="cluster")
    action_items: Mapped[list["ActionItem"]] = relationship(back_populates="cluster")


class FeedbackCard(Base):
    __tablename__ = "feedback_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("feedback_cycles.id"))
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"))
    category: Mapped[CardCategory] = mapped_column(Enum(CardCategory))
    text: Mapped[str] = mapped_column(Text)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cycle: Mapped["FeedbackCycle"] = relationship(back_populates="cards")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="cards")


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cluster: Mapped["Cluster"] = relationship(back_populates="votes")


class DiscussionNote(Base):
    __tablename__ = "discussion_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cluster: Mapped["Cluster"] = relationship(back_populates="notes")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("feedback_cycles.id"))
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"))
    description: Mapped[str] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cycle: Mapped["FeedbackCycle"] = relationship(back_populates="decisions")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="decisions")


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("feedback_cycles.id"))
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"))
    description: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus), default=ActionStatus.OPEN)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cycle: Mapped["FeedbackCycle"] = relationship(back_populates="action_items")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="action_items")
