import hashlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
OUTPUT = ROOT / "jobs.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IntegratedCareRecruitmentMonitor/1.0)"}
POST_WORDS = ("채용공고", "직원채용", "직원 채용", "인재채용", "인재 채용", "채용 안내", "채용안내", "구인")
RESULT_WORDS = ("최종합격", "서류합격", "면접공지", "합격자", "전형결과", "결과발표", "서류전형")
TARGET_WORDS = ("통합돌봄서비스", "최중증 발달장애인 통합돌봄", "최중증 통합돌봄", "발달장애인 통합돌봄")
DATE_TOKEN = r"20\d{2}[.년/-]\s*\d{1,2}[.월/-]\s*\d{1,2}"

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text

def is_same_site(base, link):
    return urlparse(base).netloc.lower().removeprefix("www.") == urlparse(link).netloc.lower().removeprefix("www.")

def parse_date(text):
    m = re.search(DATE_TOKEN, text or "")
    if not m:
        return ""
    parts = re.findall(r"20\d{2}|\d{1,2}", m.group())
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
    except (ValueError, IndexError):
        return ""

def looks_like_post(title):
    return any(word in title for word in POST_WORDS) and not any(word in title for word in RESULT_WORDS)

def find_board_links(soup, base):
    found = []
    for a in soup.find_all("a", href=True):
        label = clean(a.get_text(" ", strip=True) or a.get("title", ""))
        href = urljoin(base, a["href"])
        if label and any(word in label for word in ("채용", "인재채용", "직원채용")) and is_same_site(base, href):
            found.append(href)
    return list(dict.fromkeys(found))

def extract_rows(soup, page_url, org):
    rows, seen = [], set()
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True) or a.get("title", ""))
        href = urljoin(page_url, a["href"])
        if not title or len(title) < 5 or not looks_like_post(title):
            continue
        if not is_same_site(page_url, href) or href.rstrip("/") == page_url.rstrip("/"):
            continue
        if href in seen:
            continue
        seen.add(href)
        parent, context = a, ""
        for _ in range(4):
            if parent.parent:
                parent = parent.parent
                context += " " + clean(parent.get_text(" ", strip=True))
        rows.append({"organization": org, "title": title, "url": href, "postedAt": parse_date(context), "source": page_url})
    return rows

def enrich(row):
    try:
        soup = BeautifulSoup(fetch(row["url"]), "html.parser")
        text = clean(soup.get_text(" ", strip=True))
        combined = row["title"] + " " + text
        if not any(word in combined for word in TARGET_WORDS):
            return None

        # 접수기간의 시작일과 마감일이 원문에 명시된 경우에만 유효 공고로 인정
        period = re.search(
            r"접수(?:기간|일정)\s*[:：]?\s*(" + DATE_TOKEN + r")\s*[~〜～-]\s*(" + DATE_TOKEN + r")",
            text
        )
        if not period:
            return None
        row["openDate"] = parse_date(period.group(1))
        row["closeDate"] = parse_date(period.group(2))
        if not row["openDate"] or not row["closeDate"]:
            return None
        if date.fromisoformat(row["closeDate"]) < date.today():
            return None

        row["position"] = "공고 원문 확인"
        for word in ("사회복지사", "제공인력", "활동지원사", "생활재활교사", "작업치료사", "물리치료사", "언어재활사", "간호사", "조리사"):
            if word in row["title"] or word in text[:1200]:
                row["position"] = word
                break
    except Exception as exc:
        row["detailCheck"] = "상세페이지 자동 확인 실패"
        return None

    row["id"] = hashlib.sha256((row["organization"] + "|" + row["url"]).encode("utf-8")).hexdigest()[:18]
    return row

def main():
    all_rows, source_status = [], []
    for source in CONFIG["sources"]:
        try:
            html = fetch(source["url"])
            soup = BeautifulSoup(html, "html.parser")
            listing_pages = [source["url"]]
            if source.get("mode") == "discover":
                discovered = find_board_links(soup, source["url"])
                listing_pages = discovered[:5]
            count = 0
            for listing in listing_pages:
                page_html = html if listing == source["url"] else fetch(listing)
                rows = extract_rows(BeautifulSoup(page_html, "html.parser"), listing, source["organization"])
                all_rows.extend(rows)
                count += len(rows)
                time.sleep(0.2)
            source_status.append({
                "organization": source["organization"], "url": source["url"],
                "status": "ok" if listing_pages else "board_not_found", "itemsFound": count
            })
        except Exception as exc:
            source_status.append({"organization": source["organization"], "url": source["url"], "status": "error", "message": str(exc)[:180]})
        time.sleep(0.2)

    unique = {}
    for row in all_rows:
        job = enrich(row)
        if job:
            unique[job["id"]] = job

    jobs = sorted(unique.values(), key=lambda x: (x.get("postedAt", ""), x["organization"]), reverse=True)
    payload = {
        "lastChecked": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourcePolicy": CONFIG["policy"],
        "filterPolicy": "공식 홈페이지 원문에서 통합돌봄 관련 문구와 접수기간 시작일·마감일이 모두 확인되고, 마감일이 지나지 않은 채용공고만 표시",
        "sourceCount": len(CONFIG["sources"]),
        "sources": source_status,
        "jobs": jobs
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("공식 홈페이지", len(CONFIG["sources"]), "곳 확인 / 접수 중 통합돌봄 채용공고", len(jobs), "건")
    print("기관별 확인 결과:", json.dumps(source_status, ensure_ascii=False))

if __name__ == "__main__":
    main()
