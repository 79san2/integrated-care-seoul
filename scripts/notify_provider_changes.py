"""Send a push only when previously recorded official source content changes."""
import json
import os
from pathlib import Path

report = json.loads(Path("audit-report.json").read_text(encoding="utf-8"))
changes = report.get("changes", [])
if not changes:
    print("No changes in previously checked official sources.")
    raise SystemExit(0)
raw = os.environ.get("FCM_SERVICE_ACCOUNT_JSON", "").strip()
token = os.environ.get("FCM_DEVICE_TOKEN", "").strip()
if not raw or not token:
    print("Push credentials missing. Changes remain in audit-report.json.")
    raise SystemExit(0)
import firebase_admin
from firebase_admin import credentials, messaging
try:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    body = ", ".join(dict.fromkeys(c.get("name", "") for c in changes[:3]))
    if len(changes) > 3:
        body += f" 외 {len(changes)-3}건"
    messaging.send(messaging.Message(
        token=token,
        data={"title": f"통합돌봄 기관정보 변경 감지 ({len(changes)}건)",
              "body": body + " · 공식 출처 재확인 필요",
              "url": "https://79san2.github.io/integrated-care-seoul/?view=statistics"},
        android=messaging.AndroidConfig(priority="high"),
        webpush=messaging.WebpushConfig(headers={"Urgency": "high"}),
    ))
    print(f"Sent source-change alert for {len(changes)} entries.")
except Exception as exc:
    print(f"Push delivery failed: {exc}")
    raise
