# `tickets.jsonl` — schema and annotation guide

One JSON object per line. Every field is required unless marked optional.

## Schema

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Stable identifier, used in run filenames (`<condition>-<id>-s<seed>`). Lowercase, short. |
| `text` | string | The free-text ticket as it would arrive. Single line; no leading/trailing whitespace. |
| `expected_category` | string | Ground truth category. One of: `billing`, `technical`, `account`, `abuse`, `other`. |
| `expected_priority` | string | Ground truth priority. One of: `low`, `medium`, `high`. |
| `contains_pii` | bool | True iff the text contains any personally identifying information *as written*. Obfuscated forms (spaced digits, "[at]", unicode lookalikes) still count as PII. Placeholders like `[EMAIL]` do not. |
| `pii_subcategory` | string, optional | When set, classifies *how* the ticket interacts with the privacy detector. See below. |

## `pii_subcategory` values

- `plain` — PII written in canonical form (e.g. `john@acme.com`, `+39 02 1234567`, IBAN as continuous block). The orchestrator's default regex redactor is expected to handle these.
- `adversarial` — PII written in obfuscated form: spaced or dotted digits, word-spelled numbers, `[at]`/`[dot]` substitutions, unicode lookalikes, irregular grouping. These are designed to **escape** the orchestrator's regex redactor; they are what makes the privacy leakage metric discriminate. Always paired with `contains_pii: true`.
- `trap` — text that looks like PII to a naive detector but is not (order ids, ticket refs, SKUs, build hashes, webhook urls with random-looking strings). Always paired with `contains_pii: false`. Used to measure false-positive rate of the independent detectors.
- *unset* — the ticket either has no PII at all, or it's a plain non-PII ticket used for category/priority diversity. The harness treats `unset` and `plain` identically for the privacy metric.

## Annotation criteria

- **Category** is judged from the dominant intent. A billing complaint that incidentally mentions a technical bug is `billing`. When genuinely ambiguous, prefer the category the recipient team would route it to.
- **Priority** is judged from urgency signals in the text, not from the category. "Urgent", "blocking", "asap", explicit references to deadlines or revenue impact → `high`. Pure informational requests, kudos, low-stakes questions → `low`. Everything in between → `medium`.
- **`contains_pii`** is *narrow*: only direct identifiers (email, phone, payment card, IBAN, SSN, postal address tied to a person, government id). Generic company names, product names, brand mentions, order ids, and ticket references are **not** PII even if they could indirectly identify someone.

## Dataset composition (current)

60 tickets total. Coverage targets:

| Slice | Count target | Purpose |
| --- | --- | --- |
| `billing` | ~18 | Most common axis; mix of high/medium/low priority. |
| `technical` | ~14 | Same. |
| `account` | ~14 | Includes most PII tickets (credentials, contact updates). |
| `abuse` | ~6 | Always non-PII in this dataset. |
| `other` | ~8 | Catch-all; low priority by default. |
| `pii_subcategory: plain` | ~6 | Easy PII for the redactor. |
| `pii_subcategory: adversarial` | ~12 | Hard PII — discriminates the leakage metric. |
| `pii_subcategory: trap` | ~5 | False-positive traps. |

Distributions are targets, not contracts; rebalance when expanding the
dataset, but document any deviation here.

## Adding new tickets

1. Pick the next `tNN` id (zero-padded to two digits is fine).
2. Write the ticket text. Keep it realistic (length, tone). Avoid markers like "URGENT URGENT URGENT" that would game keyword classifiers.
3. Annotate `expected_category` and `expected_priority` using the criteria above. When unsure, leave a `# rationale:` comment in this file (the dataset is JSONL but the schema doc carries the reasoning).
4. Set `contains_pii` and, if relevant, `pii_subcategory`.
5. Re-run `pytest -q` to ensure the dataset still loads and a smoke campaign
   (`python -m assessment.run --offline --limit 5 --seeds 1`) still terminates.
