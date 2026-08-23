# ParcelPilot Support Agent

A customer-facing, evidence-grounded support agent for the CalQuity assessment. It runs solely on the supplied ParcelPilot data pack and demonstrates tenant-scoped structured lookups, source precedence, multi-step decisions, and confirmation-gated state changes.

## Quick start

1. Create and activate a virtual environment (Python 3.11+ recommended).
2. Install dependencies: `pip install -r requirements.txt`
3. Keep the supplied assessment files in `data/`.
4. Start the app: `streamlit run app.py`

No API key is required for the assessment demo: the included narrow decision agent handles the supplied source pack deterministically. `OPENAI_API_KEY` is reserved for a production LLM orchestration layer and is intentionally not required to expose or test the application.

## Demo flow

1. Select **Northstar Logistics** and ask: `Can Northstar cancel ORD-1001 without a cancellation fee?`
2. Select **LumenWorks** and ask: `Is ORD-2002 eligible for a service credit?`
3. Ask: `My SwiftShip shipment is still BOOKED after pickup`.
4. Ask: `Please escalate this issue`; confirm the separately displayed proposed action.
5. Open **Internal signals** to show proactive P1 / P2 issue detection.

For the full request-to-action flow and ready-to-use diagrams, see [PROJECT_WORKFLOW.md](PROJECT_WORKFLOW.md).

## Architecture note

### Agent design

`SupportAgent` classifies natural-language support intent, selects the required tools, then applies small deterministic decision policies. The narrow implementation is intentional: it makes source precedence and action safety auditable for a high-risk financial/logistics support workflow. A production model can use function calling to select the same tools, but cannot bypass their authorization and policy checks.

### Tools

| Tool | Purpose | Safety boundary |
| --- | --- | --- |
| `document_search` | Searches current policy/SOP/product docs and the authenticated customer agreement | Ranks only allowed current sources; the agreement is injected as highest authority |
| `structured_lookup` | Looks up orders, accounts, and tickets | Filters by server-provided `AuthContext.account_id`; no model-selected tenant ID |
| `prepare_escalation` | Drafts a local escalation / follow-up | Has no state effect; a separate UI confirmation triggers the mock action |

### Source reliability

Authority order is: signed active customer agreement, current policy/SOP/product guide, then historical tickets as context only. The deprecated Support Policy v2 is never searched. If carrier fault, timing, or customer fault is unknown/conflicting, the system does not promise a credit and offers a verification escalation.

### Trade-offs

The retrieval layer is deliberately lightweight lexical search because the pack is small and human-auditable. Production would use chunked hybrid retrieval, metadata filters (status/effective date/account), citations at paragraph granularity, a durable action service with idempotency keys, RBAC/SSO, encrypted audit logs, and evaluation traces.

## Product note

### Additional problem selected: proactive issue detection

The **Internal signals** tab surfaces P1 candidate incidents, corroborates known issue KI-208, and warns about SwiftShip’s known delay. I chose it because it converts the same data pack from a reactive answer engine into a triage aid for the 20-person operations team.

### Next priorities

1. Replace lexical retrieval with source-versioned hybrid retrieval and citation-level confidence checks.
2. Add SSO/RBAC, immutable audit trails, and a real ticketing integration with approval policies.
3. Build a ticket clusterer and SLA-breach queue with alert routing.
4. Add a labelled evaluation set for accuracy, refusal quality, cross-tenant leakage, and action confirmation.

### Intentionally omitted

Real authentication, durable ticketing integration, background monitoring, model-provider orchestration, and production observability are mocked or scoped out to keep the submission focused and runnable with only the supplied data pack.

### Success metric

**Verified first-contact resolution rate**: the share of eligible support requests resolved without human follow-up, sampled and reviewed for policy/contract correctness and zero cross-tenant leakage.

## AI tool usage

Codex was used to accelerate extraction of the supplied files, scaffold the application, and assist with testing and documentation. The implementation, source-authority policy, safety boundaries, and final review remain explicitly documented in this repository.
