# External Blockers

Only genuine external dependencies belong here.

Examples:

- missing platform credentials;
- platform developer approval;
- seller OAuth authorization;
- external private/paid API access.

Internal engineering problems do not belong here.

---

# Active Blockers

None confirmed. Douyin and TikTok Shop connectors are not implemented yet; this is an
internal `MISSING` task, not an external blocker. Real credentials, developer approval, or
seller authorization may become `BLOCKED_EXTERNAL` only after the corresponding connector
implementation and contract verification exist.

`COM-P1-006` has no external blocker. Optional detectors, Agent registration, and effect tracking
are internal future scope and therefore remain `MISSING`, not `BLOCKED_EXTERNAL`.

`COM-P1-007` has no external blocker. CSV/XLSX parsing, preview, validation, and import are local
implementation work.

---

# Resolved Blockers

None.
