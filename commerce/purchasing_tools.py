from __future__ import annotations

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from commerce.authorization import Principal
from commerce.schemas import ReplenishmentDraftCreate
from commerce.services.purchasing import PurchasingService


class ReplenishmentToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    warehouse_id: int = Field(gt=0)
    supplier_product_id: int = Field(gt=0)


class ReplenishmentDraftToolInput(ReplenishmentToolInput):
    idempotency_key: str = Field(min_length=8, max_length=128)


class PurchasingAgentTools:
    """Validated purchasing tools with no approval or execution capability."""

    def __init__(self, session: Session, principal: Principal) -> None:
        self.service = PurchasingService(session, principal)

    def langchain_tools(self) -> list[BaseTool]:
        service = self.service

        @tool(args_schema=ReplenishmentToolInput)
        def get_replenishment_recommendation(
            warehouse_id: int,
            supplier_product_id: int,
        ) -> dict[str, object]:
            """Return the deterministic server-calculated replenishment quantity."""
            return service.replenishment_recommendation(
                warehouse_id=warehouse_id,
                supplier_product_id=supplier_product_id,
            )

        @tool(args_schema=ReplenishmentDraftToolInput)
        def create_purchase_draft(
            warehouse_id: int,
            supplier_product_id: int,
            idempotency_key: str,
        ) -> dict[str, object]:
            """Create a draft using the server-calculated quantity; never approve or execute it."""
            order, recommendation, replayed = service.create_replenishment_draft(
                ReplenishmentDraftCreate(
                    warehouse_id=warehouse_id,
                    supplier_product_id=supplier_product_id,
                    idempotency_key=idempotency_key,
                )
            )
            return {
                "purchase_order_id": order.id,
                "status": order.status.value,
                "recommended_quantity": recommendation["recommended_quantity"],
                "quantity": order.items[0].quantity,
                "idempotent_replay": replayed,
            }

        return [get_replenishment_recommendation, create_purchase_draft]
