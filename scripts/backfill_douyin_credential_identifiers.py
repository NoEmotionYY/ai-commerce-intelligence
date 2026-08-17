from __future__ import annotations

from commerce.config import get_settings
from commerce.credentials import CredentialCipher
from commerce.database import SessionLocal
from commerce.services.credential_backfill import DouyinCredentialIdentifierBackfill


def main() -> None:
    cipher = CredentialCipher.from_settings(get_settings())
    totals = {"scanned": 0, "updated": 0, "failed": 0}
    after_id = 0
    with SessionLocal() as session:
        service = DouyinCredentialIdentifierBackfill(session, cipher)
        while True:
            result = service.run_batch(after_id=after_id, limit=100)
            totals["scanned"] += result.scanned
            totals["updated"] += result.updated
            totals["failed"] += result.failed
            if result.scanned == 0:
                break
            after_id = result.last_credential_id
    print(
        "douyin credential identifier backfill: "
        f"scanned={totals['scanned']} updated={totals['updated']} failed={totals['failed']}"
    )
    if totals["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
