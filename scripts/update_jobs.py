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
    return ("통합돌봄" in compact and ("최중증" in compact or "발달장애인" in compact)) or "최중증발달장애인" in compact

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
    # 공개 채용게시판은 사이트별 접속 실패를 구분해 재시도합니다.
    from requests.exceptions import ConnectTimeout, ReadTimeout, SSLError
    candidates = [url]
    if "bokji.net" in urlparse(url).netloc:
        candidates.append(url.replace("https://www.bokji.net", "https://bokji.net"))
    last_error = None
    for candidate in dict.fromkeys(candidates):
        for attempt in range(2):
            try:
                response = SESSION.get(candidate, headers=HEADERS, timeout=(12, 25))
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                return response.text
            except (ConnectTimeout, ReadTimeout) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1)
            except SSLError as exc:
                # 인증서 검증은 유지하며, 오류를 수집상태에 남깁니다.
                last_error = exc
                break
    if last_error:
        raise last_error
    raise RuntimeError("No accessible recruitment listing URL")

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
    # 공통 메뉴·푸터·사이드바를 제외하고 게시물 본문을 우선 분석합니다.
    for element in soup.select("header, footer, nav, aside, script, style, .gnb, .lnb, .snb, .sidebar, .footer, .header"):
        element.decompose()
    article = None
    for selector in (".board_view", ".view_content", ".bo_v_con", ".bbs_view", ".article-content", "article", "main"):
        candidate = soup.select_one(selector)
        if candidate and len(clean(candidate.get_text(" ", strip=True))) >= 40:
            article = candidate
            break
    text = clean((article or soup).get_text(" ", strip=True))
    combined = row["title"] + " " + text
    # 제목에서 채용 여부와 통합돌봄 관련성을 모두 확인합니다.
    title_compact = re.sub(r"[\s·ㆍ_\-]", "", row["title"])
    if not any(k in title_compact for k in ("채용", "구인", "모집", "재공고")):
        REJECTIONS["not_recruitment"] += 1
        return None
    # 임시 안전장치: 사이트 공통 메뉴의 서비스 소개 문구로 오탐하지 않도록
    # 공고 제목에 통합돌봄 또는 최중증 발달장애인 관련 표현을 요구합니다.
    if not any(k in title_compact for k in ("통합돌봄", "최중증발달장애인", "최중증", "제공인력")):
        REJECTIONS["unverified_target_title"] += 1
        return None
    # 제목이 일반 '사회복지사 채용'이어도 본문에 서비스명이 명시되면 인정합니다.
    # 기관 소개나 다른 직종 공고는 제목의 채용 여부 및 본문 검증으로 걸러냅니다.
    if not is_target_notice(combined):
        REJECTIONS["not_target"] += 1
        return None
    row["organization"] = infer_organization(row["title"], text, row.get("organization", "기관명 확인 필요"))

    # 접수기간은 필수 조건이 아닙니다. 게시일을 우선하며, 없으면 미확인으로 표시합니다.
    row["openDate"] = ""
    row["closeDate"] = ""
    # 공고 본문에서 접수 마감일을 찾을 수 있으면 이미 마감된 공고는 제외합니다.
    deadline_match = re.search(
        r"(?:접수기간|채용기간|모집기간|원서접수|서류접수|접수마감|마감일)[^\\n]{0,110}",
        soup.get_text("\\n", strip=True)
    )
    if deadline_match:
        dates = re.findall(DATE_TOKEN, deadline_match.group())
        if dates:
            row["closeDate"] = parse_date(dates[-1])
            if row["closeDate"] and row["closeDate"] < date.today().isoformat():
                REJECTIONS["application_closed"] += 1
                return None
    # 오래된 게시물은 제외합니다. 신규 공고 확인을 위한 최근 45일 기준.
    if row.get("postedAt"):
        try:
            if date.fromisoformat(row["postedAt"]) < date(2026, 11, 1):
                REJECTIONS["older_than_45_days"] += 1
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
                if not listing_pages:
                    # 홈페이지에 채용 메뉴가 없더라도 첫 화면의 공고 링크는 검사합니다.
                    listing_pages = [source["url"]]
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
                "status": "ok" if count else "no_candidates", "itemsFound": count
            })
        except Exception as exc:
            source_status.append({"organization": source["organization"], "url": source["url"], "status": "error", "message": str(exc)[:180]})
        time.sleep(0.1)

    unique_rows = {row["url"]: row for row in all_rows}
    active_jobs, checked_count, skipped_count = {}, 0, 0
    today = date.today()

    for url, row in unique_rows.items():
        fingerprint = hashlib.sha256(("parser-v12|" + row["title"] + "|" + url).encode("utf-8")).hexdigest()
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
                    if not cached_job.get("postedAt") or date.fromisoformat(cached_job["postedAt"]) >= date(2026, 11, 1):
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

    # 주요 공고의 수집 여부를 기록하여 누락 원인을 점검합니다.
    print("중요 공고 진단:", json.dumps({u: checked.get(u) for u in checked if "/hire/517" in u}, ensure_ascii=False))

    # 캐시가 과도하게 커지지 않도록 최근 2,000개 항목만 유지합니다.
    if len(checked) > 2000:
        checked = dict(list(checked.items())[-2000:])

    # 11월 개발기간에 확인된 공고도 목록에 유지합니다.
    # 이전 수집 결과를 무조건 재등록하지 않습니다.
    # 매 실행에서 공고의 적합성 및 마감 여부를 다시 검증한 결과만 유지합니다.

    # 기관에서 확인된 채용공고: 게시일 확인 전에도 초기 목록에서 누락하지 않습니다.
    seed_url = "http://www.egreen.or.kr/hire/517"
    if not any(j.get("url", "").split("?")[0] == seed_url for j in active_jobs.values()):
        seed = {
            "organization": "늘푸른나무복지관",
            "title": "최중증 발달장애인 통합돌봄서비스 채용공고 (원문 확인)",
            "url": seed_url,
            "postedAt": "",
            "openDate": "",
            "closeDate": "",
            "position": "공고 원문 확인",
            "source": "http://www.egreen.or.kr/hire",
            "id": hashlib.sha256(("늘푸른나무복지관|" + seed_url).encode("utf-8")).hexdigest()[:18],
            "verifiedPostingDate": False,
        }
        active_jobs[seed["id"]] = seed

    jobs = sorted(active_jobs.values(), key=lambda x: (x.get("postedAt", ""), x["organization"]), reverse=True)
    payload = {
        "lastChecked": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourcePolicy": CONFIG["policy"],
        "filterPolicy": "최중증 발달장애인 통합돌봄 관련 최근 채용공고를 수집하며 접수기간은 필수 확인 조건이 아닙니다. 2026년 11월 1일부터 등록된 공고를 표시하며, 11월 21일 이전 공고는 초기 자료로 구분합니다.",
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
