# Privacy Policy

**Last updated: 2026-08-23**

This Privacy Policy describes how antcrew ("antcrew", "we", "us") collects, uses, and
protects personal data when you use antcrew-platform and related services.

antcrew is operated by a company established in Spain and is subject to the
General Data Protection Regulation (GDPR, Regulation (EU) 2016/679) and the
Organic Law 3/2018 on Data Protection and Guarantee of Digital Rights (LOPDGDD).

---

## 1. Data controller

**antcrew**
Email: [legal@antcrew.org](mailto:legal@antcrew.org)

---

## 2. What data we collect

| Category | Examples | Legal basis (GDPR Art. 6) |
|---|---|---|
| Account data | Email address, display name, password hash | Art. 6(1)(b) — contract performance |
| Workspace data | Workspace name, slug, configuration | Art. 6(1)(b) — contract performance |
| Run data | The free-form text submitted as `run.request`; agent outputs stored in `run.state` | Art. 6(1)(b) — contract performance |
| Usage and billing | Cost per run, subscription status, Stripe customer ID | Art. 6(1)(b) — contract performance |
| API keys | Hashed key values, role, creation timestamp | Art. 6(1)(b) — contract performance |
| Email addresses on compliance_viewer keys | Used to deliver daily digest emails | Art. 6(1)(b) — contract performance |
| Security logs | Login events, failed authentication, IP addresses | Art. 6(1)(f) — legitimate interest (platform security) |

We do not collect personal data beyond what is necessary to provide the service.

---

## 3. Run request content

The `run.request` field stores the exact free-form text you submit when triggering
a run — a feature description, a document, a code review request. This field:

- **May contain personal data** if it names or identifies individuals.
- Is retained indefinitely by default, as it forms the audit trail for completed work.
- Can be erased via the GDPR erasure endpoint (see section 7).
- Is optionally encrypted at rest using AES-256-GCM when `ANTCREW_ENCRYPTION_KEY`
  is configured (see [field encryption](encryption.md)).

---

## 4. Sub-processors

antcrew engages the following sub-processors. Each has been assessed for GDPR adequacy
or relies on Standard Contractual Clauses (SCCs) where applicable.

| Sub-processor | Purpose | Location | Safeguard |
|---|---|---|---|
| **Anthropic** | Claude API — managed inference | USA | SCCs + DPA |
| **OpenAI** | GPT-4 API — managed inference | USA | SCCs + DPA |
| **Hetzner Online GmbH** | Cloud infrastructure (servers, storage) | Germany / EU | EU-based, no transfer |
| **Stripe, Inc.** | Payment processing and billing | USA | SCCs + DPA |
| **GitHub, Inc.** | OAuth login, repository access | USA | SCCs + DPA |
| **SMTP provider** | Transactional email delivery | Configurable by self-hosted operators | Operator-selected |

For BYOK (Bring Your Own Key) deployments and self-hosted instances:
antcrew does not process your LLM API calls. The LLM provider you configure is
your own sub-processor, not ours.

---

## 5. Data retention

| Data type | Retention |
|---|---|
| `event` rows (engine events) | 30 days (configurable via `DATA_RETENTION_DAYS`) |
| `webhook_delivery` rows | 30 days |
| `discovery_session` rows | 7 days of inactivity |
| `run` rows (including `run.request`) | Indefinite — forms the audit trail |
| `workspace` and account data | Duration of service agreement |

See [data retention](data-retention.md) for full details and manual deletion procedures.

---

## 6. International transfers

Run request content processed via Anthropic or OpenAI APIs is transferred to the USA.
This transfer is governed by Standard Contractual Clauses. For deployments where no
personal data may leave the EU, use a self-hosted instance with an EU-hosted model
(e.g., Mistral via LiteLLM, or Ollama on EU infrastructure).

---

## 7. Your rights (GDPR Art. 15–22)

You have the right to:

- **Access** (Art. 15): request a copy of personal data we hold about you.
- **Rectification** (Art. 16): correct inaccurate personal data.
- **Erasure** (Art. 17): request deletion of your personal data. The platform provides
  an automated erasure endpoint: `POST /admin/users/{user_id}/erase` (admin-only).
  This anonymises account PII and erases run request content across all your workspaces.
  See [data retention](data-retention.md#gdpr-erasure-api) for the full procedure.
- **Restriction** (Art. 18): request that we stop processing your data while a dispute is resolved.
- **Portability** (Art. 20): receive your data in a machine-readable format.
- **Object** (Art. 21): object to processing based on legitimate interests.

To exercise any of these rights, contact [legal@antcrew.org](mailto:legal@antcrew.org).
We will respond within 30 days.

---

## 8. Data Processing Agreement (DPA)

If you use antcrew-platform to process personal data on behalf of your own customers,
antcrew acts as your Data Processor under GDPR Art. 28.

A DPA template is available at [dpa-template](dpa-template.md).
For a countersigned DPA, contact [legal@antcrew.org](mailto:legal@antcrew.org).

---

## 9. Cookies and tracking

antcrew-platform does not use tracking cookies or third-party analytics.
The platform sets one session cookie for authenticated users. No cross-site tracking.

---

## 10. Security measures

- HTTPS enforced on all endpoints.
- Passwords stored as bcrypt hashes; never logged.
- API keys stored as SHA-256 hashes; the plaintext is shown once on creation.
- Optional AES-256-GCM field encryption at rest for `run.state` and integration credentials.
- Optional BYOK (Bring Your Own Key) via keybridge — your keys never leave your KMS.

---

## 11. Contact and supervisory authority

**Data controller contact:** [legal@antcrew.org](mailto:legal@antcrew.org)

You also have the right to lodge a complaint with the Spanish supervisory authority:

**Agencia Española de Protección de Datos (AEPD)**
C/ Jorge Juan, 6, 28001 Madrid, Spain
[www.aepd.es](https://www.aepd.es)

---

## 12. Changes to this policy

We will notify registered users by email of any material changes. The "Last updated"
date at the top of this page reflects the most recent revision.
