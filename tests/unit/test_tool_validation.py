from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

import commerce.database as database_module
from commerce.database import get_session
from commerce.models import OperationLog, Product
from commerce.tools import CommerceTools


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_sku_sales", {"sku": "", "days": 7}),
        ("get_sku_sales", {"sku": "SKU-1", "days": 0}),
        ("get_inventory", {"sku": "bad sku"}),
        ("compare_competitor_prices", {"external_id": "x" * 129}),
        ("analyze_market_trends", {"keyword": ""}),
        ("run_crawler", {"source": "https://attacker.example"}),
        ("get_orders", {"days": 7, "limit": 501}),
        ("get_sales_summary", {"days": 366}),
    ],
)
def test_invalid_tool_arguments_are_rejected_before_side_effects(
    db_session: Session, tool_name: str, arguments: dict[str, object]
) -> None:
    tools = {
        item.name: item  # type: ignore[attr-defined]
        for item in CommerceTools(db_session, datetime.now(UTC)).langchain_tools()
    }
    with pytest.raises(ValidationError):
        tools[tool_name].invoke(arguments)  # type: ignore[attr-defined]
    assert db_session.query(OperationLog).count() == 0


def test_tool_schemas_do_not_expose_tenant_or_unrestricted_path_arguments(
    db_session: Session,
) -> None:
    tools = CommerceTools(db_session, datetime.now(UTC)).langchain_tools()
    for item in tools:
        properties = item.args_schema.model_json_schema()["properties"]  # type: ignore[attr-defined]
        assert "organization_id" not in properties
        assert "shop_id" not in properties
        assert "url" not in properties
        assert "sql" not in properties


@pytest.mark.parametrize("injected", ["organization_id", "shop_id", "url", "sql"])
def test_tool_schemas_reject_unknown_security_boundary_arguments(
    db_session: Session, injected: str
) -> None:
    tool = next(
        item
        for item in CommerceTools(db_session, datetime.now(UTC)).langchain_tools()
        if item.name == "get_inventory"  # type: ignore[attr-defined]
    )
    with pytest.raises(ValidationError):
        tool.invoke({"sku": "SKU-1", injected: "attacker-controlled"})  # type: ignore[attr-defined]
    assert db_session.query(OperationLog).count() == 0


def test_tool_string_inputs_reject_whitespace_only_values(db_session: Session) -> None:
    tool = next(
        item
        for item in CommerceTools(db_session, datetime.now(UTC)).langchain_tools()
        if item.name == "analyze_market_trends"  # type: ignore[attr-defined]
    )
    with pytest.raises(ValidationError):
        tool.invoke({"keyword": "   "})  # type: ignore[attr-defined]


def _ambient_product() -> Product:
    return Product(
        sku="AMBIENT-SKU",
        name="Ambient transaction",
        category="test",
        price="10.00",
        cost="5.00",
        supplier="test",
    )


def test_successful_read_tool_does_not_commit_ambient_business_writes(db_session: Session) -> None:
    db_session.add(_ambient_product())
    tool = next(
        item
        for item in CommerceTools(db_session, datetime.now(UTC)).langchain_tools()
        if item.name == "get_sales_summary"  # type: ignore[attr-defined]
    )

    tool.invoke({"days": 7})  # type: ignore[attr-defined]
    assert db_session.query(OperationLog).count() == 0
    db_session.rollback()

    assert db_session.query(Product).count() == 0
    assert db_session.query(OperationLog).count() == 0


def test_failed_read_tool_does_not_commit_ambient_business_writes(db_session: Session) -> None:
    db_session.add(_ambient_product())
    tool = next(
        item
        for item in CommerceTools(db_session, datetime.now(UTC)).langchain_tools()
        if item.name == "get_product"  # type: ignore[attr-defined]
    )

    with pytest.raises(LookupError):
        tool.invoke({"sku": "MISSING-SKU"})  # type: ignore[attr-defined]
    assert db_session.query(OperationLog).one().status == "FAILED"
    db_session.rollback()

    assert db_session.query(Product).count() == 0
    assert db_session.query(OperationLog).count() == 1


def test_failed_tool_persists_prior_success_and_failure_audits(db_session: Session) -> None:
    owner = CommerceTools(db_session, datetime.now(UTC))
    tools = {item.name: item for item in owner.langchain_tools()}  # type: ignore[attr-defined]

    tools["get_sales_summary"].invoke({"days": 7})  # type: ignore[attr-defined]
    with pytest.raises(LookupError):
        tools["get_product"].invoke({"sku": "MISSING-SKU"})  # type: ignore[attr-defined]

    with Session(db_session.get_bind()) as verifier:
        operations = verifier.query(OperationLog).order_by(OperationLog.id).all()
        assert [(item.tool_name, item.status) for item in operations] == [
            ("get_sales_summary", "SUCCESS"),
            ("get_product", "FAILED"),
        ]


def test_request_unit_of_work_persists_successful_tool_audit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database_module, "SessionLocal", factory)
    dependency = get_session()
    request_session = next(dependency)
    tool = next(
        item
        for item in CommerceTools(request_session, datetime.now(UTC)).langchain_tools()
        if item.name == "get_sales_summary"  # type: ignore[attr-defined]
    )
    tool.invoke({"days": 7})  # type: ignore[attr-defined]

    with pytest.raises(StopIteration):
        next(dependency)

    with Session(db_session.get_bind()) as verifier:
        assert verifier.query(OperationLog).one().status == "SUCCESS"
