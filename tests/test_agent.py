from parcelpilot.agent import SupportAgent
from parcelpilot.data import AuthContext, ParcelPilotStore


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
