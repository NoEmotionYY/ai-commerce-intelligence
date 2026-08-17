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

None confirmed. The Douyin and TikTok Shop connectors are implemented and contract/mock verified,
but both real-platform statuses are `IMPLEMENTED_UNVERIFIED`; no unavailable credential, developer
approval, seller authorization, or external environment has been confirmed, so neither is
`BLOCKED_EXTERNAL`.

`COM-P1-006` and `COM-P1-010` completed without an external blocker. Optional price/order/finance
detectors and future product-frontend/deployment work are internal scope, not `BLOCKED_EXTERNAL`.

`COM-P1-007` through `COM-P1-009` completed without an external blocker. Real Douyin/TikTok Shop
credentials, developer approval, seller authorization, or platform access have not been confirmed
unavailable and are not recorded as active blockers. `COM-P1-011` is the next internal task.

As of the final `COM-P1-010` Exit Review, active `BLOCKED_EXTERNAL` count is `0`. The final local
and mock evidence does not substitute for real-platform verification; absence of a confirmed
credential/approval/environment denial is intentionally not recorded as an external blocker.

---

# Resolved Blockers

None.
