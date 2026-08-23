"""Optional, bounded LLM routing for ParcelPilot support requests.

The model sees only the customer's message. It never receives order data or
source documents, and it cannot decide an outcome or perform an action.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


ALLOWED_INTENTS = {
    "cancellation",
    "service_credit",
    "webhook_delay",
    "bulk_upload",
    "sla",
    "escalation",
    "unknown",
}


@dataclass(frozen=True)
class IntentRoute:
    intent: str
    order_id: str | None = None
    provider: str = "deterministic"


class GroqIntentRouter:
    """Uses Groq only to classify a request into a fixed, safe vocabulary."""

    def __init__(self, api_key: str | None, model: str = "llama-3.3-70b-versatile") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def route(self, message: str) -> IntentRoute:
        if not self.enabled:
            return IntentRoute("unknown")
        try:
            # Delayed import keeps the deterministic assessment mode runnable
            # even before the optional SDK has been installed.
            from groq import Groq

            response = Groq(api_key=self.api_key).chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=80,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify the ParcelPilot support request. Return JSON only with "
                            "intent and order_id. intent must be exactly one of: cancellation, "
                            "service_credit, webhook_delay, bulk_upload, sla, escalation, unknown. "
                            "order_id must be an explicit ORD-<digits> in the request or null. "
                            "Do not answer the customer and do not infer eligibility."
                        ),
                    },
                    {"role": "user", "content": message},
                ],
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            intent = str(payload.get("intent", "unknown"))
            raw_order_id = payload.get("order_id")
            order_id = str(raw_order_id).upper() if raw_order_id else None
            if intent not in ALLOWED_INTENTS:
                return IntentRoute("unknown")
            if order_id and not re.fullmatch(r"ORD-\d+", order_id):
                order_id = None
            return IntentRoute(intent, order_id, provider="groq")
        except Exception:
            # Provider outages and malformed model responses must never block
            # support. The deterministic router remains the safe fallback.
            return IntentRoute("unknown")
