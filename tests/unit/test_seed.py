from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce.models import CompetitorComment, Order, Product
from commerce.seed import reset_and_seed


def test_seed_is_repeatable(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    first = (
        db_session.scalar(select(func.count()).select_from(Product)),
        db_session.scalar(select(func.count()).select_from(Order)),
        db_session.scalar(select(func.count()).select_from(CompetitorComment)),
    )
    reset_and_seed(db_session, order_count=1000)
    second = (
        db_session.scalar(select(func.count()).select_from(Product)),
        db_session.scalar(select(func.count()).select_from(Order)),
        db_session.scalar(select(func.count()).select_from(CompetitorComment)),
    )
    assert first == second == (50, 1000, 1200)
