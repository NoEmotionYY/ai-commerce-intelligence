from datetime import timedelta

import pytest
from langgraph.types import Command
from sqlalchemy.orm import Session

from commerce.models import ApprovalStatus, PurchaseOrder, utcnow
from commerce.seed import AS_OF, reset_and_seed
from commerce.workflow import create_purchase_draft, decide_approval, purchase_graph


def test_purchase_draft_requires_decision(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    draft = create_purchase_draft(db_session, "B205", AS_OF, "agent", "draft-reject-0001")
    assert draft.status is ApprovalStatus.PENDING
    assert draft.action_data["quantity"] == 300
    assert db_session.query(PurchaseOrder).count() == 0
    rejected = decide_approval(db_session, draft.id, "reject", "approver", "不需要")
    assert rejected.status is ApprovalStatus.REJECTED
    assert db_session.query(PurchaseOrder).count() == 0


def test_purchase_graph_is_used_by_real_approval_service(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    draft = create_purchase_draft(db_session, "B205", AS_OF, "agent", "draft-approve-0001")
    approved = decide_approval(db_session, draft.id, "approve", "approver")
    assert approved.status is ApprovalStatus.APPROVED


def test_langgraph_interrupt_and_resume() -> None:
    config = {"configurable": {"thread_id": "workflow-test-1"}}
    first = purchase_graph.invoke(
        {"approval_id": 1, "sku": "B205", "quantity": 300, "unit_cost": "32.00"}, config
    )
    assert "__interrupt__" in first
    resumed = purchase_graph.invoke(Command(resume="approve"), config)
    assert resumed["status"] == "APPROVED"
    assert resumed["decision"] == "approve"


def test_expired_purchase_cannot_be_approved(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    draft = create_purchase_draft(db_session, "B205", AS_OF, "agent", "draft-expired-0001")
    draft.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(ValueError, match="过期"):
        decide_approval(db_session, draft.id, "approve", "approver")
    db_session.refresh(draft)
    assert draft.status is ApprovalStatus.EXPIRED


def test_draft_idempotency_and_conflicting_decision(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    first = create_purchase_draft(db_session, "B205", AS_OF, "agent", "same-action-0001")
    repeated = create_purchase_draft(db_session, "B205", AS_OF, "agent", "same-action-0001")
    assert repeated.id == first.id
    rejected = decide_approval(db_session, first.id, "reject", "approver")
    assert rejected.status is ApprovalStatus.REJECTED
    assert decide_approval(db_session, first.id, "reject", "approver").id == first.id
    with pytest.raises(ValueError, match="相反决定"):
        decide_approval(db_session, first.id, "approve", "approver")
