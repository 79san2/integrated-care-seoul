import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

SERVICE_KEY = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
ENDPOINT = "https://apis.data.go.kr/B554287/sclWlfrFcltInfoInqirService2/getFcltByJoInfo2"
OUTPUT = Path("jobs.json")
SOURCE_PAGE = "https://www.data.go.kr/data/15001848/openapi.do"
SEOUL_CODE = "11000"

# 장애인복지시설의 모든 직종 + 그 밖의 사회복지시설 중 돌봄·복지 현장 핵심 직종
RELEVANT_WORDS = (
    "장애인", "발달장애", "최중증", "통합돌봄", "사회복지사", "생활재활교사",
    "생활지도원", "제공인력", "활동지원", "돌봄", "특수교사", "언어재활",
    "작업치료", "물리치료", "간호사", "요양보호사", "주간활동", "방과후"
)

def local_name(tag):
    return tag.rsplit("}", 1)[-1]

def read_items(root):
    return [node for node in root.iter() if local_name(node.tag) == "item"]

def value(node, name):
    for child in node.iter():
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""

def request_page(page):
    params = {
        "serviceKey": SERVICE_KEY,
        "numOfRows": 100,
        "pageNo": page,
    }
    response = requests.get(ENDPOINT, params=params, timeout=40)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    code = value(root, "resultCode")
    if code and code not in ("00", "0000"):
        msg = value(root, "resultMsg") or "공공데이터 API 오류"
        raise RuntimeError(f"API 오류 {code}: {msg}")
    total = value(root, "totalCount")
    return root, int(total or 0)

def as_date(raw):
    raw = (raw or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""

def safe_url(raw):
    raw = (raw or "").strip()
    if raw and not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    return raw if parsed.scheme in ("http", "https") and parsed.netloc else ""

def main():
    if not SERVICE_KEY:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY GitHub Secret이 설정되지 않았습니다.")
    first, total = request_page(1)
    items = read_items(first)
    page_count = max(1, (total + 99) // 100)
    for page in range(2, page_count + 1):
        root, _ = request_page(page)
        items.extend(read_items(root))
        time.sleep(0.15)

    today = date.today()
    cutoff = today - timedelta(days=90)
    jobs = []
    seen = set()
    for item in items:
        get = lambda key: value(item, key)
        title = get("title")
        org = get("fcltNm")
        position = get("relateJssfc") or get("rcrtFld")
        duty = get("dtyCntn")
        kind = get("fcltKindNm") + " " + get("fcltDtlKindNm")
        region = " ".join((get("wrkPrargRegn"), get("fcltAddr"), get("wrkPrargRegnCd")))
        searchable = " ".join((org, title, position, duty, kind))
        is_seoul = "서울" in region or get("wrkPrargRegnCd") == SEOUL_CODE
        is_relevant = "장애인" in kind or any(word in searchable for word in RELEVANT_WORDS)
        if not (is_seoul and is_relevant and title and org):
            continue

        posted = as_date(get("regDt")) or as_date(get("chgDt"))
        start = as_date(get("accpBgnDe"))
        close = as_date(get("accpClseDe"))
        if posted:
            try:
                if date.fromisoformat(posted) < cutoff and (not close or date.fromisoformat(close) < today):
                    continue
            except ValueError:
                pass
        if close:
            try:
                if date.fromisoformat(close) < today:
                    continue
            except ValueError:
                pass

        join_no = get("joinno")
        stable = join_no or "|".join((org, title, posted, start, close))
        job_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:18]
        if job_id in seen:
            continue
        seen.add(job_id)

        original = safe_url(get("urllnk"))
        homepage = safe_url(get("homepageAddr"))
        jobs.append({
            "id": job_id,
            "organization": org,
            "title": title,
            "position": position or get("rcrtFld") or "공고 원문 확인",
            "postedAt": posted,
            "openDate": start,
            "closeDate": close,
            "url": original or SOURCE_PAGE,
            "homepageUrl": homepage,
            "source": "한국사회보장정보원 사회복지시설정보서비스",
            "sourcePage": SOURCE_PAGE,
            "location": get("wrkPrargRegn") or get("fcltAddr"),
            "employmentType": get("emplStle"),
            "recruitCount": get("recruitNops"),
            "requirements": get("applyQlfc"),
        })

    jobs.sort(key=lambda j: (j.get("postedAt", ""), j.get("closeDate", "")), reverse=True)
    payload = {
        "lastChecked": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE_PAGE,
        "sourceName": "한국사회보장정보원 사회복지시설정보서비스",
        "totalFetched": len(items),
        "jobs": jobs,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"전체 {len(items)}건 확인, 서울·관련 분야 공고 {len(jobs)}건 저장")

if __name__ == "__main__":
    main()
