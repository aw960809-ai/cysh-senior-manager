#!/usr/bin/env python3
"""Background opportunity updater for the CYSH senior self-management system.

Designed for GitHub Actions. It fetches public official pages, extracts activities /
scholarships, normalizes and de-duplicates records, retires expired/stale items, and
writes data/opportunities.json. Network/source failure never deletes cached records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "opportunities.json"
TZ8 = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ8).date()
NOW = datetime.now(TZ8).isoformat(timespec="seconds")

ACTIVITY_RE = re.compile(r"活動|競賽|比賽|徵件|營隊|研習|講座|工作坊|志工|旅行|奧林匹亞|提案|交流|參訪|體驗|計畫|培訓|論壇|科展|創作|說明會", re.I)
SCHOLAR_RE = re.compile(r"獎學金|獎助學金|助學金|補助|獎勵金|就學金|圓夢計畫|扶助", re.I)
EXCLUDE_RE = re.compile(r"教師甄選|教師課表|班級課表|教科書|健康檢查|疫苗|服裝儀容|採購|會計|人事|公務員|得獎名單|錄取名單", re.I)
HIGHER_ED_ONLY_RE = re.compile(r"限?大專|大專校院學生|大學生限定|研究生限定|大專女學生", re.I)

SOURCES = [
    {"id": "cysh-external", "name": "嘉義高中｜校園訊息", "url": "https://www.cysh.cy.edu.tw/p/403-1008-12-1.php?Lang=zh-tw", "kind": "cysh", "region": "校內"},
    {"id": "cysh-scholarship", "name": "嘉義高中｜獎助學金", "url": "https://www.cysh.cy.edu.tw/p/403-1008-18-1.php?Lang=zh-tw", "kind": "cysh", "region": "校內"},
    {"id": "moe-scholarship", "name": "教育部｜獎學金公告資訊網", "url": "https://www.edu.tw/scholarshipinfo/Default.aspx", "kind": "moe", "region": "全國"},
    {"id": "youthfirst", "name": "青年發展署｜青年第一讚", "url": "https://youthfirst.yda.gov.tw/index.php/subject/index/0/1", "kind": "youth", "region": "全國"},
]

UA = "Mozilla/5.0 (compatible; CYSH-Senior-OpportunityBot/1.0; public-information-monitor)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6"})


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" \t\r\n-–—｜|")


def norm_title(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", clean(s).lower(), flags=re.UNICODE)


def stable_id(source_id: str, url: str, title: str) -> str:
    base = f"{source_id}|{url}|{norm_title(title)}".encode("utf-8")
    return "opp_" + hashlib.sha1(base).hexdigest()[:16]


def fetch(url: str, timeout: int = 22) -> str:
    r = SESSION.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def fetch_reader(url: str, timeout: int = 25, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            r = SESSION.get("https://r.jina.ai/" + url, timeout=timeout, allow_redirects=True, headers={"Accept": "text/plain"})
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else min(8.0, 2.2 * (2 ** i))
                if i < tries - 1:
                    time.sleep(wait)
                    continue
            r.raise_for_status()
            r.encoding = r.apparent_encoding or r.encoding
            return r.text
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(0.7 + 0.6 * i)
    raise last


def soup_lines(soup: BeautifulSoup) -> str:
    return "\n".join(clean(x) for x in soup.get_text("\n", strip=True).splitlines() if clean(x))


def roc_to_ad(y: int) -> int:
    return y + 1911 if y < 1911 else y


def parse_date_candidates(text: str, reference: date | None = None) -> list[tuple[date, int, int]]:
    reference = reference or TODAY
    out: list[tuple[date, int, int]] = []
    patterns = [
        re.compile(r"(?P<y>20\d{2}|1\d{2})\s*[年./-]\s*(?P<m>\d{1,2})\s*[月./-]\s*(?P<d>\d{1,2})\s*日?"),
        re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*[月/]\s*(?P<d>\d{1,2})\s*日?"),
    ]
    for idx, pat in enumerate(patterns):
        for m in pat.finditer(text):
            try:
                if "y" in m.groupdict() and m.group("y"):
                    y = roc_to_ad(int(m.group("y")))
                else:
                    y = reference.year
                    candidate = date(y, int(m.group("m")), int(m.group("d")))
                    if candidate < reference - timedelta(days=120):
                        y += 1
                dt = date(y, int(m.group("m")), int(m.group("d")))
                if reference - timedelta(days=400) <= dt <= reference + timedelta(days=900):
                    out.append((dt, m.start(), m.end()))
            except ValueError:
                continue
    return sorted(set(out), key=lambda x: (x[1], x[0]))


def extract_deadline(text: str, reference: date | None = None) -> str | None:
    reference = reference or TODAY
    segments = [clean(x) for x in re.split(r"[\n。；]+", text or "") if clean(x)]
    strong_re = re.compile(r"截止|期限|最後(?:收件|報名|申請|繳交|送件)|申請(?:時間|期間|日期)|報名(?:時間|期間|日期)|收件(?:時間|期間|日期)|受理(?:時間|期間|日期)|繳交(?:時間|期限)|送件(?:時間|期限)|校內(?:收件|截止)|前(?:完成|提出|繳交|送交|送達|報名|申請)|至.{0,36}(?:止|截止|前)")
    weak_re = re.compile(r"報名|申請|收件|受理|繳交|送件|提出申請|填表")
    negative_re = re.compile(r"活動(?:時間|日期)|比賽(?:日期|時間)|參訪(?:日期|時間)|辦理日期|研習日期|決賽日期|初賽日期")
    scored: list[tuple[int, date]] = []
    for seg in segments:
        strong = bool(strong_re.search(seg))
        if (not strong and not weak_re.search(seg)) or (negative_re.search(seg) and not re.search(r"截止|報名|申請", seg)):
            continue
        cands = sorted({d for d, _, _ in parse_date_candidates(seg, reference)})
        for idx, dt in enumerate(cands):
            score = 115 if strong else 50
            if dt >= reference: score += 22
            if dt <= reference + timedelta(days=550): score += 5
            if len(cands) > 1 and idx == len(cands)-1: score += 25
            if len(cands) > 1 and idx == 0 and re.search(r"開始|起|自|即日起", seg): score -= 20
            if idx == len(cands)-1 and re.search(r"前|止|截止", seg): score += 12
            scored.append((score, dt))
    if not scored:
        compact = clean(text)
        m = re.search(r"(?:截止|期限|申請時間|報名時間|收件時間|受理時間).{0,90}", compact)
        if m:
            cands = sorted({d for d,_,_ in parse_date_candidates(m.group(0), reference)})
            if cands: return cands[-1].isoformat()
        return None
    scored.sort(key=lambda x:(x[0],x[1]), reverse=True)
    return scored[0][1].isoformat()

def extract_scholarship_deadline(text: str, reference: date | None = None) -> str | None:
    reference = reference or TODAY
    base = extract_deadline(text, reference)
    if base:
        return base
    lines = [clean(x) for x in re.split(r"[\n。；]+", text or "") if clean(x)]
    cues = re.compile(r"請.*?前.*?(?:申請|繳交|送交|送件|提出)|受理申請書期限|校內.*?(?:收件|申請|送件)|申請期限|收件截止|截止日期|逾期不受理")
    for line in lines:
        if not cues.search(line):
            continue
        cands = sorted({d for d,_,_ in parse_date_candidates(line, reference)})
        if cands:
            return cands[-1].isoformat()
    return None


def _normalize_digits(value: str) -> str:
    fw = "０１２３４５６７８９"
    return "".join(str(fw.index(ch)) if ch in fw else ch for ch in str(value or ""))


def money_values_in_line(line: str) -> list[int]:
    s = _normalize_digits(line).replace(",", "").replace("，", "")
    vals: set[int] = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*萬(?:\s*(\d+(?:\.\d+)?)\s*[千仟])?\s*元?", s):
        v = round(float(m.group(1))*10000 + (float(m.group(2))*1000 if m.group(2) else 0))
        if v > 0: vals.add(v)
    for m in re.finditer(r"(?<!萬)(\d+(?:\.\d+)?)\s*[千仟]\s*元", s):
        v = round(float(m.group(1))*1000)
        if v > 0: vals.add(v)
    for m in re.finditer(r"(?<![萬千仟\d])(\d{3,7})\s*元", s):
        v = int(m.group(1))
        if 500 <= v <= 10_000_000: vals.add(v)
    return sorted(vals)


def format_ntd(v: int) -> str:
    return f"NT${v:,}"


def extract_scholarship_amount(text: str) -> str:
    lines = [clean(x) for x in re.split(r"[\n。；]+", text or "") if clean(x)]
    keys = re.compile(r"獎學金|獎助學金|助學金|補助|獎勵金|每名|每人|每生|支給數額|發放|核發|金額|額度")
    rows: list[tuple[int, str, list[int]]] = []
    for line in lines:
        if len(line) > 520 or not keys.search(line):
            continue
        vals = money_values_in_line(line)
        if not vals:
            continue
        score = 40 + (30 if re.search(r"每名|每人|每生", line) else 0) + (25 if re.search(r"獎學金|獎助學金|助學金", line) else 0) + (12 if re.search(r"高中|本校學生|學生", line) else 0) - (8 if re.search(r"大學|大專", line) and not re.search(r"高中", line) else 0)
        rows.append((score, line, vals))
    if not rows:
        vals = money_values_in_line(text)
        return format_ntd(vals[0]) if len(vals) == 1 else "依官方簡章"
    rows.sort(key=lambda x:x[0], reverse=True)
    top = rows[:8]
    vals = sorted({v for _,_,vs in top for v in vs})
    if len(vals) == 1:
        suffix = "／名" if re.search(r"每名|每人|每生", top[0][1]) else ""
        return format_ntd(vals[0]) + suffix
    if len(vals) > 1:
        return f"多項獎助｜{format_ntd(vals[0])}～{format_ntd(vals[-1])}（依項目）"
    return top[0][1][:260]


def extract_amount(text: str) -> str:
    return extract_scholarship_amount(text)


def extract_eligibility(text: str) -> str:
    lines=[clean(x) for x in re.split(r"[\n。；]+", text or "") if clean(x)]
    patterns=[re.compile(r"(?:參加|申請|報名|參與|補助)(?:對象|資格|條件)[:：]?"),re.compile(r"(?:申請人|申請學生|參加者|參賽者)(?:須|應|需|資格|條件)"),re.compile(r"資格條件|申請條件|符合下列條件")]
    for pat in patterns:
        for i,line in enumerate(lines):
            if pat.search(line) and len(line)<420:
                parts=[line]
                if len(line)<90 and i+1<len(lines) and re.search(r"^[（(一二三四五六七八九十\d]|高中|本校|學生|年齡|設籍|成績|家境|清寒",lines[i+1]): parts.append(lines[i+1])
                return "；".join(parts)[:320]
    for line in lines:
        if re.search(r"(?:凡|限|須為|應為).{0,35}(?:本校學生|高中職|高中生|高級中等|國高中|中學生|青少年|在學學生)|本校在學學生",line): return line[:300]
    if HIGHER_ED_ONLY_RE.search(clean(text)): return "疑似大專限定，系統將降低高三學生適配度；請查看官方來源"
    return "資格待確認；請查看官方來源"


def extract_scholarship_eligibility(text: str) -> str:
    base = extract_eligibility(text)
    if parsed_eligibility(base):
        return base
    lines = [clean(x) for x in re.split(r"[\n。；]+", text or "") if clean(x)]
    criteria = re.compile(r"本校學生|在學學生|高中(?:職)?|高級中等|年級|設籍|戶籍|低收入|中低收入|清寒|家境|家庭(?:年)?收入|單親|無父母|特殊境遇|原住民|身心障礙|身障|資優|成績|學業成績|平均成績|德行|操行|獎懲|班級前三名|偏鄉|癌友家庭|家庭貧困|經濟弱勢|弱勢學生|申請資格|申請條件|申請對象|補助對象|符合.*條件")
    noise = re.compile(r"申請書|下載|附件|櫃台|逾期|申請期限|截止|電話|網址|瀏覽數|作者|發佈日期")
    picked=[]
    for line in lines:
        if len(line)>360 or noise.search(line) or not criteria.search(line):
            continue
        if "不得重複請領" in line and not picked:
            continue
        picked.append(re.sub(r"^[*#\-•·\s]+", "", line))
        if len(picked)>=3:
            break
    if picked:
        return ("資格摘要｜" + "；".join(picked))[:420]
    if re.search(r"公教人員子女|同仁子女|教職員工子女|子女教育補助", clean(text)):
        return "資格摘要｜公教人員／教職員工子女相關補助，非一般學生自行申請；請確認家庭身分"
    return base


def extract_location(text: str) -> str:
    lines=[clean(x) for x in re.split(r"[\n。；]+", text or "") if clean(x)]
    for line in lines:
        m=re.search(r"(?:活動地點|參訪地點|比賽地點|競賽地點|研習地點|辦理地點|集合地點|場地|地點)[:：]\s*([^。；]{2,180})",line)
        if m:return clean(m.group(1))[:160]
    for line in lines:
        m=re.search(r"(?:於|假)\s*([^，。；]{2,100}?)(?:舉辦|辦理|進行|集合)",line)
        if m:return clean(m.group(1))[:160]
    return "詳官方公告"

def extract_event_date(text: str, reference: date | None = None) -> str:
    reference=reference or TODAY
    keys=re.compile(r"活動時間|參訪時間|比賽日期|競賽日期|研習日期|辦理日期|活動日期|考試日期|決賽日期|初賽日期|舉辦日期|行程日期|出發日期|活動期程|辦理時間|時間")
    best=None
    for seg in [clean(x) for x in re.split(r"[\n。；]+",text or "") if clean(x)]:
        if not keys.search(seg) or re.search(r"申請|報名|截止|期限|收件|受理",seg):continue
        ds=sorted({d for d,_,_ in parse_date_candidates(seg,reference)})
        if not ds:continue
        value=f"{ds[0].isoformat()}～{ds[-1].isoformat()}" if len(ds)>=2 else ds[0].isoformat()
        score=50+(30 if re.search(r"活動|參訪|比賽|競賽|研習|辦理|行程|出發",seg) else 0)+(10 if ds[0]>=reference else 0)
        if best is None or score>best[0]:best=(score,value)
    return best[1] if best else "詳官方公告"

def extract_organizer(text: str, fallback: str) -> str:
    text = clean(text)
    m = re.search(r"(?:主辦單位|承辦單位|辦理單位|提供單位|指導單位)[:：]\s*([^。；]{2,120})", text)
    return clean(m.group(1))[:100] if m else fallback


def extract_attachment_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str,str,int]]:
    out=[];seen=set()
    for a in soup.find_all("a",href=True):
        name=clean(a.get_text(" ",strip=True));url=urljoin(base_url,a.get("href") or "")
        if not url or url in seen:continue
        ext=Path(urlparse(url).path).suffix.lower()
        priority=(40 if re.search(r"簡章|要點|辦法|公告|計畫|申請|獎學金|補助|規定",name) else 0)+(30 if ext==".pdf" else 18 if ext in {".doc",".docx",".odt"} else 0)
        if priority<18:continue
        seen.add(url);out.append((name,url,priority))
    return sorted(out,key=lambda x:x[2],reverse=True)[:6]


def deep_fields(text: str, kind: str, reference: date | None = None) -> dict:
    return {"deadline":extract_scholarship_deadline(text,reference) if kind=="scholarship" else extract_deadline(text,reference),"eligibility":extract_scholarship_eligibility(text) if kind=="scholarship" else extract_eligibility(text),"amount":extract_scholarship_amount(text) if kind=="scholarship" else None,"date":extract_event_date(text,reference) if kind=="activity" else "—","location":extract_location(text)}


def needs_deep_attachment(fields: dict, kind: str) -> bool:
    return (not fields.get("deadline") or not parsed_eligibility(fields.get("eligibility", "")) or (kind=="scholarship" and fields.get("amount") in (None,"依官方簡章")) or (kind=="activity" and (fields.get("date")=="詳官方公告" or fields.get("location")=="詳官方公告")))

def parsed_eligibility(value: str) -> bool:
    return bool(value and "待確認" not in value and "查看官方" not in value)


def infer_type(title: str, body: str = "") -> str | None:
    s = f"{title} {body[:1000]}"
    if SCHOLAR_RE.search(s):
        return "scholarship"
    if ACTIVITY_RE.search(s):
        return "activity"
    return None


def eligibility_decision(item: dict) -> str:
    text = clean(" ".join(str(item.get(k, "")) for k in ("title", "eligibility", "reason")))
    if re.search(r"公教人員子女|同仁子女|教職員工子女|子女教育補助", text) and not re.search(r"學生(?:本人)?可申請|本校學生可申請", text):
        return "reject"
    if HIGHER_ED_ONLY_RE.search(text) and not re.search(r"高中|高中職|高級中等|國高中", text):
        return "reject"
    if re.search(r"本校學生|高中生|高中職|高級中等|國高中|中學生|青少年", text):
        return "pass"
    return "review"


def estimate_raw_announcements(html: str) -> tuple[int, int]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    raw = excluded = 0
    nav_words = re.compile(r"首頁|網站導覽|回上一頁|更多|English|登入|搜尋|最新消息$|公告$", re.I)
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 5 or nav_words.fullmatch(title):
            continue
        url = urljoin("https://diagnostic.invalid/", a.get("href"))
        key = f"{norm_title(title)}|{url}"
        if key in seen:
            continue
        seen.add(key)
        raw += 1
        if EXCLUDE_RE.search(title):
            excluded += 1
    return raw, excluded


def filter_and_diagnose(html: str, items: list[dict]) -> tuple[list[dict], dict]:
    raw_count, excluded_keyword = estimate_raw_announcements(html)
    diag = {
        "rawCount": raw_count,
        "identifiedCount": len(items),
        "eligibilityPass": 0,
        "eligibilityReview": 0,
        "eligibilityRejected": 0,
        "unexpiredCount": 0,
        "expiredCount": 0,
        "noDeadlineCount": 0,
        "finalCount": 0,
        "excludedKeyword": excluded_keyword,
        "notOpportunity": max(0, raw_count - len(items) - excluded_keyword),
        "parseErrors": 0,
    }
    final: list[dict] = []
    for item in items:
        decision = eligibility_decision(item)
        if decision == "reject":
            diag["eligibilityRejected"] += 1
            continue
        diag["eligibilityPass" if decision == "pass" else "eligibilityReview"] += 1
        deadline = item.get("deadline")
        if not deadline:
            diag["noDeadlineCount"] += 1
        else:
            try:
                if date.fromisoformat(deadline) < TODAY:
                    diag["expiredCount"] += 1
                    continue
                diag["unexpiredCount"] += 1
            except ValueError:
                diag["noDeadlineCount"] += 1
        final.append(item)
    diag["finalCount"] = len(final)
    return final, diag


def infer_goal(text: str, typ: str) -> str:
    if typ == "scholarship":
        if re.search(r"海外|國際|留學|交換", text):
            return "海外／國際資源支持"
        if re.search(r"清寒|弱勢|經濟", text):
            return "就學經濟支持"
        return "就學經濟支持／學業獎勵"
    if re.search(r"海外|國際|日本|交流|旅行|度假打工", text):
        return "國際／海外經驗"
    if re.search(r"競賽|科展|創作|提案|徵件", text):
        return "競賽／自主表達／學習歷程"
    if re.search(r"升學|生涯|職涯|大學", text):
        return "升學／生涯探索"
    return "自主學習／活動經驗"


def infer_region(text: str, default: str) -> str:
    if re.search(r"本校|嘉義高中|嘉中", text):
        return "校內"
    if re.search(r"嘉義市", text):
        return "嘉義市"
    if re.search(r"海外|國際|日本|韓國|美國|歐洲|澳洲|紐西蘭", text):
        return "海外"
    return default


def scores_for(item: dict) -> dict:
    text = " ".join(str(item.get(k, "")) for k in ("title", "eligibility", "goal", "reason"))
    eligibility = 30
    if HIGHER_ED_ONLY_RE.search(text):
        eligibility = 2
    elif re.search(r"資格待確認|家庭|清寒|特殊身分|資優", text):
        eligibility = 12
    elif re.search(r"高中職|高中生|高級中等|本校學生", text):
        eligibility = 30
    goal = 20 if item.get("type") == "activity" else 25
    if re.search(r"升學|學習|競賽|國際|就學", text):
        goal += 3
    deadline = item.get("deadline")
    timing = 12
    if deadline:
        try:
            d = date.fromisoformat(deadline)
            days = (d - TODAY).days
            timing = 18 if 7 <= days <= 60 else 12 if days > 60 else 8 if days >= 0 else 0
        except ValueError:
            pass
    distance = {"校內": 10, "嘉義市": 9, "區域": 7, "全國": 6, "海外": 4}.get(item.get("region"), 6)
    cost = 8
    if re.search(r"費用|自費|NT\$|元", text) and item.get("type") == "activity":
        cost = 5
    return {"資格": eligibility, "目標": min(goal, 30), "時程": timing, "距離": distance, "成本": cost}


def make_item(*, source: dict, title: str, url: str, body: str, published: str | None = None) -> dict | None:
    title = clean(title)
    if len(title) < 5 or EXCLUDE_RE.search(title):
        return None
    typ = infer_type(title, body)
    if not typ:
        return None
    ref = TODAY
    if published:
        try:
            ref = date.fromisoformat(published)
        except ValueError:
            pass
    deadline = extract_scholarship_deadline(body, ref) if typ == "scholarship" else extract_deadline(body, ref)
    fallback_org = source["name"].split("｜", 1)[0]
    item = {
        "id": stable_id(source["id"], url, title),
        "type": typ,
        "title": title,
        "org": extract_organizer(body, fallback_org),
        "region": infer_region(title + " " + body, source.get("region", "全國")),
        "deadline": deadline,
        "date": extract_event_date(body, ref) if typ == "activity" else "—",
        "location": extract_location(body),
        "amount": extract_scholarship_amount(body) if typ == "scholarship" else None,
        "cost": "依官方公告" if typ == "activity" else None,
        "eligibility": extract_scholarship_eligibility(body) if typ == "scholarship" else extract_eligibility(body),
        "goal": infer_goal(title + " " + body, typ),
        "reason": "已解析官方詳細公告；資格、期限與內容仍以原始公告為準。" if body and body != title else "由官方列表取得；詳細內容待確認。",
        "url": url,
        "sourceUrl": url,
        "sourceId": source["id"],
        "sourceName": source["name"],
        "published": published,
        "lastSeen": NOW,
        "updatedAt": NOW,
        "missCount": 0,
        "needsReview": False,
        "detailParsed": bool(body and body != title),
        "deadlineConfidence": "detail" if deadline and body and body != title else "list-or-title",
    }
    item["scores"] = scores_for(item)
    return item


def find_published(text: str) -> str | None:
    c = parse_date_candidates(text)
    if not c:
        return None
    past = [d for d, _, _ in c if d <= TODAY + timedelta(days=2)]
    return (max(past) if past else c[0][0]).isoformat()


def parse_link_source(source: dict, html: str, detail_limit: int = 16) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[str, str, str | None]] = []
    seen_urls: set[str] = set()
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if not title or EXCLUDE_RE.search(title) or not (ACTIVITY_RE.search(title) or SCHOLAR_RE.search(title)):
            continue
        url = urljoin(source["url"], a.get("href"))
        if url in seen_urls or urlparse(url).scheme not in {"http", "https"}:
            continue
        seen_urls.add(url)
        parent_text = clean(a.parent.get_text(" ", strip=True) if a.parent else title)
        candidates.append((title, url, find_published(parent_text)))
    # Most official lists place newest first.
    out: list[dict] = []
    for title, url, published in candidates[:detail_limit]:
        try:
            body_html = fetch(url, 15)
            body = clean(BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True))
        except Exception:
            body = title
        item = make_item(source=source, title=title, url=url, body=body, published=published)
        if item:
            out.append(item)
    return out



def parse_cysh(source: dict, html: str, detail_limit: int = 24) -> tuple[list[dict], dict]:
    """Parse CYSH category pages, detail bodies and selected attachments."""
    soup=BeautifulSoup(html,"html.parser");is_scholar=source["id"]=="cysh-scholarship";raw_posts=[];seen=set();excluded=0
    for a in soup.find_all("a",href=True):
        url=urljoin(source["url"],a.get("href") or "")
        if "/p/406-1008-" not in url:continue
        title=clean(a.get_text(" ",strip=True))
        if len(title)<4:continue
        key=f"{norm_title(title)}|{url}"
        if key in seen:continue
        seen.add(key);parent_text=clean(a.parent.get_text(" ",strip=True) if a.parent else title);published=find_published(parent_text);raw_posts.append((title,url,published));excluded+=1 if EXCLUDE_RE.search(title) else 0
    identified=[x for x in raw_posts if not EXCLUDE_RE.search(x[0]) and (is_scholar or infer_type(x[0],""))]
    out=[];parse_errors=detail_fetched=detail_unavailable=0;deadline_parsed=event_date_parsed=eligibility_parsed=location_parsed=organizer_parsed=amount_parsed=0;attachment_links=attachment_fetched=attachment_used=attachment_errors=0;attachment_budget=10 if is_scholar else 6
    for title,url,published in identified[:detail_limit]:
        detail_html="";body=title;detail_ok=False
        try:
            detail_html=fetch(url,18);detail_soup=BeautifulSoup(detail_html,"html.parser");body=soup_lines(detail_soup);detail_fetched+=1;detail_ok=True
        except Exception:
            parse_errors+=1;detail_unavailable+=1;detail_soup=None
        kind="scholarship" if is_scholar else infer_type(title,body)
        if not kind:continue
        fields=deep_fields(title+"\n"+body,kind,date.fromisoformat(published) if published else TODAY);used=[]
        attachments=extract_attachment_links(detail_soup,url) if detail_soup else [];attachment_links+=len(attachments)
        if attachments and needs_deep_attachment(fields,kind) and attachment_budget>0:
            deep=title+"\n"+body
            for name,att_url,_ in attachments[:2]:
                if attachment_budget<=0:break
                attachment_budget-=1
                try:
                    att_text=fetch_reader(att_url,25);attachment_fetched+=1
                    if len(att_text)>40:
                        deep=(deep+"\n附件｜"+name+"\n"+att_text)[:120000];used.append(name);fields=deep_fields(deep,kind,date.fromisoformat(published) if published else TODAY)
                        if not needs_deep_attachment(fields,kind):break
                except Exception:attachment_errors+=1
            if used:attachment_used+=1;body=deep
        fallback="國立嘉義高級中學獎助學金公告／轉知" if is_scholar else "國立嘉義高級中學公告／轉知"
        org=extract_organizer(body,fallback)
        item={"id":stable_id(source["id"],url,title),"type":kind,"title":title,"org":org,"region":infer_region(title+" "+body),"deadline":fields["deadline"],"eligibility":fields["eligibility"],"goal":infer_goal(title+" "+body,kind),"reason":"已解析嘉中公告正文與附件；實際資格與期限仍以官方文件為準。" if used else ("已解析嘉中詳細公告；系統已抽取可辨識欄位。" if detail_ok else "嘉中詳細頁暫未取得，保留待確認。"),"url":url,"sourceUrl":url,"sourceId":source["id"],"sourceName":source["name"],"published":published,"firstSeen":NOW,"lastSeen":NOW,"updatedAt":NOW,"missCount":0,"detailParsed":detail_ok,"attachmentParsed":bool(used),"attachmentNames":used,"deadlineConfidence":"attachment" if fields["deadline"] and used else "detail" if fields["deadline"] and detail_ok else "unknown"}
        if kind=="scholarship":item["amount"]=fields["amount"]
        else:item["date"]=fields["date"];item["location"]=fields["location"];item["cost"]="費用待確認"
        item["scores"]=scores_for(item)
        if item.get("deadline"):deadline_parsed+=1
        if kind=="activity" and item.get("date") not in (None,"詳官方公告","—"):event_date_parsed+=1
        if parsed_eligibility(item.get("eligibility", "")):eligibility_parsed+=1
        if kind=="activity" and item.get("location") not in (None,"詳官方公告"):location_parsed+=1
        if org!=fallback:organizer_parsed+=1
        if kind=="scholarship" and item.get("amount") not in (None,"依官方簡章"):amount_parsed+=1
        out.append(item)
    diagnostics={"rawCount":len(raw_posts),"identifiedCount":len(identified),"eligibilityPass":0,"eligibilityReview":0,"eligibilityRejected":0,"unexpiredCount":0,"expiredCount":0,"noDeadlineCount":0,"finalCount":0,"excludedKeyword":excluded,"notOpportunity":max(0,len(raw_posts)-len(identified)-excluded),"parseErrors":parse_errors,"parserVersion":5,"detailFetched":detail_fetched,"detailUnavailable":detail_unavailable,"detailFetchErrors":parse_errors,"deadlineParsed":deadline_parsed,"eventDateParsed":event_date_parsed,"eligibilityParsed":eligibility_parsed,"locationParsed":location_parsed,"organizerParsed":organizer_parsed,"amountParsed":amount_parsed,"attachmentLinks":attachment_links,"attachmentFetched":attachment_fetched,"attachmentUsed":attachment_used,"attachmentErrors":attachment_errors}
    final=[]
    for item in out:
        decision=eligibility_decision(item)
        if decision=="reject":diagnostics["eligibilityRejected"]+=1;continue
        diagnostics["eligibilityPass" if decision=="pass" else "eligibilityReview"]+=1;dl=item.get("deadline")
        if not dl:diagnostics["noDeadlineCount"]+=1
        else:
            try:
                if date.fromisoformat(dl)<TODAY:diagnostics["expiredCount"]+=1;continue
                diagnostics["unexpiredCount"]+=1
            except ValueError:diagnostics["noDeadlineCount"]+=1
        final.append(item)
    diagnostics["finalCount"]=len(final);return final,diagnostics

def parse_moe(source: dict, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" ", strip=True))
    out = parse_link_source(source, html, detail_limit=12)
    # Ensure the annual general application notice is captured even when link parsing changes.
    m = re.search(r"(1\d{2})學年度獎學金自[^。]{0,180}", text)
    if m:
        roc = int(m.group(1))
        title = f"教育部接受捐助獎學基金會{roc}學年度獎學金"
        item = make_item(source=source, title=title, url=source["url"], body=m.group(0), published=None)
        if item:
            out.append(item)
    return out


def parse_youthfirst(source: dict, html: str) -> tuple[list[dict], dict]:
    soup=BeautifulSoup(html,"html.parser");text=soup_lines(soup);lines=[clean(x) for x in text.splitlines() if clean(x)];out=[];malformed=excluded=0
    for i,line in enumerate(lines):
        if "申請期限" not in line:continue
        m=re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\s*(?:[-–—~～至]\s*(20\d{2})[./-](\d{1,2})[./-](\d{1,2}))?",line)
        if not m:malformed+=1;continue
        deadline=date(int(m.group(4) or m.group(1)),int(m.group(5) or m.group(2)),int(m.group(6) or m.group(3))).isoformat();prev=[x for x in lines[max(0,i-6):i] if x!="Image" and not x.isdigit() and not re.search(r"^顯示\d*筆|^字級",x)];title=org=""
        for v in reversed(prev):
            if re.fullmatch(r"獎補助金|競賽|活動|平台|津貼|貸款|不限制",v):continue
            if not org:org=v;continue
            title=v;break
        if not title:malformed+=1;continue
        if EXCLUDE_RE.search(title):excluded+=1;continue
        kind=infer_type(title,org) or "activity";item={"id":stable_id(source["id"],source["url"],title),"type":kind,"title":title,"org":org or "教育部青年發展署","region":infer_region(title+" "+org),"deadline":deadline,"eligibility":"青年資源；請開啟來源確認年齡與身分限制","goal":infer_goal(title+" "+org,kind),"reason":"由青年發展署青年第一讚自動取得，申請期限由列表直接解析。","url":source["url"],"sourceUrl":source["url"],"sourceId":source["id"],"sourceName":source["name"],"firstSeen":NOW,"lastSeen":NOW,"updatedAt":NOW,"missCount":0};item["scores"]=scores_for(item);out.append(item)
    diagnostics={"rawCount":sum(1 for x in lines if "申請期限" in x),"identifiedCount":len(out),"eligibilityPass":0,"eligibilityReview":0,"eligibilityRejected":0,"unexpiredCount":0,"expiredCount":0,"noDeadlineCount":0,"finalCount":0,"excludedKeyword":excluded,"notOpportunity":0,"parseErrors":malformed,"deadlineParsed":sum(1 for x in out if x.get("deadline"))}
    final=[]
    for item in out:
        decision=eligibility_decision(item);diagnostics["eligibilityPass" if decision=="pass" else "eligibilityReview"]+=1
        if item["deadline"]<TODAY.isoformat():diagnostics["expiredCount"]+=1;continue
        diagnostics["unexpiredCount"]+=1;final.append(item)
    diagnostics["finalCount"]=len(final);return final,diagnostics


def dedupe(items: Iterable[dict]) -> tuple[list[dict], int]:
    out: list[dict] = []
    keys: set[str] = set()
    dup = 0
    for item in items:
        key = item.get("sourceUrl") or f"{item.get('sourceId')}|{norm_title(item.get('title',''))}"
        if key in keys:
            dup += 1
            continue
        keys.add(key)
        out.append(item)
    return out, dup


def load_previous() -> dict:
    if not OUT.exists():
        return {"active": [], "archive": [], "sources": {}}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"active": [], "archive": [], "sources": {}}


def merge(previous: dict, source_results: list[dict]) -> dict:
    prev_active = {x["id"]: x for x in previous.get("active", []) if x.get("id")}
    archive = {x["id"]: x for x in previous.get("archive", []) if x.get("id")}
    successful_ids = {r["source"]["id"] for r in source_results if r["ok"]}
    seen_ids: set[str] = set()
    stats = {"new": 0, "updated": 0, "duplicate": 0, "retired": 0, "review": 0}
    merged_items: list[dict] = []
    all_new = []
    for r in source_results:
        if r["ok"]:
            all_new.extend(r["items"])
    all_new, stats["duplicate"] = dedupe(all_new)

    for item in all_new:
        iid = item["id"]
        seen_ids.add(iid)
        old = prev_active.get(iid) or archive.pop(iid, None)
        if old:
            old_sig = json.dumps([old.get(k) for k in ("title", "deadline", "eligibility", "amount", "date", "location", "org", "url")], ensure_ascii=False)
            new_sig = json.dumps([item.get(k) for k in ("title", "deadline", "eligibility", "amount", "date", "location", "org", "url")], ensure_ascii=False)
            item["firstSeen"] = old.get("firstSeen") or NOW
            if old_sig == new_sig:
                item["updatedAt"] = old.get("updatedAt") or NOW
            else:
                stats["updated"] += 1
        else:
            item["firstSeen"] = NOW
            stats["new"] += 1
        item["lastSeen"] = NOW
        item["missCount"] = 0
        item["needsReview"] = False
        merged_items.append(item)

    # Preserve cached items for failed sources; increment miss count only for successful sources.
    for iid, old in prev_active.items():
        if iid in seen_ids:
            continue
        o = dict(old)
        if o.get("sourceId") in successful_ids:
            o["missCount"] = int(o.get("missCount") or 0) + 1
            if o["missCount"] >= 2:
                o["needsReview"] = True
                stats["review"] += 1
        merged_items.append(o)

    keep: list[dict] = []
    for o in merged_items:
        expired = False
        if o.get("deadline"):
            try:
                expired = date.fromisoformat(o["deadline"]) < TODAY
            except ValueError:
                pass
        stale = False
        if o.get("needsReview") and o.get("lastSeen"):
            try:
                stale = datetime.fromisoformat(o["lastSeen"]).date() < TODAY - timedelta(days=30)
            except Exception:
                pass
        if expired or stale:
            o = dict(o)
            o["retiredAt"] = NOW
            o["retireReason"] = "截止日已過" if expired else "來源連續未出現且超過30日"
            archive[o["id"]] = o
            stats["retired"] += 1
        else:
            keep.append(o)

    keep.sort(key=lambda x: (x.get("deadline") or "9999-12-31", -sum((x.get("scores") or {}).values()), x.get("title") or ""))
    archive_list = sorted(archive.values(), key=lambda x: x.get("retiredAt") or "", reverse=True)[:300]
    return {"active": keep, "archive": archive_list, "stats": stats}


def run() -> int:
    previous = load_previous()
    results = []
    source_state: dict[str, dict] = {}
    ordered_sources = sorted(SOURCES, key=lambda x: {"youth": 0, "moe": 1, "cysh": 2}.get(x.get("kind"), 9))
    for idx, source in enumerate(ordered_sources):
        if idx:
            time.sleep(0.8)
        started = time.monotonic()
        try:
            html = fetch(source["url"])
            if source["kind"] == "moe":
                parsed_items = parse_moe(source, html)
                items, diagnostics = filter_and_diagnose(html, parsed_items)
            elif source["kind"] == "cysh":
                items, diagnostics = parse_cysh(source, html)
            elif source["kind"] == "youth":
                items, diagnostics = parse_youthfirst(source, html)
            else:
                parsed_items = parse_link_source(source, html)
                items, diagnostics = filter_and_diagnose(html, parsed_items)
            ok = True
            error = ""
        except Exception as e:
            items = []
            diagnostics = {"rawCount": 0, "identifiedCount": 0, "eligibilityPass": 0, "eligibilityReview": 0, "eligibilityRejected": 0, "unexpiredCount": 0, "expiredCount": 0, "noDeadlineCount": 0, "finalCount": 0, "excludedKeyword": 0, "notOpportunity": 0, "parseErrors": 1}
            ok = False
            error = f"{type(e).__name__}: {e}"
        elapsed = round((time.monotonic() - started) * 1000)
        results.append({"ok": ok, "source": source, "items": items})
        source_state[source["id"]] = {
            "name": source["name"], "url": source["url"], "ok": ok,
            "count": len(items), "lastAttempt": NOW, "error": error, "ms": elapsed,
            "diagnostics": diagnostics,
        }
        diag_text = f"raw={diagnostics.get('rawCount',0)} identified={diagnostics.get('identifiedCount',0)} eligible={diagnostics.get('eligibilityPass',0)+diagnostics.get('eligibilityReview',0)} valid={diagnostics.get('unexpiredCount',0)+diagnostics.get('noDeadlineCount',0)} final={diagnostics.get('finalCount',0)}"
        print(f"[{ 'OK' if ok else 'FAIL' }] {source['name']}: {len(items)} items ({elapsed} ms) {diag_text}{' - '+error if error else ''}")

    merged = merge(previous, results)
    payload = {
        "schema": 2,
        "generatedAt": NOW,
        "today": TODAY.isoformat(),
        "active": merged["active"],
        "archive": merged["archive"],
        "sources": source_state,
        "stats": merged["stats"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok_count = sum(1 for x in source_state.values() if x["ok"])
    print(f"Wrote {OUT}: active={len(payload['active'])}, archive={len(payload['archive'])}, sources_ok={ok_count}/{len(SOURCES)}")
    # A total network failure keeps old cache and returns non-zero so Actions visibly reports it.
    return 0 if ok_count else 2


def selftest() -> int:
    sample = "115學年度獎學金自9月1日至9月30日受理，請於9月30日前提出申請。高中職在學生可申請。"
    assert extract_deadline(sample, date(2026, 8, 18)) == "2026-09-30"
    source = SOURCES[2]
    item = make_item(source=source, title="115學年度測試獎學金", url=source["url"]+"#test", body=sample)
    assert item and item["type"] == "scholarship" and item["deadline"] == "2026-09-30"
    trip = "參加對象：本校在學學生。參訪時間：2026年12月7日至12月12日。參訪地點：日本北陸。報名期間：115年9月1日至115年9月24日止。"
    assert extract_deadline(trip, date(2026, 9, 1)) == "2026-09-24"
    assert extract_event_date(trip, date(2026, 9, 1)) == "2026-12-07～2026-12-12"
    assert extract_location(trip) == "日本北陸"
    scholar = "114學年第二學期校內清寒獎助學金。劉俊杰校友獎學金：家庭貧困、單親、無父母學生，每名3萬5仟元；嘉義市診所協會獎助學金：每名5千元。申請期限：115.2.23起至115.3.11止，逾期不受理。"
    assert extract_scholarship_deadline(scholar, date(2026,2,9)) == "2026-03-11"
    assert extract_scholarship_amount(scholar) == "多項獎助｜NT$5,000～NT$35,000（依項目）"
    assert "家庭貧困" in extract_scholarship_eligibility(scholar)
    staff = {"title":"子女教育補助費","eligibility":"資格摘要｜公教人員子女相關補助","reason":""}
    assert eligibility_decision(staff) == "reject"
    expired = dict(item, id="expired", deadline=(TODAY-timedelta(days=1)).isoformat())
    filtered, diag = filter_and_diagnose('<a href="/x">115學年度測試獎學金</a>', [item, expired])
    assert len(filtered) == 1 and diag["expiredCount"] == 1 and diag["finalCount"] == 1
    merged = merge({"active":[expired],"archive":[]}, [])
    # No successful source => never retire only because it disappeared, but deadline still retires.
    assert not merged["active"] and merged["archive"][0]["retireReason"] == "截止日已過"
    print("selftest: OK")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    sys.exit(selftest() if args.selftest else run())
