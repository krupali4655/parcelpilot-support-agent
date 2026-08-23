from __future__ import annotations

import sys
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent / "src"))
from parcelpilot.agent import SupportAgent
from parcelpilot.data import AuthContext, ParcelPilotStore
from parcelpilot.llm import GroqIntentRouter


st.set_page_config(page_title="ParcelPilot Support", page_icon="✦", layout="wide")
load_dotenv(Path(__file__).parent / ".env")

@st.cache_resource
def services() -> tuple[ParcelPilotStore, SupportAgent]:
    store = ParcelPilotStore()
    return store, SupportAgent(store)


def setting(name: str, default: str = "") -> str:
    """Read the local .env file first, then Streamlit Cloud's server-side secrets."""
    if value := os.environ.get(name):
        return value
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


@st.cache_resource
def intent_router(api_key: str, model: str) -> GroqIntentRouter:
    return GroqIntentRouter(api_key or None, model)

store, agent = services()
groq_api_key = setting("GROQ_API_KEY")
if not groq_api_key:
    st.error("Groq API key required. Add GROQ_API_KEY to .env locally, then restart Streamlit.")
    st.stop()
router = intent_router(groq_api_key, setting("GROQ_MODEL", "llama-3.3-70b-versatile"))
st.markdown("""<style>
  .stApp { background: #07111f; color: #e7eef9; }
  [data-testid='stSidebar'] { background: #0b1b30; }
  .signal { border-left: 4px solid #35d0ba; padding: .25rem .75rem; background: #10253d; border-radius: 6px; }
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.title("✦ ParcelPilot")
    st.caption("Grounded support decision agent")
    st.divider()
    labels = {row.account_name: row.account_id for _, row in store.accounts.iterrows()}
    chosen = st.selectbox("Demo customer session", list(labels), help="Mock auth context. Every data tool filters by this server-side account ID.", key="account_selector")
    account_id = labels.get(chosen)
    if account_id is None:
        st.error("Invalid account session.")
        st.stop()
    role_labels = {
        "Customer admin": "customer",
        "Support operations analyst": "internal_support",
    }
    role_label = st.selectbox("Demo role", list(role_labels), help="Mock role-based access control for the assessment demo.", key="role_selector")
    role = role_labels.get(role_label)
    if role is None:
        st.error("Invalid role session.")
        st.stop()
    st.success(f"Authenticated as {chosen}")
    st.caption(f"Role: {role_label}")
    st.divider()
    st.caption("Reference snapshot: 16 Aug 2026, 11:00 IST")
    st.caption("Sources: supplied assessment pack only")
    st.caption("LLM routing: Groq enabled")

auth = AuthContext(account_id=account_id, account_name=chosen, role=role)

# Conversation history and pending drafts are account- and role-scoped. Changing
# either identity clears them before anything from the previous session renders.
session_identity = (account_id, role)
if st.session_state.get("session_identity") != session_identity:
    st.session_state.session_identity = session_identity
    st.session_state.messages = []

is_internal = role == "internal_support"
tab_names = ["Support chat", "Trust controls"]
if is_internal:
    tab_names.insert(1, "Internal signals")
tabs = st.tabs(tab_names)
chat_tab = tabs[0]
ops_tab = tabs[1] if is_internal else None
trust_tab = tabs[-1]

with chat_tab:
    st.title("Customer Support")
    st.caption("Answers are constrained by your account, current sources, and signed agreement where applicable.")
    st.session_state.setdefault("messages", [])
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("tools"):
                st.caption("Tools: " + " → ".join(msg["tools"]))
            if msg.get("sources"):
                st.caption("Sources: " + " · ".join(msg["sources"]))
            if msg.get("confidence"):
                st.caption(f"Confidence: {msg['confidence']}")
    prompt = st.chat_input("e.g. Can I cancel ORD-1001 without a fee?")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        route = router.route(prompt)
        reply = agent.respond(prompt, auth, routed_intent=route.intent, routed_order_id=route.order_id)
        tools = (["llm_intent_router"] if route.provider == "groq" else []) + reply.tools
        st.session_state.messages.append({"role": "assistant", "content": reply.answer, "tools": tools, "sources": reply.sources, "draft": reply.action_draft, "confidence": reply.confidence})
        st.rerun()
    if st.session_state.messages and st.session_state.messages[-1].get("draft"):
        draft = st.session_state.messages[-1]["draft"]
        action_title = draft["title"].lower()
        if "approve service credit" in action_title:
            confirmation_label = "Confirm and create credit approval follow-up"
            confirmation_result = "✅ Confirmed: a local credit-approval follow-up was created."
        elif "verify service credit" in action_title:
            confirmation_label = "Confirm and create service-credit verification follow-up"
            confirmation_result = "✅ Confirmed: a local service-credit verification follow-up was created."
        else:
            confirmation_label = "Confirm and create escalation"
            confirmation_result = "✅ Confirmed: a local escalation was created."
        st.warning(f"Proposed action: {draft['title']}\n\n{draft['reason']}")
        if st.button(confirmation_label, type="primary"):
            st.session_state.messages[-1]["content"] += f"\n\n{confirmation_result}"
            st.session_state.messages[-1]["tools"].append("confirmed_state_action")
            st.session_state.messages[-1]["draft"] = None
            st.rerun()

if ops_tab is not None:
    with ops_tab:
        st.title("Operations signal desk")
        st.caption("Available only to the authorised internal-support role in this mock authentication flow.")
        for signal in store.proactive_signals(auth):
            st.markdown(f"<div class='signal'><b>{signal['priority']} · {signal['title']}</b><br/>{signal['detail']}</div><br/>", unsafe_allow_html=True)

with trust_tab:
    st.title("Reliability contract")
    st.markdown("""
    - **Authority:** signed active agreement → current policy/SOP/product guide → historical tickets (context only). Deprecated policy v2 is excluded.
    - **Privacy:** account ID comes from mock authentication, not the chat; structured lookups apply tenant filtering before returning data.
    - **Uncertainty:** unknown fault, timing, or source conflict produces a verification/escalation draft, never a promised credit.
    - **Actions:** the agent can only prepare a draft. A separate explicit confirmation creates the local mock action.
    - **Auditability:** every answer displays the tools and source files used.
    """)
