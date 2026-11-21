import hashlib
from collections import Counter
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
OUTPUT = ROOT / "jobs.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IntegratedCareRecruitmentMonitor/1.0)"}
POST_WORDS = ("채용", "모집", "구인")
RESULT_WORDS = ("최종합격", "서류합격", "면접공지", "합격자", "전형결과", "결과발표", "서류전형")
TARGET_WORDS = ("통합돌봄서비스", "최중증 발달장애인 통합돌봄", "최중증 통합돌봄", "발달장애인 통합돌봄")

def is_target_notice(text):
    compact = re.sub(r"[\s·ㆍ_\-]", "", text or "")
    # '통합돌봄'과 '최중증' 또는 '발달장애인'이 함께 확인되는 공고만 대상
    return "통합돌봄" in compact and ("최중증" in compact or "발달장애인" in compact)

def infer_organization(title, context, fallback):
    combined = clean(title + " " + context[:5000])
    compact = re.sub(r"\s+", "", combined).lower()
    known = CONFIG.get("organizations", [])
    # 앱에 등록된 서울 제공기관 명칭과 공고 원문을 대조해 실제 채용기관을 식별합니다.
    matches = [
        name for name in known
        if re.sub(r"\s+", "", name).lower() in compact
    ]
    if matches:
        return max(matches, key=len)
    match = re.match(r"^[\[\(【]([^\]\)】]{2,40})[\]\)】]", title)
    if match:
        return clean(match.group(1))
    prefix = re.split(r"(?:직원)?(?:채용|모집|구인)", title, maxsplit=1)[0].strip(" []()【】-_:：")
    if 2 <= len(prefix) <= 35 and not any(x in prefix for x in ("공고", "긴급", "재공고", "직원", "통합돌봄", "최중증", "발달장애인", "제공인력")):
        return prefix
    m = re.search(r"(?:기관명|시설명|복지관명)\s*[:：]\s*([^,|]{2,40})", context[:1800])
    return clean(m.group(1)) if m else fallback
DATE_TOKEN = r"20\d{2}\s*[.년/-]\s*\d{1,2}\s*[.월/-]\s*\d{1,2}"
REJECTIONS = Counter()
SESSION = requests.Session()

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def canonicalize_url(url):
    """Remove listing sort/pagination parameters that do not identify a notice."""
    from urllib.parse import parse_qsl, urlencode
    parts = urlparse(url)
    ignored = {"page", "sst", "sod", "sop"}
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in ignored]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))

def fetch(url):
    response = SESSION.get(url, headers=HEADERS, timeout=25)
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
    recruitment_title = any(word in title for word in POST_WORDS) or is_target_notice(title)
    return recruitment_title and not any(word in title for word in RESULT_WORDS)

def find_board_links(soup, base):
    found = []
    for a in soup.find_all("a", href=True):
        label = clean(a.get_text(" ", strip=True) or a.get("title", ""))
        href = urljoin(base, a["href"])
        if label and any(word in label for word in ("채용", "인재채용", "직원채용")) and is_same_site(base, href):
            found.append(href)
    return list(dict.fromkeys(found))

def find_pagination_links(soup, base, limit=5):
    found = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        parsed = urlparse(href)
        query = parsed.query.lower()
        label = clean(a.get_text(" ", strip=True))
        is_page_link = (
            re.search(r"(?:^|[&?])(?:page|pageindex|pageno|page_no|pg)=\d+", "?" + query) is not None
            or label in ("다음", ">", "»")
        )
        if is_page_link and is_same_site(base, href) and href.rstrip("/") != base.rstrip("/"):
            found.append(href)
    return list(dict.fromkeys(found))[:limit]

def extract_rows(soup, page_url, org, allow_external=False):
    rows, seen = [], set()
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True) or a.get("title", ""))
        href = canonicalize_url(urljoin(page_url, a["href"]))
        if not title or len(title) < 5 or not looks_like_post(title):
            continue
        if (not allow_external and not is_same_site(page_url, href)) or href.rstrip("/") == page_url.rstrip("/"):
            continue
        if href in seen:
            continue
        seen.add(href)
        parent, context = a, ""
        for _ in range(4):
            if parent.parent:
                parent = parent.parent
                context += " " + clean(parent.get_text(" ", strip=True))
        rows.append({"organization": infer_organization(title, context, "기관명 확인 필요" if allow_external else org), "sourceOrganization": org,
                     "title": title, "url": href, "postedAt": parse_date(context), "source": page_url})
    return rows

def enrich(row):
    soup = BeautifulSoup(fetch(row["url"]), "html.parser")
    text = clean(soup.get_text(" ", strip=True))
    combined = row["title"] + " " + text
    if not is_target_notice(combined):
        REJECTIONS["not_target"] += 1
        return None
    row["organization"] = infer_organization(row["title"], text, row.get("organization", "기관명 확인 필요"))

    # 접수기간 표기 변형(접수기간/원서접수/모집기간, 요일 괄호, 날짜 사이 공백)을 폭넓게 처리
    period = re.search(
        r"(?:접수\s*기간|접수\s*일정|모집\s*기간|모집\s*일정|원서\s*접수|지원\s*기간|채용\s*기간)[^0-9]{0,35}(" + DATE_TOKEN + r")[^0-9]{0,30}[~〜～부터-]+[^0-9]{0,10}(" + DATE_TOKEN + r")",
        text,
        re.IGNORECASE
    )
    if period:
        row["openDate"] = parse_date(period.group(1))
        row["closeDate"] = parse_date(period.group(2))
    else:
        dates = list(re.finditer(DATE_TOKEN, text))
        found = None
        for i in range(len(dates) - 1):
            between = text[dates[i].end():dates[i+1].start()]
            before = text[max(0, dates[i].start()-100):dates[i].start()]
            if re.search(r"[~〜～-]", between) and re.search(r"접수|모집|원서|지원", before):
                found = (dates[i].group(), dates[i+1].group())
                break
        if found:
            row["openDate"] = parse_date(found[0])
            row["closeDate"] = parse_date(found[1])
        else:
            # 시작일이 생략된 공고는 명시된 마감일과 게시일이 모두 있을 때만 인정
            deadline = re.search(
                r"(?:접수\s*마감|모집\s*마감|지원\s*마감|마감\s*일(?:시)?|접수\s*기한|제출\s*기한)[^0-9]{0,35}(" + DATE_TOKEN + r")",
                text, re.IGNORECASE
            )
            if not deadline or not row.get("postedAt"):
                REJECTIONS["date_not_found"] += 1
                return None
            row["openDate"] = row["postedAt"]
            row["closeDate"] = parse_date(deadline.group(1))
    if not row["openDate"] or not row["closeDate"]:
        REJECTIONS["invalid_date"] += 1
        return None
    start = date.fromisoformat(row["openDate"])
    end = date.fromisoformat(row["closeDate"])
    # Reject dates from unrelated annual schedules, archives or malformed notices.
    # Recruitment periods should be plausible relative to the notice's posting date.
    if end < start or (end - start).days > 120:
        REJECTIONS["implausible_period"] += 1
        return None
    if end < date.today() or start > date.today():
        REJECTIONS["closed_or_not_started"] += 1
        return None
    if row.get("postedAt"):
        try:
            posted = date.fromisoformat(row["postedAt"])
            if start < posted.replace(year=posted.year - 1) or (start - posted).days > 90:
                return None
        except ValueError:
            pass

    row["position"] = "공고 원문 확인"
    for word in ("사회복지사", "제공인력", "활동지원사", "생활재활교사", "작업치료사", "물리치료사", "언어재활사", "간호사", "조리사"):
        if word in row["title"] or word in text[:1200]:
            row["position"] = word
            break
    row["id"] = hashlib.sha256((row["organization"] + "|" + row["url"]).encode("utf-8")).hexdigest()[:18]
    return row

def main():
    previous = {}
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}

    checked = previous.get("checkedCandidates", {})
    old_jobs = {job.get("url"): job for job in previous.get("jobs", []) if job.get("url")}
    all_rows, source_status = [], []

    # 매일 모든 기관의 게시판 목록만 확인하고, 신규·변경된 공고만 상세페이지를 내려받습니다.
    for source in CONFIG["sources"]:
        try:
            html = fetch(source["url"])
            soup = BeautifulSoup(html, "html.parser")
            if source.get("mode") == "detail":
                all_rows.append({
                    "organization": source["organization"],
                    "title": source.get("title", "채용공고"),
                    "url": source["url"],
                    "postedAt": "",
                    "source": source["url"]
                })
                source_status.append({
                    "organization": source["organization"], "url": source["url"],
                    "status": "ok", "itemsFound": 1
                })
                continue

            listing_pages = [source["url"]]
            if source.get("mode") == "discover":
                listing_pages = find_board_links(soup, source["url"])[:5]
            # 게시판의 다음 페이지도 확인해 최근 공고 누락을 줄입니다.
            page_limit = int(source.get("maxPages", 5))
            expanded_pages = []
            for listing in listing_pages:
                expanded_pages.append(listing)
                listing_html = html if listing == source["url"] else fetch(listing)
                page_soup = BeautifulSoup(listing_html, "html.parser")
                expanded_pages.extend(find_pagination_links(page_soup, listing, max(0, page_limit - 1)))
            listing_pages = list(dict.fromkeys(expanded_pages))[:page_limit]
            count = 0
            for listing in listing_pages:
                page_html = html if listing == source["url"] else fetch(listing)
                rows = extract_rows(
                    BeautifulSoup(page_html, "html.parser"), listing, source["organization"],
                    allow_external=bool(source.get("allowExternalLinks", False))
                )
                all_rows.extend(rows)
                count += len(rows)
                time.sleep(0.1)
            source_status.append({
                "organization": source["organization"], "url": source["url"],
                "status": "ok" if listing_pages else "board_not_found", "itemsFound": count
            })
        except Exception as exc:
            source_status.append({"organization": source["organization"], "url": source["url"], "status": "error", "message": str(exc)[:180]})
        time.sleep(0.1)

    unique_rows = {row["url"]: row for row in all_rows}
    active_jobs, checked_count, skipped_count = {}, 0, 0
    today = date.today()

    for url, row in unique_rows.items():
        fingerprint = hashlib.sha256(("parser-v5|" + row["title"] + "|" + url).encode("utf-8")).hexdigest()
        cached_job = old_jobs.get(url)
        cache_entry = checked.get(url)
        cache_fingerprint = cache_entry.get("fingerprint") if isinstance(cache_entry, dict) else cache_entry
        cache_date = cache_entry.get("checkedAt", "") if isinstance(cache_entry, dict) else ""
        cache_has_job = cache_entry.get("hasJob", False) if isinstance(cache_entry, dict) else bool(cached_job)
        recent_negative_cache = False
        if isinstance(cache_entry, dict) and cache_date and not cache_has_job:
            try:
                recent_negative_cache = (today - date.fromisoformat(cache_date)).days < 3
            except ValueError:
                recent_negative_cache = False
        cache_hit = cache_fingerprint == fingerprint and (cache_has_job or recent_negative_cache)
        if cache_hit:
            skipped_count += 1
            if cached_job:
                try:
                    if date.fromisoformat(cached_job["closeDate"]) >= today:
                        active_jobs[cached_job["id"]] = cached_job
                except (KeyError, ValueError):
                    pass
            continue

        try:
            job = enrich(row)
            checked[url] = {
                "fingerprint": fingerprint,
                "checkedAt": today.isoformat(),
                "hasJob": bool(job)
            }
            checked_count += 1
            if job:
                active_jobs[job["id"]] = job
        except requests.RequestException as exc:
            print("상세페이지 조회 실패:", url, str(exc)[:120])
        time.sleep(0.1)

    # 캐시가 과도하게 커지지 않도록 최근 2,000개 항목만 유지합니다.
    if len(checked) > 2000:
        checked = dict(list(checked.items())[-2000:])

    jobs = sorted(active_jobs.values(), key=lambda x: (x.get("postedAt", ""), x["organization"]), reverse=True)
    payload = {
        "lastChecked": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourcePolicy": CONFIG["policy"],
        "filterPolicy": "공식 기관 홈페이지 및 등록된 사회복지 전문 채용게시판의 원문에서 통합돌봄 관련 문구와 접수기간 또는 게시일과 명시된 마감일이 확인되고, 마감일이 지나지 않은 채용공고만 표시",
        "sourceCount": len(CONFIG["sources"]),
        "sources": source_status,
        "checkedCandidates": checked,
        "stats": {"candidateCount": len(unique_rows), "detailPagesFetched": checked_count, "cachedCandidatesSkipped": skipped_count, "rejectionReasons": dict(REJECTIONS)},
        "jobs": jobs
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("기관 홈페이지", len(CONFIG["sources"]), "곳 확인 / 신규·변경 상세페이지", checked_count, "건 / 캐시 활용", skipped_count, "건 / 유효 공고", len(jobs), "건")
    print("공고 제외 사유:", json.dumps(dict(REJECTIONS), ensure_ascii=False))
    print("기관별 확인 결과:", json.dumps(source_status, ensure_ascii=False))

if __name__ == "__main__":
    main()
