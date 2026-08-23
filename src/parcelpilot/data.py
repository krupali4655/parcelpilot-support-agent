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
        readme = pd.read_excel(workbook, sheet_name="README", header=None).fillna("")
        self.snapshot_time = self._snapshot_time_from_readme(readme)
        self.accounts = pd.read_excel(workbook, sheet_name="accounts").fillna("")
        self.orders = pd.read_excel(workbook, sheet_name="orders").fillna("")
        self.tickets = pd.read_excel(workbook, sheet_name="tickets").fillna("")
        self.documents = self._load_documents()
        self.contract_terms = self._load_contract_terms()

    @staticmethod
    def _snapshot_time_from_readme(readme: pd.DataFrame) -> pd.Timestamp:
        snapshot_rows = readme.loc[readme.iloc[:, 0].astype(str).str.strip() == "Dataset snapshot"]
        if snapshot_rows.empty:
            return SNAPSHOT_TIME  # Fallback for malformed assessment workbooks.
        snapshot_text = str(snapshot_rows.iloc[0, 1]).strip()
        try:
            datetime_text, timezone_name = snapshot_text.rsplit(" ", 1)
            return pd.Timestamp(datetime_text, tz=timezone_name)
        except (TypeError, ValueError):
            return SNAPSHOT_TIME  # Fallback for unparseable README timestamps.

    def _load_documents(self) -> dict[str, str]:
        docs: dict[str, str] = {}
        for path in sorted(self.data_dir.glob("*.pdf")):
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            docs[path.name] = re.sub(r"\s+", " ", text).strip()
        return docs

    def _load_contract_terms(self) -> dict[str, dict[str, Any]]:
        """Extract the small set of active customer-specific overrides from contract text."""
        terms_by_account: dict[str, dict[str, Any]] = {}
        for _, account in self.accounts.iterrows():
            contract_file = str(account.get("contract_file", ""))
            text = self.documents.get(contract_file, "")
            if not contract_file or not text:
                continue
            terms: dict[str, Any] = {}
            if re.search(r"BOOKED shipment before pickup with no cancellation fee", text, re.IGNORECASE):
                terms["cancellation_fee_waived_when_booked"] = True
            credit_match = re.search(r"more than (\d+) hours.*?fixed INR ([\d,]+) service credit", text, re.IGNORECASE)
            if credit_match:
                terms["credit_late_hours_threshold"] = int(credit_match.group(1))
                terms["credit_amount_inr"] = int(credit_match.group(2).replace(",", ""))
            sla_match = re.search(r"P1:\s*(.*?)\s*● P2:\s*(.*?)\s*● P3:\s*(.*?)(?:\s*2\. |\s*● No weekend|$)", text)
            if sla_match:
                terms.update({
                    "sla_p1": sla_match.group(1).strip(),
                    "sla_p2": sla_match.group(2).strip(),
                    "sla_p3": sla_match.group(3).strip(),
                })
            terms_by_account[str(account["account_id"])] = terms
        return terms_by_account

    def terms_for_customer(self, auth: AuthContext) -> dict[str, Any]:
        return self.contract_terms.get(auth.account_id, {})

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

    def open_orders_for_customer(self, auth: AuthContext) -> list[dict[str, Any]]:
        """Return non-delivered orders belonging to the authenticated customer."""
        rows = self.orders.loc[
            (self.orders.account_id == auth.account_id)
            & (self.orders.status != "DELIVERED")
        ]
        return rows.to_dict(orient="records")

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

    def proactive_signals(self, auth: AuthContext) -> list[dict[str, str]]:
        """Internal-only analysis. Customer tool paths never call this method."""
        if auth.role != "internal_support":
            raise PermissionError("Proactive signals are restricted to internal support/operations roles.")
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
