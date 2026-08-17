from __future__ import annotations

import sys
from pathlib import Path

import defusedxml.ElementTree as ET

ALLOWED_SKIPPED_MODULES = {
    "tests.e2e.test_compose",
    "tests.e2e.test_deepseek_cloud",
    "tests.e2e.test_streamlit_browser",
    "tests.e2e.test_v2_streamlit_browser",
}
ALLOWED_SKIP_REASONS = {
    "仅在 Compose 验收环境运行",
    "仅在云模型故障注入验收中运行",
    "仅在显式 DeepSeek 真实云验收环境运行",
    "仅在显式 V2 Streamlit 浏览器验收环境运行",
    "仅在真实 Compose Streamlit 验收环境运行",
}


def validate_skips(report_path: Path) -> int:
    root = ET.parse(report_path).getroot()
    skipped_count = 0
    violations: list[str] = []
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        skipped_count += 1
        module = case.attrib.get("classname", "")
        reason = skipped.attrib.get("message", "")
        if module not in ALLOWED_SKIPPED_MODULES or reason not in ALLOWED_SKIP_REASONS:
            violations.append(f"{module}::{case.attrib.get('name', '')}: {reason}")
    if violations:
        details = "\n".join(violations)
        raise RuntimeError(f"unreviewed pytest skips detected:\n{details}")
    return skipped_count


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_pytest_skips.py <junit.xml>")
    report_path = Path(sys.argv[1])
    count = validate_skips(report_path)
    print(f"reviewed environment-gated pytest skips: {count}")


if __name__ == "__main__":
    main()
