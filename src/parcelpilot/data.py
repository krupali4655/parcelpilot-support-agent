from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SNAPSHOT_TIME = pd.Timestamp("2026-08-16 11:00", tz="Asia/Kolkata")


@dataclass(frozen=True)
class AuthContext:
    """Mock session context. Tools receive this object, never a model-supplied tenant id."""

    account_id: str
    account_name: str
    role: str = "customer"


class ParcelPilotStore:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.data_dir = data_dir
        workbook = data_dir / "ParcelPilot_Assessment_Data.xlsx"
        self.accounts = pd.read_excel(workbook, sheet_name="accounts").fillna("")
        self.orders = pd.read_excel(workbook, sheet_name="orders").fillna("")
        self.tickets = pd.read_excel(workbook, sheet_name="tickets").fillna("")
        self.documents = self._load_documents()

    def _load_documents(self) -> dict[str, str]:
        docs: dict[str, str] = {}
        for path in sorted(self.data_dir.glob("*.pdf")):
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            docs[path.name] = re.sub(r"\s+", " ", text).strip()
        return docs

    def account(self, auth: AuthContext) -> dict[str, Any]:
        row = self.accounts.loc[self.accounts.account_id == auth.account_id]
        if row.empty:
            raise PermissionError("Unknown account context")
        return row.iloc[0].to_dict()

    def order_for_customer(self, order_id: str, auth: AuthContext) -> dict[str, Any] | None:
        """Tenant filter is in the data layer, so prompt injection cannot bypass it."""
        row = self.orders.loc[
            (self.orders.order_id.astype(str).str.upper() == order_id.upper())
            & (self.orders.account_id == auth.account_id)
        ]
        return None if row.empty else row.iloc[0].to_dict()

    def tickets_for_customer(self, auth: AuthContext) -> list[dict[str, Any]]:
        rows = self.tickets.loc[self.tickets.account_id == auth.account_id]
        return rows.to_dict(orient="records")

    def search_documents(self, query: str, auth: AuthContext, limit: int = 3) -> list[dict[str, str]]:
        """Simple lexical retrieval with source eligibility enforced before ranking."""
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        allowed = [
            "01_Support_Policy_v3_CURRENT.pdf",
            "03_Cancellation_and_Service_Credit_SOP_v4.pdf",
            "04_Product_Operations_Guide_and_Known_Issues.pdf",
        ]
        contract = self.account(auth).get("contract_file", "")
        if contract:
            allowed.insert(0, str(contract))  # Contract is deliberately higher priority.

        scored: list[tuple[int, str, str]] = []
        for name in allowed:
            source = self.documents.get(name, "")
            score = sum(token in source.lower() for token in tokens)
            if score:
                excerpt = source[:700] if len(source) <= 700 else source[:700].rsplit(" ", 1)[0] + "..."
                scored.append((score, name, excerpt))
        return [
            {"source": name, "excerpt": excerpt, "authority": self.source_authority(name, auth)}
            for _, name, excerpt in sorted(scored, reverse=True)[:limit]
        ]

    def source_authority(self, filename: str, auth: AuthContext) -> str:
        contract = self.account(auth).get("contract_file", "")
        if filename == contract and contract:
            return "Signed customer agreement - highest authority"
        if "DEPRECATED" in filename:
            return "Deprecated - excluded from current answers"
        if "Policy_v3" in filename or "SOP_v4" in filename or "Operations_Guide" in filename:
            return "Current operational source"
        return "Context only"

    def proactive_signals(self) -> list[dict[str, str]]:
        """Internal-only analysis. Customer tool paths never call this method."""
        tickets = self.tickets.loc[self.tickets.status == "open"]
        signals: list[dict[str, str]] = []
        p1 = tickets[tickets.subject.str.contains("shipment creation|API key", case=False, regex=True)]
        for _, ticket in p1.iterrows():
            account = self.accounts.loc[self.accounts.account_id == ticket.account_id].iloc[0]
            signals.append({"priority": "P1", "title": ticket.subject, "detail": f"{ticket.ticket_id} for {account.account_name}: immediate escalation recommended."})
        bulk = tickets[tickets.subject.str.contains("Bulk upload", case=False, regex=False)]
        if not bulk.empty:
            signals.append({"priority": "P2", "title": "Known issue KI-208 corroborated", "detail": "Bulk-upload failure is consistent with the current product known issue; guide customer to files below 3,000 rows."})
        signals.append({"priority": "Watch", "title": "SwiftShip status delay", "detail": "KI-211 may explain a BOOKED shipment for up to 20 minutes after physical pickup; verify carrier status before declaring a failed pickup."})
        return signals
