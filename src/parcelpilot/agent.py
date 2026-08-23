from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .data import AuthContext, ParcelPilotStore, SNAPSHOT_TIME


@dataclass
class AgentReply:
    answer: str
    tools: list[str]
    sources: list[str]
    action_draft: dict[str, str] | None = None
    confidence: str = "High"


class SupportAgent:
    """A narrow, grounded decision agent for the supplied ParcelPilot evidence only."""

    def __init__(self, store: ParcelPilotStore) -> None:
        self.store = store

    @staticmethod
    def _order_id(message: str) -> str | None:
        match = re.search(r"\bORD-\d+\b", message.upper())
        return match.group(0) if match else None

    @staticmethod
    def _as_time(value: Any) -> pd.Timestamp | None:
        if value in ("", None) or pd.isna(value):
            return None
        return pd.Timestamp(value, tz="Asia/Kolkata")

    def respond(self, message: str, auth: AuthContext) -> AgentReply:
        text = message.lower()
        order_id = self._order_id(message)
        if order_id and ("cancel" in text or "cancellation" in text):
            return self._cancellation(order_id, auth)
        if order_id and ("credit" in text or "late" in text or "pickup" in text):
            return self._service_credit(order_id, auth)
        if "bulk" in text or "upload" in text:
            return self._bulk_upload(auth)
        if "booked" in text and ("pickup" in text or "driver" in text):
            return self._webhook_delay(auth)
        if "sla" in text or "response" in text or "support target" in text:
            return self._sla(auth)
        if "escalat" in text:
            return AgentReply(
                "I can prepare an escalation for the ParcelPilot support team. Please confirm before I create it.",
                ["prepare_escalation"], [],
                {"title": "Customer-requested support escalation", "reason": message[:300]}, "Medium"
            )
        return AgentReply(
            "I can help with orders, cancellations, failed-pickup credits, support SLAs, bulk uploads, and known issues. Please include an order ID where relevant. I will escalate questions that need judgment or evidence not in the supplied sources.",
            ["document_search"], [], confidence="Medium"
        )

    def _cancellation(self, order_id: str, auth: AuthContext) -> AgentReply:
        order = self.store.order_for_customer(order_id, auth)
        if not order:
            return AgentReply("I cannot find that order in your account. For privacy, I cannot search or reveal another customer’s orders.", ["structured_lookup"], [], confidence="High")
        account = self.store.account(auth)
        sources = self.store.search_documents("cancellation booked pickup fee agreement", auth)
        if order["status"] == "PICKED_UP":
            answer = f"{order_id} has already been picked up, so it cannot be cancelled. The applicable workflow is return-to-origin."
        elif order["status"] == "DELIVERED":
            answer = f"{order_id} is delivered and cannot be cancelled."
        elif order["status"] == "DRAFT":
            answer = f"{order_id} is DRAFT and may be cancelled without a fee."
        elif account["account_id"] == "ACCT-001":
            answer = f"Yes. {order_id} is BOOKED and has not been picked up. Northstar’s signed agreement overrides the general SOP and waives the cancellation fee for any BOOKED shipment before pickup."
        else:
            booked = self._as_time(order["booked_at"])
            requested = self._as_time(order["cancellation_requested_at"])
            minutes = int((requested - booked).total_seconds() / 60) if booked is not None and requested is not None else None
            fee = "no fee" if minutes is not None and minutes <= 30 else "INR 250 fee"
            answer = f"{order_id} is BOOKED and not picked up, so it may be cancelled. The current SOP applies and the request was {minutes} minutes after booking; the applicable outcome is {fee}."
        return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources])

    def _service_credit(self, order_id: str, auth: AuthContext) -> AgentReply:
        order = self.store.order_for_customer(order_id, auth)
        if not order:
            return AgentReply("I cannot find that order in your account. I cannot access another customer’s orders.", ["structured_lookup"], [], confidence="High")
        end = self._as_time(order["pickup_window_end"])
        late_hours = (SNAPSHOT_TIME - end).total_seconds() / 3600 if end else 0
        sources = self.store.search_documents("failed pickup service credit carrier fault threshold agreement", auth)
        if not order["carrier_fault"] or order["customer_fault"]:
            answer = f"I cannot approve a credit for {order_id}: the required carrier-fault/no-customer-fault evidence is not satisfied. I can prepare an escalation if you have additional evidence."
            return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources], {"title": f"Verify service credit eligibility: {order_id}", "reason": "Missing or conflicting fault evidence"}, "Medium")
        if auth.account_id == "ACCT-002":
            eligible = late_hours > 4
            amount = 300
            rule = "LumenWorks’ signed agreement replaces the default rule: more than 4 hours late earns a fixed INR 300 credit."
        else:
            eligible = late_hours > 2
            amount = min(500, round(float(order["shipment_fee_inr"]) * 0.10))
            rule = "The current SOP applies: more than 2 hours late, carrier fault, no customer fault; the credit is the lower of INR 500 or 10% of the shipment fee."
        status = f"Eligible for INR {amount} service credit" if eligible else "Not yet eligible at the dataset snapshot"
        answer = f"{status} for {order_id}. It is {late_hours:.1f} hours past the scheduled pickup-window end. {rule} I will not create a credit automatically; a human must confirm the state-changing action."
        draft = {"title": f"Approve service credit: {order_id}", "reason": f"{status}; carrier fault recorded; {rule}"} if eligible else None
        return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources], draft)

    def _bulk_upload(self, auth: AuthContext) -> AgentReply:
        sources = self.store.search_documents("bulk upload 3000 5000 known issue", auth)
        plan = self.store.account(auth)["plan"]
        if plan == "Standard":
            answer = "Bulk Upload is not included on the Standard plan."
        else:
            answer = "Bulk Upload is included on your plan up to 5,000 CSV rows. However, current known issue KI-208 causes intermittent failures above about 3,000 rows; split the upload into files below 3,000 rows while it is investigated."
        return AgentReply(answer, ["document_search", "structured_lookup"], [s["source"] for s in sources])

    def _webhook_delay(self, auth: AuthContext) -> AgentReply:
        sources = self.store.search_documents("SwiftShip BOOKED pickup webhook delay", auth)
        return AgentReply("Before treating this as a missed pickup, verify carrier status or wait through the known 20-minute SwiftShip webhook delay (KI-211). A parcel can be physically collected while ParcelPilot still shows BOOKED.", ["document_search"], [s["source"] for s in sources], confidence="Medium")

    def _sla(self, auth: AuthContext) -> AgentReply:
        account = self.store.account(auth)
        sources = self.store.search_documents("support response P1 P2 P3 agreement", auth)
        if auth.account_id == "ACCT-001":
            answer = "Northstar’s signed agreement overrides the support policy: P1 15 minutes, 24x7; P2 1 hour; P3 8 business hours."
        elif auth.account_id == "ACCT-002":
            answer = "LumenWorks’ signed agreement applies: P1 2 business hours, P2 4 business hours, P3 2 business days, with no weekend or after-hours coverage."
        else:
            answer = f"Your {account['plan']} plan uses the current Support Policy v3 unless a signed agreement is added."
        return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources])
