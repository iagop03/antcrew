# Data Processing Agreement (DPA) — template

This template governs the processing of personal data by antcrew on behalf of the
Customer. Both parties must sign and date this agreement before personal data is transferred.

Contact [legal@antcrew.org](mailto:legal@antcrew.org) for a countersigned copy.

---

## Parties

**Data Controller ("Customer"):**
[Customer legal name, address, registration number]

**Data Processor ("antcrew"):**
antcrew — [registered address, Spain]

Together "the parties."

---

## 1. Subject matter and duration

antcrew processes personal data on behalf of the Customer for the purpose of providing
the antcrew-platform service: execution of AI pipelines, storage of run history and
audit attestations, and associated platform functions.

This agreement is in effect for the duration of the service agreement between the parties
and terminates automatically on its expiry or termination.

---

## 2. Nature and purpose of processing

antcrew processes personal data to:

- Execute AI pipeline runs submitted by the Customer ("run requests").
- Store run history, outputs, and attestation documents for audit and reproducibility.
- Deliver notifications and compliance digests configured by the Customer.
- Provide billing, access control, and platform administration.

---

## 3. Categories of data subjects

- Employees and contractors of the Customer who submit run requests.
- Third parties whose personal data may be included in run request content by the Customer.
- Compliance officers and API key holders configured by the Customer.

---

## 4. Categories of personal data

- Run request content (`run.request`): free-form text submitted by the Customer, which may contain names, contact details, or other personal data about data subjects.
- Account identifiers: email addresses, display names, and API key metadata for Customer users.
- Usage metadata: timestamps, cost figures, model identifiers.

antcrew does not collect special-category data (GDPR Art. 9) as part of the service.
The Customer is responsible for not submitting special-category data unless an appropriate
legal basis exists and the Customer has notified antcrew in writing.

---

## 5. Processing instructions

antcrew processes personal data only on the documented instructions of the Customer, as
set out in this agreement and in the service agreement. antcrew will inform the Customer
immediately if, in its opinion, an instruction infringes GDPR or applicable law.

---

## 6. Confidentiality

antcrew ensures that persons authorised to process personal data are bound by confidentiality
obligations (contractual or statutory). Access to Customer data is restricted to personnel
who need it to provide the service.

---

## 7. Security measures (Art. 32)

antcrew implements appropriate technical and organisational measures including:

- HTTPS encryption in transit for all API endpoints.
- AES-256-GCM encryption at rest for sensitive columns when `ANTCREW_ENCRYPTION_KEY` is configured.
- BYOK (Bring Your Own Key) option via keybridge — encryption keys remain in the Customer's KMS.
- Bcrypt password hashing; API keys stored only as SHA-256 hashes.
- Role-based access control; `compliance_viewer` keys scoped to read-only compliance paths.
- Logical workspace isolation — one workspace cannot access another's data.

The Customer may enable additional security controls (BYOK, self-hosting, field encryption)
as documented in [encryption](encryption.md) and [deployment](deployment.md).

---

## 8. Sub-processors (Art. 28(2))

antcrew uses the following sub-processors. The Customer provides general authorisation
for antcrew to engage sub-processors subject to the conditions below.

| Sub-processor | Purpose | Location |
|---|---|---|
| Hetzner Online GmbH | Cloud infrastructure | Germany / EU |
| Stripe, Inc. | Payment processing | USA (SCCs) |
| GitHub, Inc. | OAuth authentication, repository access | USA (SCCs) |
| Anthropic, PBC | Claude API (managed inference only) | USA (SCCs) |
| OpenAI, LLC | GPT-4 API (managed inference only) | USA (SCCs) |

antcrew will notify the Customer of any intended changes to sub-processors with
reasonable advance notice (minimum 14 days), giving the Customer the opportunity to
object. For self-hosted or BYOK deployments, the LLM provider is selected by the
Customer and is the Customer's own sub-processor.

---

## 9. Assistance to the controller (Art. 28(3)(e–f))

antcrew will assist the Customer in fulfilling obligations under GDPR Art. 32–36 (security,
breach notification, DPIA, prior consultation) taking into account the nature of the
processing and information available to antcrew.

antcrew will assist the Customer in responding to data subject requests under GDPR Art. 15–22
via the erasure and data access APIs documented in [data retention](data-retention.md).

---

## 10. Return and deletion of data (Art. 28(3)(g))

Upon expiry or termination of the service agreement, antcrew will:

1. Make Customer data available for export via `GET /compliance/export` for 30 days post-termination (Compliance Pack required).
2. Delete all Customer run data and account information within 90 days of termination.
3. Provide written confirmation of deletion on request.

The Customer may also request immediate erasure via `DELETE /admin/workspaces/{workspace_id}`
(admin-only endpoint) at any time during the service term.

---

## 11. Audit and demonstration of compliance (Art. 28(3)(h))

antcrew will make available to the Customer all information necessary to demonstrate
compliance with the obligations in this agreement and allow for and contribute to audits
and inspections conducted by the Customer or an auditor mandated by the Customer.

Audit requests should be directed to [legal@antcrew.org](mailto:legal@antcrew.org).
Audits are limited to once per 12-month period unless a security incident warrants additional review.

---

## 12. Data breach notification (Art. 33–34)

antcrew will notify the Customer without undue delay (and within 72 hours where feasible)
after becoming aware of a personal data breach. The notification will include, to the extent
available:

- Nature of the breach and categories of data affected.
- Likely consequences.
- Measures taken or proposed to address the breach.

Notifications will be sent to the email address registered on the Customer's account
and to [security@antcrew.org](mailto:security@antcrew.org).

---

## 13. Transfers outside the EEA

Where sub-processors listed in section 8 are located outside the EEA (USA), transfers
are governed by Standard Contractual Clauses (EU Commission Decision 2021/914) incorporated
by reference into this agreement. Copies of applicable SCCs are available from antcrew
on request.

For deployments that must not transfer personal data outside the EU, the Customer should
use a self-hosted antcrew-platform instance with an EU-hosted LLM provider.

---

## 14. Governing law

This agreement is governed by Spanish law. The parties submit to the jurisdiction of
the courts of [city of registration], Spain, without prejudice to either party's right
to seek injunctive relief in any jurisdiction.

---

## 15. Signatures

| | Data Controller (Customer) | Data Processor (antcrew) |
|---|---|---|
| **Name** | | |
| **Title** | | |
| **Date** | | |
| **Signature** | | |

---

*For a countersigned version of this DPA, contact [legal@antcrew.org](mailto:legal@antcrew.org).*
