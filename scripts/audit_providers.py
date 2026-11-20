"""Monthly evidence audit. Never replace provider values from unverified pages."""
import json
import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
now = datetime.now(ZoneInfo("Asia/Seoul"))
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SeoulCareDirectoryAudit/1.0)"})
previous_path = ROOT / "audit-report.json"
try:
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    previous = {}
previous_checks = {x["id"]: x for x in previous.get("checks", []) if x.get("id")}
changes = []
checks = []
for record in data.get("records", []):
    source = record.get("sourceUrl", "")
    missing = [label for key, label in (
        ("userCapacity", "이용인 정원"), ("userCurrent", "이용인 현원"),
        ("staffCapacity", "종사자 정원"), ("staffCurrent", "종사자 현원"),
        ("teamPhone", "담당팀 연락처")
    ) if record.get(key) in ("", None)]
    result = {"id": record.get("id"), "name": record.get("name"),
              "type": record.get("type"), "sourceUrl": source,
              "missing": missing, "status": "not_checked"}
    if not source or urlparse(source).scheme not in ("http", "https"):
        result["status"] = "no_source"
    else:
        try:
            response = session.get(source, timeout=15, allow_redirects=True)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = re.sub(r"\\s+", " ", soup.get_text(" ", strip=True))
            result["status"] = "reachable"
            result["contentHash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            old = previous_checks.get(record.get("id"), {})
            if old.get("contentHash") and old["contentHash"] != result["contentHash"]:
                changes.append({"id": record.get("id"), "name": record.get("name"),
                                "type": record.get("type"), "sourceUrl": source,
                                "reason": "공식 출처 페이지 변경 감지; 기관별 항목 확인 필요"})
            result["pageTitle"] = (soup.title.get_text(" ", strip=True) if soup.title else "")[:140]
            result["mentionsOrganization"] = re.sub(r"\\s+", "", record.get("name", "")) in re.sub(r"\\s+", "", text)
            # A reachable page is NOT evidence that any stored figure is current.
        except requests.RequestException as exc:
            result["status"] = "fetch_error"
            result["error"] = str(exc)[:180]
    checks.append(result)

report = {
    "checkedAt": now.isoformat(timespec="seconds"),
    "timezone": "Asia/Seoul",
    "totalRecords": len(checks),
    "reachable": sum(x["status"] == "reachable" for x in checks),
    "errors": sum(x["status"] == "fetch_error" for x in checks),
    "missingCounts": {key: sum(key in x["missing"] for x in checks)
                      for key in ("이용인 정원", "이용인 현원", "종사자 정원",
                                  "종사자 현원", "담당팀 연락처")},
    "note": "공식 근거 링크 접속 및 누락 항목을 자동 점검했습니다. 숫자와 연락처는 원문에서 기관·기준일·항목을 확정 검증하기 전에는 자동 변경하지 않습니다.",
    "changes": changes,
    "changeCount": len(changes),
    "checks": checks,
}
(ROOT / "audit-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Checked {len(checks)} entries; reachable={report['reachable']}, errors={report['errors']}")
