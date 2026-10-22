"""Render the actual JUnit outcomes without treating pytest xfail as Passed."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
rows = ["# Phase 2 — Per-test results", "",
        "Generated from `results.xml`. Known Defect = strict pytest xfail; not Passed.", "",
        "| Test | Status | Detail |", "|---|---|---|"]
for case in ET.parse(ROOT / "results.xml").iter("testcase"):
    skipped = case.find("skipped")
    failure = case.find("failure")
    error = case.find("error")
    if skipped is not None:
        status = "Known Defect" if skipped.get("type") == "pytest.xfail" else "Blocked / skipped"
        detail = skipped.get("message", "")
    elif failure is not None or error is not None:
        status, detail = "Failed", "See results.xml"
    else:
        status, detail = "Passed", ""
    name = case.get("classname") + "::" + case.get("name")
    rows.append(f"| `{name}` | {status} | {detail.replace('|', '/')} |")
(ROOT / "CASE_RESULTS.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
