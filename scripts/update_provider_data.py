"""Conservative automatic update of verified institution-specific public information."""
import json
import re
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"
AUDIT=ROOT/"audit-report.json"
OUT=ROOT/"auto-update-report.json"
data=json.loads(DATA.read_text(encoding="utf-8"))
audit=json.loads(AUDIT.read_text(encoding="utf-8"))
today=datetime.now(ZoneInfo("Asia/Seoul")).date()
session=requests.Session()
session.headers.update({"User-Agent":"Mozilla/5.0 (compatible; SeoulCareVerifiedUpdate/1.0)"})
phone_pattern=r"(?:0[2-6][0-9]?|070)-?\d{3,4}-?\d{4}"
fields={"userCapacity":"이용인 정원","userCurrent":"이용인 현원",
        "staffCapacity":"종사자 정원","staffCurrent":"종사자 현원"}
result={"checkedAt":datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "updated":[],"candidates":[],"skipped":[],"note":"기관별 최신 공식 근거가 확인될 때만 갱신. 공개되지 않은 현원은 미확인 유지."}
for record in data["records"]:
    url=record.get("teamPhoneSourceUrl") or record.get("sourceUrl") or ""
    host=urlparse(url).netloc.lower()
    # Generic listings and unrelated announcements cannot substantiate staffing or occupancy.
    if not url.startswith("https://") or not host or "broso.or.kr" in host or "data.seoul.go.kr" in host:
        result["skipped"].append({"id":record["id"],"reason":"기관별 수치·직통번호를 증명하는 개별 자료 없음"})
        continue
    try:
        response=session.get(url,timeout=18)
        response.raise_for_status()
        soup=BeautifulSoup(response.text,"html.parser")
        for t in soup(["script","style","nav","footer"]):t.decompose()
        text=re.sub(r"\s+"," ",soup.get_text(" ",strip=True))
    except requests.RequestException as exc:
        result["skipped"].append({"id":record["id"],"reason":"자료 접근 실패: "+str(exc)[:100]})
        continue
    normalized=lambda s:re.sub(r"[^가-힣a-zA-Z0-9]","",s).lower()
    if normalized(record["name"]) not in normalized(text):
        result["skipped"].append({"id":record["id"],"reason":"원문에 정확한 기관명이 확인되지 않음"})
        continue
    if not any(s in text for s in ("통합돌봄","최중증")):
        result["skipped"].append({"id":record["id"],"reason":"통합돌봄서비스 관련성 확인 불가"})
        continue
    # Only dates explicitly printed next to the data are accepted; historical figures never become current.
    for field,label in fields.items():
        pattern=rf"(?:기준일|현황 기준|작성일)\s*[:：]?\s*(20\d{{2}})[.\-/년 ]+([01]?\d)[.\-/월 ]+([0-3]?\d)[일]?[^\n]{{0,140}}?{re.escape(label)}\s*[:：]?\s*(\d{{1,3}})\s*명"
        for match in re.finditer(pattern,text):
            try:stamp=date(*map(int,match.group(1,2,3)))
            except ValueError:continue
            value=int(match.group(4))
            if not (0<=value<=300) or not (date(today.year,today.month,1)<=stamp<=today):continue
            # A single labelled figure is a candidate, not an automatic update:
            # page text may contain multiple service types and historical tables.
            result["candidates"].append({"id":record["id"],"name":record["name"],"field":field,
              "value":value,"asOf":stamp.isoformat(),"sourceUrl":url,"reason":"동일 페이지의 다른 서비스유형과 혼동 가능: 검증 대기"})
    # Explicit team-labelled contact may be safely filled when previously missing,
    # only on a matching institution-specific page, never replacing existing contacts.
    if not record.get("teamPhone"):
        matches=re.findall(r"(?:최중증.{0,16}|통합돌봄.{0,16})(?:팀|담당|문의|연락처)\s*[:：]?\s*("+phone_pattern+r")",text)
        distinct=set(matches)
        if len(distinct)==1:
            number=distinct.pop()
            record["teamPhone"]=number
            record["teamPhoneSource"]="기관별 통합돌봄 담당 연락처 공식 공개자료"
            record["teamPhoneSourceUrl"]=url
            result["updated"].append({"id":record["id"],"name":record["name"],"field":"teamPhone","value":number,"sourceUrl":url})
if result["updated"]:
    data["lastAutoUpdated"]=today.isoformat()
    DATA.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"Applied {len(result['updated'])} verified contacts; {len(result['candidates'])} figures queued for verification.")
