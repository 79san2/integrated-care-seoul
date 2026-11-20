import json, os, sys
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, messaging
BEFORE=Path("/tmp/jobs-before.json")
CURRENT=Path("jobs.json")
def load(p):
 try:return json.loads(p.read_text(encoding="utf-8"))
 except (OSError,json.JSONDecodeError):return {}
def main():
 raw=os.environ.get("FCM_SERVICE_ACCOUNT_JSON","").strip()
 token=os.environ.get("FCM_DEVICE_TOKEN","").strip()
 if not raw or not token:
  print("FCM secrets are not configured; push delivery skipped.")
  return 0
 try:
  if not firebase_admin._apps:firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
 except Exception as e:
  print(f"Firebase credential initialization failed: {e}")
  return 1
 old=load(BEFORE);new=load(CURRENT)
 known={str(j.get("id")) for j in old.get("jobs",[]) if j.get("id")}
 fresh=[j for j in new.get("jobs",[]) if j.get("id") and str(j["id"]) not in known]
 sent=0
 for j in fresh:
  data={"title":f"새 채용공고: {j.get('organization','기관명 확인')}"[:100],"body":j.get("title","새로운 통합돌봄서비스 채용공고")[:200],"url":j.get("url") or "https://79san2.github.io/integrated-care-seoul/jobs.html","jobId":str(j.get("id",""))}
  try:
   result=messaging.send(messaging.Message(token=token,data=data,android=messaging.AndroidConfig(priority="high"),webpush=messaging.WebpushConfig(headers={"Urgency":"high"})))
   print(f"Push sent: {data['title']} / {result}");sent+=1
  except Exception as e:
   print(f"Push failed for {j.get('id')}: {e}");return 1
 print(f"New jobs: {len(fresh)}; push notifications sent: {sent}")
 return 0
if __name__=="__main__":sys.exit(main())
