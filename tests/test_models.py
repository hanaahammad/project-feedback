from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    ActionItem,
    ActionStatus,
    CardCategory,
    Cluster,
    CycleStatus,
    Decision,
    DiscussionNote,
    FeedbackCard,
    FeedbackCycle,
    Project,
    User,
    Vote,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_entities_persist_with_relationships(session):
    project = Project(
        name="Weekly Feedback Tool",
        description="MVP",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )
    session.add(project)
    session.flush()

    author = User(email="author@example.com", password_hash="hashed")
    session.add(author)
    session.flush()

    cycle = FeedbackCycle(project_id=project.id, status=CycleStatus.OPEN)
    session.add(cycle)
    session.flush()

    cluster = Cluster(cycle_id=cycle.id, name="Communication")
    session.add(cluster)
    session.flush()

    card = FeedbackCard(
        cycle_id=cycle.id,
        cluster_id=cluster.id,
        author_id=author.id,
        category=CardCategory.START,
        text="Start writing weekly summaries",
        is_anonymous=True,
    )
    vote = Vote(cluster_id=cluster.id, participant_id=author.id)
    note = DiscussionNote(cluster_id=cluster.id, text="Team agrees this is a priority", author_id=author.id)
    decision = Decision(
        cycle_id=cycle.id,
        cluster_id=cluster.id,
        description="Adopt weekly summaries",
        author_id=author.id,
    )
    action_item = ActionItem(
        cycle_id=cycle.id,
        cluster_id=cluster.id,
        description="Draft summary template",
        due_date=date(2026, 1, 15),
        status=ActionStatus.OPEN,
        owner_id=author.id,
    )
    session.add_all([card, vote, note, decision, action_item])
    session.commit()

    session.expire_all()
    reloaded_project = session.get(Project, project.id)

    assert len(reloaded_project.cycles) == 1
    reloaded_cycle = reloaded_project.cycles[0]
    assert reloaded_cycle.status == CycleStatus.OPEN
    assert reloaded_cycle.project is reloaded_project

    assert len(reloaded_cycle.clusters) == 1
    reloaded_cluster = reloaded_cycle.clusters[0]
    assert reloaded_cluster.name == "Communication"

    assert len(reloaded_cluster.cards) == 1
    assert reloaded_cluster.cards[0].category == CardCategory.START
    assert reloaded_cluster.cards[0].is_anonymous is True
    assert reloaded_cluster.cards[0].author_id == author.id
    assert reloaded_cluster.cards[0].author.email == "author@example.com"

    assert len(reloaded_cluster.votes) == 1
    assert len(reloaded_cluster.notes) == 1
    assert reloaded_cluster.notes[0].text == "Team agrees this is a priority"

    assert len(reloaded_cluster.decisions) == 1
    assert reloaded_cluster.decisions[0].confirmed is False

    assert len(reloaded_cluster.action_items) == 1
    assert reloaded_cluster.action_items[0].status == ActionStatus.OPEN
    assert reloaded_cluster.action_items[0].confirmed is False
