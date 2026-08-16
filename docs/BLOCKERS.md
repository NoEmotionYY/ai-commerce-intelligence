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

None confirmed. The Douyin connector is implemented and contract/mock verified, but real-platform
status is `IMPLEMENTED_UNVERIFIED`; no unavailable credential, developer approval, seller
authorization, or external environment has been confirmed, so it is not `BLOCKED_EXTERNAL`.
The TikTok Shop connector is still internal `MISSING` work, not an external blocker.

`COM-P1-006` has no external blocker. Optional detectors, Agent registration, and effect tracking
are internal future scope and therefore remain `MISSING`, not `BLOCKED_EXTERNAL`.

`COM-P1-007` and `COM-P1-008` completed without an external blocker. Real Douyin credentials,
developer approval, seller authorization, or platform access have not been confirmed unavailable
and are not recorded as active blockers. `COM-P1-009` is the next internal implementation task.

---

# Resolved Blockers

None.
