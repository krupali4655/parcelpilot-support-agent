from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .data import AuthContext, ParcelPilotStore


# Default targets sourced from 01_Support_Policy_v3_CURRENT.pdf.
DEFAULT_SLA_TARGETS = {
    "Enterprise": "P1 30 minutes, 24x7; P2 2 hours; P3 1 business day",
    "Growth": "P1 2 business hours; P2 4 business hours; P3 2 business days",
    "Standard": "P1 4 business hours; P2 1 business day; P3 2 business days",
}


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

    def _historical_guidance_note(self, auth: AuthContext, keyword: str) -> str:
        for ticket in self.store.tickets_for_customer(auth):
            subject = str(ticket.get("subject", "")).lower()
            resolution = str(ticket.get("historical_resolution", "")).strip()
            if keyword in subject and resolution:
                return f" Note: previous support ticket {ticket['ticket_id']} gave different guidance; that historical guidance is not authoritative. This answer uses the current agreement and policy."
        return ""

    def respond(
        self,
        message: str,
        auth: AuthContext,
        routed_intent: str | None = None,
        routed_order_id: str | None = None,
    ) -> AgentReply:
        text = message.lower()
        # An LLM may classify an intent, but it must never introduce an order
        # identifier. Only an ID explicitly present in the customer message is
        # eligible for lookup; otherwise the safe account-scoped resolver runs.
        order_id = self._order_id(message)
        cancellation_intent = routed_intent == "cancellation" or "cancel" in text or "cancellation" in text
        credit_intent = routed_intent == "service_credit" or "credit" in text or "late" in text or "pickup" in text
        webhook_intent = routed_intent == "webhook_delay" or ("booked" in text and ("pickup" in text or "driver" in text))
        bulk_intent = routed_intent == "bulk_upload" or "bulk" in text or "upload" in text
        sla_intent = routed_intent == "sla" or "sla" in text or "response" in text or "support target" in text
        if routed_intent == "escalation" or "escalat" in text:
            account = self.store.account(auth)
            priority_note = " Premium-support account: prioritise review." if account.get("premium_support") else ""
            return AgentReply(
                f"I can prepare an escalation for the ParcelPilot support team.{priority_note} Please confirm before I create it.",
                ["prepare_escalation"], [],
                {"title": "Customer-requested support escalation", "reason": message[:300] + priority_note}, "Medium"
            )
        if order_id is None and (cancellation_intent or credit_intent):
            candidates = self.store.open_orders_for_customer(auth)
            if credit_intent:
                candidates = [
                    order for order in candidates
                    if order["status"] == "BOOKED" and not order["pickup_actual_at"] and order["carrier_fault"]
                ]
            else:
                candidates = [
                    order for order in candidates
                    if order["status"] in {"DRAFT", "BOOKED"} and not order["pickup_actual_at"]
                ]
            if len(candidates) == 1:
                order_id = str(candidates[0]["order_id"])
            elif not candidates:
                return AgentReply(
                    "I don't see an order on your account matching that description.",
                    ["structured_lookup"], [], confidence="Medium"
                )
            else:
                candidate_ids = ", ".join(str(order["order_id"]) for order in candidates)
                return AgentReply(
                    f"I found multiple orders matching that description: {candidate_ids}. Please specify which order ID you mean.",
                    ["structured_lookup"], [], confidence="Medium"
                )
        if order_id and cancellation_intent:
            return self._cancellation(order_id, auth)
        if webhook_intent:
            return self._webhook_delay(auth, order_id)
        if order_id and credit_intent:
            return self._service_credit(order_id, auth)
        if bulk_intent:
            return self._bulk_upload(auth)
        if sla_intent:
            return self._sla(auth)
        return AgentReply(
            "I can help with orders, cancellations, failed-pickup credits, support SLAs, bulk uploads, and known issues. Please include an order ID where relevant. I will escalate questions that need judgment or evidence not in the supplied sources.",
            [], [], confidence="Medium"
        )

    def _cancellation(self, order_id: str, auth: AuthContext) -> AgentReply:
        order = self.store.order_for_customer(order_id, auth)
        if not order:
            return AgentReply("I cannot find that order in your account. For privacy, I cannot search or reveal another customer’s orders.", ["structured_lookup"], [], confidence="High")
        account = self.store.account(auth)
        contract_terms = self.store.terms_for_customer(auth)
        sources = self.store.search_documents("cancellation booked pickup fee agreement", auth)
        if order["status"] == "PICKED_UP":
            answer = f"{order_id} has already been picked up, so it cannot be cancelled. The applicable workflow is return-to-origin."
        elif order["status"] == "DELIVERED":
            answer = f"{order_id} is delivered and cannot be cancelled."
        elif order["status"] == "DRAFT":
            answer = f"{order_id} is DRAFT and may be cancelled without a fee."
        elif contract_terms.get("cancellation_fee_waived_when_booked"):
            answer = f"Yes. {order_id} is BOOKED and has not been picked up. Northstar’s signed agreement overrides the general SOP and waives the cancellation fee for any BOOKED shipment before pickup."
        else:
            booked = self._as_time(order["booked_at"])
            requested = self._as_time(order["cancellation_requested_at"])
            reference = requested if requested is not None else self.store.snapshot_time
            minutes = int((reference - booked).total_seconds() / 60) if booked is not None else None
            fee = "no fee" if minutes is not None and minutes <= 30 else "INR 250 fee"
            when_clause = "the request was" if requested is not None else "cancelling now would be"
            answer = f"{order_id} is BOOKED and not picked up, so it may be cancelled. The current SOP applies and {when_clause} {minutes} minutes after booking; the applicable outcome is {fee}."
        answer += self._historical_guidance_note(auth, "cancellation")
        return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources])

    def _service_credit(self, order_id: str, auth: AuthContext) -> AgentReply:
        order = self.store.order_for_customer(order_id, auth)
        if not order:
            return AgentReply("I cannot find that order in your account. I cannot access another customer’s orders.", ["structured_lookup"], [], confidence="High")
        end = self._as_time(order["pickup_window_end"])
        actual = self._as_time(order["pickup_actual_at"])
        reference = actual if actual is not None else self.store.snapshot_time
        late_hours = (reference - end).total_seconds() / 3600 if end is not None else 0
        sources = self.store.search_documents("failed pickup service credit carrier fault threshold agreement", auth)
        contract_terms = self.store.terms_for_customer(auth)
        if not order["carrier_fault"] or order["customer_fault"]:
            answer = f"I cannot approve a credit for {order_id}: the required carrier-fault/no-customer-fault evidence is not satisfied. I can prepare an escalation if you have additional evidence."
            return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources], {"title": f"Verify service credit eligibility: {order_id}", "reason": "Missing or conflicting fault evidence"}, "Medium")
        if "credit_late_hours_threshold" in contract_terms and "credit_amount_inr" in contract_terms:
            threshold = contract_terms["credit_late_hours_threshold"]
            amount = contract_terms["credit_amount_inr"]
            eligible = late_hours > threshold
            rule = f"LumenWorks’ signed agreement replaces the default rule: more than {threshold} hours late earns a fixed INR {amount} credit."
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
        answer += self._historical_guidance_note(auth, "bulk upload")
        return AgentReply(answer, ["document_search", "structured_lookup"], [s["source"] for s in sources])

    def _webhook_delay(self, auth: AuthContext, order_id: str | None) -> AgentReply:
        sources = self.store.search_documents("SwiftShip BOOKED pickup webhook delay", auth)
        if order_id:
            order = self.store.order_for_customer(order_id, auth)
            if order and order["carrier"] != "SwiftShip":
                return AgentReply(
                    f"{order_id} uses {order['carrier']}. The known webhook-delay issue does not apply to this shipment. Please verify carrier status directly and escalate if the delay persists.",
                    ["structured_lookup", "document_search"], [s["source"] for s in sources], confidence="Medium"
                )
        return AgentReply("Before treating this as a missed pickup, verify carrier status. If the shipment is on SwiftShip, wait through the known 20-minute webhook delay (KI-211), because a parcel can be physically collected while ParcelPilot still shows BOOKED.", ["document_search"], [s["source"] for s in sources], confidence="Medium")

    def _sla(self, auth: AuthContext) -> AgentReply:
        account = self.store.account(auth)
        contract_terms = self.store.terms_for_customer(auth)
        sources = self.store.search_documents("support response P1 P2 P3 agreement", auth)
        if {"sla_p1", "sla_p2", "sla_p3"}.issubset(contract_terms):
            answer = f"Your signed agreement overrides the support policy: P1 {contract_terms['sla_p1']}; P2 {contract_terms['sla_p2']}; P3 {contract_terms['sla_p3']}."
        else:
            targets = DEFAULT_SLA_TARGETS.get(account["plan"], "")
            answer = f"Your {account['plan']} plan uses the current Support Policy v3: {targets}."
        return AgentReply(answer, ["structured_lookup", "document_search"], [s["source"] for s in sources])
