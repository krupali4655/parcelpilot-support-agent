from parcelpilot.agent import SupportAgent
from parcelpilot.data import AuthContext, ParcelPilotStore
import pandas as pd
import pytest


def agent():
    return SupportAgent(ParcelPilotStore())


def test_northstar_contract_waives_booked_cancellation_fee():
    reply = agent().respond("Can I cancel ORD-1001?", AuthContext("ACCT-001", "Northstar Logistics"))
    assert "waives the cancellation fee" in reply.answer
    assert "05_Northstar_Logistics_Enterprise_Agreement.pdf" in reply.sources


def test_cross_tenant_order_is_not_disclosed():
    reply = agent().respond("Cancel ORD-1001", AuthContext("ACCT-002", "LumenWorks"))
    assert "cannot find" in reply.answer.lower()
    assert not reply.sources


def test_lumen_credit_uses_contract_rule():
    reply = agent().respond("Is ORD-2002 eligible for a credit?", AuthContext("ACCT-002", "LumenWorks"))
    assert "INR 300" in reply.answer
    assert "LumenWorks’ signed agreement" in reply.answer


def test_action_is_only_a_draft():
    reply = agent().respond("Please escalate this", AuthContext("ACCT-001", "Northstar Logistics"))
    assert reply.action_draft
    assert "confirm" in reply.answer.lower()


def test_credit_question_without_order_id_resolves_the_single_matching_order():
    reply = agent().respond(
        "A pickup is three hours late because of carrier fault. Should I get a service credit?",
        AuthContext("ACCT-002", "LumenWorks"),
    )
    assert "ORD-2002" in reply.answer
    assert "please include an order id" not in reply.answer.lower()


def test_cancellation_with_no_logged_request_uses_current_time_not_none():
    reply = agent().respond("Can I cancel ORD-2002 without a fee?", AuthContext("ACCT-002", "LumenWorks"))
    assert "None" not in reply.answer
    assert "minutes after booking" in reply.answer


def test_explicit_escalation_request_is_not_overridden_by_cancel_keywords():
    reply = agent().respond(
        "Please escalate my cancellation request for ORD-1002, I am unhappy with the answer",
        AuthContext("ACCT-001", "Northstar Logistics"),
    )
    assert reply.action_draft is not None
    assert "confirm" in reply.answer.lower()


def test_service_credit_uses_actual_pickup_time_not_snapshot_time():
    a = agent()
    new_row = {
        "order_id": "ORD-9001", "account_id": "ACCT-002", "carrier": "RoadRunner",
        "status": "PICKED_UP", "booked_at": "2026-08-15 08:00",
        "pickup_window_start": "2026-08-15 09:00", "pickup_window_end": "2026-08-15 10:00",
        "pickup_actual_at": "2026-08-15 11:00", "shipment_fee_inr": 2000,
        "carrier_fault": True, "customer_fault": False,
        "cancellation_requested_at": "", "notes": "picked up 1 hour late, a day before snapshot",
    }
    a.store.orders = pd.concat([a.store.orders, pd.DataFrame([new_row])], ignore_index=True)
    reply = a.respond("Is ORD-9001 eligible for a service credit?", AuthContext("ACCT-002", "LumenWorks"))
    assert "1.0 hours" in reply.answer or "1 hour" in reply.answer
    assert "25" not in reply.answer


def test_webhook_delay_does_not_blame_swiftship_for_other_carriers():
    reply = agent().respond(
        "My ORD-1002 shipment with BlueDart is still BOOKED even though the driver picked it up",
        AuthContext("ACCT-001", "Northstar Logistics"),
    )
    assert "SwiftShip" not in reply.answer
    assert "KI-211" not in reply.answer


def test_sla_numbers_are_sourced_from_contract_terms_not_hardcoded_branch():
    reply = agent().respond("What is my P1 response time?", AuthContext("ACCT-001", "Northstar Logistics"))
    assert "15 minutes" in reply.answer


def test_snapshot_time_is_read_from_readme_sheet():
    store = ParcelPilotStore()
    assert str(store.snapshot_time.tz) == "Asia/Kolkata"
    assert store.snapshot_time.strftime("%Y-%m-%d %H:%M") == "2026-08-16 11:00"


def test_northstar_cancellation_flags_outdated_historical_ticket_guidance():
    reply = agent().respond("Can I cancel ORD-1001 90 minutes after booking?", AuthContext("ACCT-001", "Northstar Logistics"))
    assert "TKT-450" in reply.answer or "previous" in reply.answer.lower()


def test_lumenworks_bulk_upload_flags_outdated_historical_ticket_guidance():
    reply = agent().respond("Is bulk upload available on my plan?", AuthContext("ACCT-002", "LumenWorks"))
    assert "5,000" in reply.answer
    assert "TKT-451" in reply.answer or "previous" in reply.answer.lower()


def test_sla_gives_concrete_numbers_for_accounts_without_custom_agreement():
    reply = agent().respond("What is my support SLA?", AuthContext("ACCT-003", "Beacon Retail"))
    assert "business" in reply.answer.lower() or "hour" in reply.answer.lower()
    assert "P1" in reply.answer


def test_proactive_signals_rejects_non_internal_role():
    store = ParcelPilotStore()
    with pytest.raises(PermissionError):
        store.proactive_signals(AuthContext("ACCT-001", "Northstar Logistics", role="customer"))


def test_proactive_signals_allows_internal_role():
    store = ParcelPilotStore()
    signals = store.proactive_signals(AuthContext("ACCT-001", "Northstar Logistics", role="internal_support"))
    assert isinstance(signals, list)


def test_premium_support_escalation_includes_priority_context():
    reply = agent().respond("Please escalate this", AuthContext("ACCT-001", "Northstar Logistics"))
    assert "premium-support" in reply.answer.lower()


def test_llm_route_only_changes_intent_not_authorization_or_policy():
    reply = agent().respond(
        "Could you compensate me for ORD-2002?",
        AuthContext("ACCT-002", "LumenWorks"),
        routed_intent="service_credit",
    )
    assert "INR 300" in reply.answer


def test_mixed_cancel_and_late_keywords_still_resolves_valid_cancellation():
    reply = agent().respond(
        "My pickup was late, can I cancel my order without a fee?",
        AuthContext("ACCT-001", "Northstar Logistics"),
    )
    assert "ORD-1001" in reply.answer
    assert "don't see an order" not in reply.answer.lower()


def test_pickup_window_question_is_not_treated_as_credit_denial():
    reply = agent().respond(
        "What is the pickup window for ORD-1001?",
        AuthContext("ACCT-001", "Northstar Logistics"),
    )
    assert "cannot approve a credit" not in reply.answer.lower()


def test_explicit_credit_question_is_not_swallowed_by_webhook_heuristic():
    reply = agent().respond(
        "My BOOKED shipment pickup was late, do I get a credit?",
        AuthContext("ACCT-002", "LumenWorks"),
    )
    assert "INR 300" in reply.answer
    assert "webhook-delay issue does not apply" not in reply.answer
