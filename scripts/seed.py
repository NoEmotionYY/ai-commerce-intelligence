from __future__ import annotations

from commerce.database import SessionLocal
from commerce.seed import reset_and_seed


def main() -> None:
    with SessionLocal() as session:
        reset_and_seed(session)
    print("种子数据初始化完成")


if __name__ == "__main__":
    main()
