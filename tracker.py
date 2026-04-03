#!/usr/bin/env python3
"""
Startup Tracker — Multi-source company monitoring script
• Tavily    → News / funding (public web)
• Crawl4AI  → Website blog/news/changelog diffs
• Firecrawl → Alternative website monitor (API-based)
• Apify     → Twitter/X & LinkedIn posts

Usage:
    python tracker.py                          # uses default config
    python tracker.py --config ./my.json       # custom config path
    python tracker.py --tavily-key sk-xxx      # override API key
    python tracker.py --tavily-key sk-xxx --apify-key xxx --firecrawl-key xxx
    python tracker.py --validate               # validate API keys without running
"""

import os
import sys
import json
import time
import argparse
import hashlib
import datetime
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

try:
    from crawl4ai import AsyncWebCrawler
    CRAWL4AI_AVAILABLE = True
except Exception:
    CRAWL4AI_AVAILABLE = False


# ── CLI Arguments ────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Startup Tracker")
parser.add_argument("--config", default=None,
                    help="Path to config.json config file")
parser.add_argument("--tavily-key", default=None,
                    help="Tavily API key (overrides env/config)")
parser.add_argument("--apify-key", default=None,
                    help="Apify API token (overrides env/config)")
parser.add_argument("--firecrawl-key", default=None,
                    help="Firecrawl API key (overrides env/config)")
parser.add_argument("--init", default=None,
                    help="Write provided JSON as config.json (non-interactive config creation)")
parser.add_argument("--list", action="store_true",
                    help="Show current configuration summary")
parser.add_argument("--validate", action="store_true",
                    help="Validate API keys without running full scan")
parser.add_argument("--days", type=int, default=None,
                    help="Override monitoring window in days (overrides config.monitor_interval_days and tavily.search_days_back)")
args = parser.parse_args()

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")  # load .env from the same directory as script (skill directory)

DEFAULT_CONFIG = SCRIPT_DIR / "config.json"
STATE_DIR = SCRIPT_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)

# State files
TAVILY_STATE = STATE_DIR / "tavily_state.json"
CRAWL4AI_STATE = STATE_DIR / "crawl4ai_state.json"
FIRECRAWL_STATE = STATE_DIR / "firecrawl_state.json"
APIFY_STATE = STATE_DIR / "apify_state.json"

# Output paths
NEW_ITEMS_PATH = STATE_DIR / "new_items.json"

# ── --init: non-interactive config creation ────────────────────────────────────
if args.init:
    try:
        config_data = json.loads(args.init)
    except json.JSONDecodeError as e:
        print(f"[Error] Invalid JSON provided to --init: {e}")
        sys.exit(1)
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG
    STATE_DIR.mkdir(exist_ok=True)
    config_path.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Config written to {config_path}")
    print(f"[OK] State directory ready at {STATE_DIR}")
    sys.exit(0)

# ── Lazy config loading (called from main) ────────────────────────────────────
def load_config():
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG
    if not config_path.exists():
        print(f"[Error] Config not found: {config_path}")
        print("  → Run: python tracker.py --init '<json>'  (Agent调用)")
        print("  → Or:  /startup-tracker  (首次会自动引导)")
        sys.exit(1)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    companies = cfg.get("companies", [])
    if not companies:
        print("[Error] No companies in config.")
        sys.exit(1)

    def _key(cfg_key, env_var):
        # cfg_key e.g. "tavily-key" -> "tavily"; looks up "tavily" in api_keys
        cfg_name = cfg_key.replace("-","_").replace("_key","")
        api_keys = cfg.get("api_keys", {})
        # Support both short-form ("tavily") and full env-style ("TAVILY_API_KEY") keys
        return (
            getattr(args, cfg_key.replace("-","_"), None)
            or api_keys.get(cfg_name.lower())
            or api_keys.get(env_var)
            or os.environ.get(env_var)
        )

    TAVILY_KEY    = _key("tavily-key", "TAVILY_API_KEY")
    FIRECRAWL_KEY = _key("firecrawl-key", "FIRECRAWL_API_KEY")
    APIFY_KEY     = _key("apify-key", "APIFY_TOKEN")

    # ── Resolve monitoring days: CLI --days > config.monitor_interval_days > tavily.search_days_back > 7 ──
    monitor_days = args.days
    if monitor_days is None:
        monitor_days = cfg.get("monitor_interval_days")
    if monitor_days is None:
        monitor_days = cfg.get("tavily", {}).get("search_days_back", 7)
    # Also update tavily.search_days_back so downstream functions see the same window
    tavily_cfg = cfg.setdefault("tavily", {})
    tavily_cfg["search_days_back"] = monitor_days
    monitor_days = max(1, monitor_days)

    # Set globals for data source functions
    global TAVILY_API_KEY, FIRECRAWL_API_KEY, APIFY_TOKEN
    TAVILY_API_KEY    = TAVILY_KEY
    FIRECRAWL_API_KEY = FIRECRAWL_KEY
    APIFY_TOKEN       = APIFY_KEY
    return cfg, companies

# ── API Key Validation ───────────────────────────────────────────────────────
def validate_tavily_key(key: str) -> tuple[bool, str]:
    """Validate Tavily API key by making a test search."""
    if not key:
        return False, "Key is empty"
    try:
        cmd = ["tvly", "search", "test", "--max-results", "1", "--json"]
        env = {**os.environ, "TAVILY_API_KEY": key}
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode != 0:
            error = result.stderr.strip() if result.stderr else "Unknown error"
            return False, error
        # Try to parse JSON to confirm it worked
        json.loads(result.stdout)
        return True, "Valid"
    except subprocess.TimeoutExpired:
        return False, "Request timeout"
    except json.JSONDecodeError:
        return False, "Invalid response format"
    except Exception as e:
        return False, str(e)

def validate_apify_key(key: str) -> tuple[bool, str]:
    """Validate Apify token by checking user info. Retries on timeout."""
    if not key:
        return False, "Key is empty"
    headers = {"Authorization": f"Bearer {key}"}
    for attempt in range(3):
        try:
            r = requests.get("https://api.apify.com/v2/users/me", headers=headers, timeout=60, verify=False)
            if r.status_code == 200:
                return True, "Valid"
            elif r.status_code == 401:
                return False, "Invalid token (401 Unauthorized)"
            else:
                return False, f"HTTP {r.status_code}"
        except requests.Timeout:
            if attempt < 2:
                time.sleep(2)
                continue
            return False, "Request timeout (network may be unreachable)"
        except Exception as e:
            return False, str(e)
    return False, "Request timeout"

def check_config_status(config_path: Path = DEFAULT_CONFIG) -> dict:
    """Check configuration status for UI display."""
    status = {
        "config_exists": config_path.exists(),
        "companies_count": 0,
        "tavily": {"configured": False, "valid": False, "error": ""},
        "apify": {"configured": False, "valid": False, "error": ""},
        "firecrawl": {"configured": False, "valid": False, "error": ""},
        "crawl4ai_available": CRAWL4AI_AVAILABLE,
        "twitter_enabled": False,
        "linkedin_enabled": False,
    }

    if not config_path.exists():
        return status

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        status["companies_count"] = len(cfg.get("companies", []))

        api_keys = cfg.get("api_keys", {})

        # Check Tavily — fallback across multiple config key namings + env var
        tavily_key = (
            api_keys.get("tavily")
            or api_keys.get("TAVILY_API_KEY")
            or os.environ.get("TAVILY_API_KEY")
            or ""
        )
        if tavily_key:
            status["tavily"]["configured"] = True
            valid, error = validate_tavily_key(tavily_key)
            status["tavily"]["valid"] = valid
            status["tavily"]["error"] = error if not valid else ""

        # Check Apify — fallback across multiple config key namings + env var
        apify_key = (
            api_keys.get("apify")
            or api_keys.get("APIFY_TOKEN")
            or os.environ.get("APIFY_TOKEN")
            or ""
        )
        if apify_key:
            status["apify"]["configured"] = True
            valid, error = validate_apify_key(apify_key)
            status["apify"]["valid"] = valid
            status["apify"]["error"] = error if not valid else ""

        # Check Firecrawl — fallback to env var if config key is empty
        firecrawl_key = api_keys.get("firecrawl") or os.environ.get("FIRECRAWL_API_KEY") or ""
        if firecrawl_key:
            status["firecrawl"]["configured"] = True
            # Firecrawl validation would require a test request
            status["firecrawl"]["valid"] = True

        # Check if any company has social handles
        for comp in cfg.get("companies", []):
            if comp.get("x_handle"):
                status["twitter_enabled"] = True
            if comp.get("linkedin_url"):
                status["linkedin_enabled"] = True

    except Exception as e:
        status["error"] = str(e)

    return status

# ── JSON Helpers
def load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default or {}

def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# ── Source Name ───────────────────────────────────────────────────────────────
def get_source_name(url: str) -> str:
    domain_map = {
        "businesswire.com": "BusinessWire", "prnewswire.com": "PR Newswire",
        "techcrunch.com": "TechCrunch", "crunchbase.com": "Crunchbase",
        "axios.com": "Axios", "forbes.com": "Forbes",
        "bloomberg.com": "Bloomberg", "reuters.com": "Reuters",
        "fortune.com": "Fortune", "linkedin.com": "LinkedIn",
        "twitter.com": "X/Twitter", "x.com": "X/Twitter",
        "geekwire.com": "GeekWire", "venturebeat.com": "VentureBeat",
        "theverge.com": "The Verge", "techmeme.com": "Techmeme",
        "eweek.com": "eWeek", "yahoo.com": "Yahoo Finance",
        "youtube.com": "YouTube", "reddit.com": "Reddit",
        "pitchbook.com": "PitchBook", "ycombinator.com": "Y Combinator",
        "securitybrief.co.uk": "SecurityBrief", "regtechanalyst.com": "RegTech Analyst",
        "trysignalbase.com": "SignalBase", "techfundingnews.com": "Tech Funding News",
        "crn.com": "CRN", "constellationr.com": "Constellation Research",
        "ajbell.co.uk": "AJ Bell", "binariks.com": "Binariks",
    }
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        for key, name in domain_map.items():
            if key in domain:
                return name
        parts = domain.split(".")
        return parts[-2].capitalize() if len(parts) >= 2 else domain
    except Exception:
        return "Unknown"

# ── Date Utilities ─────────────────────────────────────────────────────────────
_MONTH_NUM = {
    "jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
    "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12",
    "january":"01","february":"02","march":"03","april":"04","june":"06",
    "july":"07","august":"08","september":"09","october":"10","november":"11","december":"12",
}

def _month_num(name: str) -> str:
    return _MONTH_NUM.get(name.lower(), "01")

def extract_date_from_content(content: str, url: str = "") -> str:
    """Try to extract date like 'July 17, 2025' from content or URL."""
    if not content and not url:
        return ""
    m = re.search(
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December|[A-Z][a-z]{2})\s+\d{1,2},?\s+\d{4})",
        content or "", 0
    )
    if m:
        date_str = m.group(1).replace(",", "").strip()
        parts = date_str.split()
        if len(parts) < 3:
            return ""
        return f"{parts[2]}-{_month_num(parts[0])}-{parts[1].zfill(2)}"
    if url:
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""

def is_within_days(date_str: str, days: int) -> bool:
    """Return True if date_str is within the last `days` days.

    Supports multiple formats:
    - ISO formats: 2026-03-25, 2026-03-25T20:12:34, 2026-03-25T20:12:34Z
    - RFC 2822: Wed, 25 Mar 2026 20:12:34 GMT (Tavily returns this)
    """
    if not date_str:
        return True  # Trust API filter if no date provided

    # Try RFC 2822 format first (Tavily returns this: "Wed, 25 Mar 2026 20:12:34 GMT")
    rfc_formats = [
        "%a, %d %b %Y %H:%M:%S %Z",      # Wed, 25 Mar 2026 20:12:34 GMT
        "%a, %d %b %Y %H:%M:%S GMT",     # Wed, 25 Mar 2026 20:12:34 GMT (explicit)
        "%d %b %Y %H:%M:%S %Z",          # 25 Mar 2026 20:12:34 GMT
        "%d %b %Y %H:%M:%S",             # 25 Mar 2026 20:12:34
    ]
    for fmt in rfc_formats:
        try:
            article_date = datetime.datetime.strptime(date_str, fmt)
            delta = datetime.datetime.now() - article_date.replace(tzinfo=None)
            return delta.days <= days
        except ValueError:
            continue

    # Try ISO formats
    iso_formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in iso_formats:
        try:
            clean = date_str.replace("Z", "+00:00").replace("+0000", "+00:00")
            article_date = datetime.datetime.strptime(clean, fmt)
            delta = datetime.datetime.now() - article_date.replace(tzinfo=None)
            return delta.days <= days
        except ValueError:
            continue

    # If we can't parse the date, trust the API's time-range filter
    return True

def _resolve_date(api_date: str, content_date: str, days_back: int) -> Optional[str]:
    """Cross-validate API date against content-extracted date.

    Returns a resolved date string (YYYY-MM-DD) if within the window,
    or None to filter out the item.

    Strategy:
    1. If both dates are available and both parse successfully:
       - If they match (same day): high confidence, use it
       - If they differ by <= 3 days: moderate confidence, use the earlier one
       - If they differ by > 3 days: suspicious, but keep the earlier one
         if it's within the window (API date may be the article pub date,
         content date may be the event date)
    2. If only API date is available: use it (trust the API filter)
    3. If only content date is available: fall back to content extraction
    4. If neither is available: return a sentinel (caller decides)
    """
    api_valid = api_date and is_within_days(api_date, days_back)
    content_valid = content_date and is_within_days(content_date, days_back)

    if api_valid and content_valid:
        # Both dates parse and are within window — compare
        try:
            api_dt = _parse_to_datetime(api_date)
            content_dt = _parse_to_datetime(content_date)
            if api_dt and content_dt:
                delta = abs((api_dt - content_dt).days)
                if delta == 0:
                    pass  # Exact match, high confidence
                elif delta <= 3:
                    pass  # Close enough, minor discrepancy
                else:
                    print(f"  [Date Mismatch] API={api_date} vs Content={content_date} (Δ{delta}d), using earlier")
                # Use the earlier date to be conservative
                earlier_dt = min(api_dt, content_dt)
                return earlier_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
        # If comparison failed for any reason, fall through to single-source logic
        return api_date[:10]

    if api_valid:
        return api_date[:10]

    if content_valid:
        return content_date

    # Neither source produced a usable date — filter out the item
    return None

def _parse_to_datetime(date_str: str) -> Optional[datetime.datetime]:
    """Parse any supported date string into a naive datetime."""
    if not date_str:
        return None
    rfc_formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S",
    ]
    for fmt in rfc_formats:
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    iso_formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in iso_formats:
        try:
            clean = date_str.replace("Z", "+00:00").replace("+0000", "+00:00")
            return datetime.datetime.strptime(clean, fmt)
        except ValueError:
            continue
    return None

# ── Tavily ────────────────────────────────────────────────────────────────────
# Tavily API only supports coarse time windows: day / week / month / year.
# We always pick the smallest window that fully covers `days_back`, then
# re-filter results locally with `is_within_days()`.
def _days_to_time_range(days_back: int) -> str:
    """Map exact days to the smallest Tavily time window that covers them."""
    if days_back <= 1: return "day"
    elif days_back <= 7: return "week"
    elif days_back <= 30: return "month"
    return "year"

def search_tavily(query: str, max_results: int = 10, days_back: int = 7) -> List[Dict]:
    if not TAVILY_API_KEY:
        return []
    time_range = _days_to_time_range(days_back)
    cmd = ["tvly", "search", query,
           "--time-range", time_range, "--topic", "news",
           "--max-results", str(max_results),
           "--include-raw-content", "markdown", "--json"]
    try:
        env = {**os.environ, "TAVILY_API_KEY": TAVILY_API_KEY}
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode != 0 and not result.stdout.strip():
            print(f"[Tavily Error] {result.stderr.strip()}")
            return []
        if not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        return data.get("results", [])
    except Exception as e:
        print(f"[Tavily Error] {query}: {e}")
        return []

def run_tavily(companies: List[Dict], config: Dict) -> List[Dict]:
    if not TAVILY_API_KEY:
        print("[Tavily] 跳过 — 未配置 TAVILY_API_KEY")
        return []
    settings = config.get("tavily", {})
    max_results = settings.get("max_results_per_company", 10)
    major_keywords = settings.get("major_keywords", [])
    days_back = settings.get("search_days_back", 7)

    seen = load_json(TAVILY_STATE, {"urls": []})
    seen_urls = set(seen.get("urls", []))
    items = []

    for comp in companies:
        name = comp["name"]
        # Pass 1: keyword-constrained search for significant events
        query = f'"{name}" funding OR launch OR product OR partnership OR series OR acquired OR acquisition OR investment OR expansion OR IPO OR merger OR round'
        print(f"[Tavily] Search: {name} ...")
        results = search_tavily(query, max_results=max_results, days_back=days_back)
        exclude_keywords = [k.lower() for k in comp.get("exclude_keywords", [])]
        # If no results from keyword search, fall back to company-name-only search
        if not results:
            print(f"[Tavily] 关键词搜索无结果，尝试放宽为仅搜索公司名称: \"{name}\"")
            results = search_tavily(f'"{name}"', max_results=max_results, days_back=days_back)
        for r in results[:max_results]:
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            api_date = r.get("published_date", "") or ""
            raw_content = r.get("raw_content", "") or ""
            content_date = extract_date_from_content(raw_content, url)
            # Cross-validate dates: use API date if available, supplement with
            # content-extracted date, and pick the more conservative (earlier) one
            # that is still within the search window.
            published_date = _resolve_date(api_date, content_date, days_back)
            # Filter out items that are verifiably outside the time window
            if published_date is None:
                continue
            published_date = published_date  # now str
            title = r.get("title", "无标题")
            snippet = r.get("content", "")
            combined = (title + " " + snippet).lower()
            if exclude_keywords and any(kw in combined for kw in exclude_keywords):
                continue
            # Relevance filter: require company name to appear in title
            # For multi-word names like "Human Delta", require both words or full phrase
            # For single-word names like "Roe", require exact match (not just substring)
            title_lower = title.lower()
            name_lower = name.lower()

            relevant = False
            if name_lower in title_lower:
                # Full company name appears in title - good match
                relevant = True
            else:
                # For multi-word names, check if ALL significant parts appear in title
                # This prevents "Human Delta" matching "Delta" (airline)
                name_parts = [p.lower() for p in name.split() if len(p) > 2]
                if len(name_parts) >= 2:
                    # Multi-word company: require all words to appear (or at least 2)
                    matching_parts = sum(1 for part in name_parts if part in title_lower)
                    # Require at least 2 matching parts, or all if only 2 total
                    relevant = matching_parts >= 2 or (len(name_parts) == 2 and matching_parts == 2)
                elif len(name_parts) == 1:
                    # Single word company: require exact word match (not substring)
                    # Use word boundary check
                    relevant = bool(re.search(r'\b' + re.escape(name_parts[0]) + r'\b', title_lower))

            if not relevant:
                continue
            seen_urls.add(url)
            imp = "MAJOR" if any(kw.lower() in combined for kw in major_keywords) else "NORMAL"
            items.append({
                "company": name, "source": "tavily",
                "title": title, "url": url,
                "snippet": snippet[:300], "published_date": published_date,
                "importance": imp,
            })
    save_json(TAVILY_STATE, {"urls": list(seen_urls)})
    print(f"[Tavily] 新增 {len(items)} 条")
    return items

# ── Crawl4AI ─────────────────────────────────────────────────────────────────
async def crawl4ai_scrape(url: str) -> Optional[str]:
    if not CRAWL4AI_AVAILABLE:
        return None
    try:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            return result.markdown.strip() if result and result.markdown else ""
    except Exception as e:
        print(f"[Crawl4AI Error] {url}: {e}")
        return None

def extract_article_signatures(markdown: str) -> str:
    lines = markdown.splitlines()
    signatures = []
    skip_texts = (
        "trusted by leading enterprises worldwide", "solutions", "company overview",
        "products", "resources", "case studies", "team", "careers",
        "webinars", "blogs", "research"
    )
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and "](" in stripped:
            m = re.search(r"\]\((https?://[^\s)]+)\)", stripped)
            if m:
                link = m.group(1)
                if (re.search(r"/\d{4}/\d{2}/\d{2}/", link)
                        or "/blog/" in link or "/post/" in link
                        or "/article/" in link or "/press/" in link):
                    if not re.search(r"/(category|tag|author|page)/", link, re.I):
                        signatures.append(stripped)
        elif stripped.startswith(("# ", "## ", "### ")) and len(stripped) > 4:
            text = stripped.lstrip("# ").strip()
            if text.lower() not in skip_texts:
                signatures.append(text)
    return "\n".join(signatures[:80]) if signatures else markdown

def run_crawl4ai(companies: List[Dict], config: Dict) -> List[Dict]:
    if not CRAWL4AI_AVAILABLE:
        print("[Crawl4AI] 跳过 — 未安装 crawl4ai")
        return []
    import asyncio
    cfg = config.get("website_monitor", {})
    max_chars = cfg.get("max_content_chars", 2000)
    use_article_sig = cfg.get("use_article_signature", True)
    state = load_json(CRAWL4AI_STATE, {})
    items = []

    async def _process():
        first_run_keys = []
        for comp in companies:
            name = comp["name"]
            urls = comp.get("monitor_urls", []) or ([comp["website"].strip().rstrip("/")] if comp.get("website") else [])
            for url in urls:
                md = await crawl4ai_scrape(url)
                if md is None:
                    continue
                content = md.strip()
                if len(content) < 50:
                    continue
                key = f"{name}::{url}"
                sig_text = extract_article_signatures(content) if use_article_sig else content
                current_hash = sha256_text(sig_text[:max_chars])
                prev_hash = state.get(key, "")
                if not prev_hash:
                    first_run_keys.append(f"{name} ({url})")
                if prev_hash and prev_hash != current_hash:
                    items.append({
                        "company": name, "source": "crawl4ai",
                        "title": f"[Website Update] {name} - {url.replace(comp.get('website','').rstrip('/'),'') or '/'}",
                        "url": url, "snippet": content[:300].replace("\n"," "),
                        "published_date": "", "importance": "NORMAL",
                    })
                state[key] = current_hash
        if first_run_keys:
            print(f"\n[Crawl4AI] 首次运行 — 已为以下站点建立基线: {', '.join(first_run_keys)}")
            print("[Crawl4AI] 网站变更检测从第二次运行开始生效\n")

    asyncio.run(_process())
    save_json(CRAWL4AI_STATE, state)
    print(f"[Crawl4AI] 新增 {len(items)} 条")
    return items

# ── Firecrawl ────────────────────────────────────────────────────────────────
def firecrawl_scrape(url: str) -> Optional[str]:
    if not FIRECRAWL_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"]}, timeout=40
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("markdown", "")
    except Exception as e:
        print(f"[Firecrawl Error] {url}: {e}")
        return None

def run_firecrawl(companies: List[Dict], config: Dict) -> List[Dict]:
    if not FIRECRAWL_API_KEY:
        print("[Firecrawl] 跳过 — 未配置 FIRECRAWL_API_KEY")
        return []
    cfg = config.get("website_monitor", {})
    max_chars = cfg.get("max_content_chars", 2000)
    use_article_sig = cfg.get("use_article_signature", True)
    state = load_json(FIRECRAWL_STATE, {})
    items = []

    for comp in companies:
        name = comp["name"]
        urls = comp.get("monitor_urls", []) or ([comp["website"].strip().rstrip("/")] if comp.get("website") else [])
        for url in urls:
            md = firecrawl_scrape(url)
            if md is None:
                continue
            content = md.strip()
            if len(content) < 50:
                continue
            key = f"{name}::{url}"
            sig_text = extract_article_signatures(content) if use_article_sig else content
            current_hash = sha256_text(sig_text[:max_chars])
            prev_hash = state.get(key, "")
            if prev_hash and prev_hash != current_hash:
                items.append({
                    "company": name, "source": "firecrawl",
                    "title": f"[Website Update] {name} - {url.replace(comp.get('website','').rstrip('/'),'') or '/'}",
                    "url": url, "snippet": content[:300].replace("\n"," "),
                    "published_date": "", "importance": "NORMAL",
                })
            state[key] = current_hash

    save_json(FIRECRAWL_STATE, state)
    print(f"[Firecrawl] 新增 {len(items)} 条")
    return items

# ── Apify ────────────────────────────────────────────────────────────────────
def run_apify_actor(actor_id: str, input_data: Dict, poll_interval: int = 5, max_poll: int = 120) -> List[Dict]:
    if not APIFY_TOKEN:
        return []
    headers = {"Authorization": f"Bearer {APIFY_TOKEN}", "Content-Type": "application/json"}
    # Apify API v2 uses raw owner/name format in URL path (NOT "~" replacement)
    base = f"https://api.apify.com/v2/acts/{actor_id}"

    # 1. Start run
    try:
        r = requests.post(f"{base}/runs", headers=headers, json=input_data, timeout=30, verify=False)
        r.raise_for_status()
        run_data = r.json()["data"]
        run_id = run_data["id"]
    except requests.exceptions.Timeout:
        print(f"[Apify Error] 启动超时 {actor_id} — 网络可能无法访问 Apify")
        return []
    except Exception as e:
        print(f"[Apify Error] 启动失败 {actor_id}: {e}")
        return []

    # 2. Poll for completion
    final_status = None
    for _ in range(max_poll // poll_interval):
        time.sleep(poll_interval)
        try:
            r2 = requests.get(f"{base}/runs/{run_id}", headers=headers, timeout=30, verify=False)
            status = r2.json()["data"]["status"]
            final_status = status
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "ABORTED", "TIMED-OUT", "FAILED-PUBLICATION"):
                print(f"[Apify Error] 运行失败 {actor_id}: {status}")
                return []
        except requests.exceptions.Timeout:
            print(f"[Apify Warning] Polling 超时，继续等待...")
            continue
        except Exception as e:
            print(f"[Apify Poll Error] {e}")
            continue

    if final_status != "SUCCEEDED":
        print(f"[Apify Warning] 运行未成功结束 {actor_id}, status={final_status}")
        return []

    # 3. Fetch dataset
    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        return []
    try:
        r3 = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            headers=headers, timeout=60,
            params={"clean": "true"},
            verify=False
        )
        r3.raise_for_status()
        return r3.json()
    except Exception as e:
        print(f"[Apify Error] 获取数据集失败: {e}")
        return []

def parse_linkedin_time(time_str: str) -> int:
    if not time_str:
        return 999
    time_str = time_str.lower().strip()
    try:
        if "yr" in time_str: return int(re.sub(r"[^\d]", "", time_str)) * 365
        if "mo" in time_str: return int(time_str.replace("mo","").strip()) * 30
        if "w" in time_str: return int(time_str.replace("w","").strip()) * 7
        if "d" in time_str: return int(time_str.replace("d","").strip())
        if "h" in time_str or "min" in time_str or "s" in time_str: return 0
    except:
        pass
    return 999

def relative_time_to_date(time_str: str) -> str:
    if not time_str:
        return ""
    now = datetime.datetime.now()
    tl = time_str.lower().strip()
    try:
        if "yr" in tl: dt = now - datetime.timedelta(days=int(re.sub(r"[^\d]","",tl)) * 365)
        elif "mo" in tl: dt = now - datetime.timedelta(days=int(tl.replace("mo","").strip()) * 30)
        elif "w" in tl: dt = now - datetime.timedelta(weeks=int(tl.replace("w","").strip()))
        elif "d" in tl: dt = now - datetime.timedelta(days=int(tl.replace("d","").strip()))
        elif "h" in tl or "min" in tl or "s" in tl: dt = now
        else:
            for fmt in ["%Y-%m-%d","%Y-%m-%dT%H:%M:%S","%Y-%m-%dT%H:%M:%SZ"]:
                try:
                    return datetime.datetime.strptime(time_str.replace("Z",""), fmt).strftime("%Y-%m-%d")
                except: continue
            return time_str
        return dt.strftime("%Y-%m-%d")
    except:
        return time_str


def run_apify_twitter(companies: List[Dict], config: Dict) -> List[Dict]:
    if not APIFY_TOKEN:
        print("[Apify Twitter] 跳过 — 未配置 APIFY_TOKEN")
        return []
    actor_id = config.get("apify", {}).get("twitter_actor_id", "parseforge/x-com-scraper")
    max_items = config.get("apify", {}).get("max_tweets_per_run", 10)
    poll_int = config.get("apify", {}).get("poll_interval_sec", 5)
    max_poll = config.get("apify", {}).get("max_poll_sec", 120)
    monitor_days = config.get("tavily", {}).get("search_days_back", 7)
    state = load_json(APIFY_STATE, {})
    items = []

    for comp in companies:
        handle = comp.get("x_handle", "").strip().lstrip("@")
        if not handle:
            continue
        name = comp["name"]
        seen_ids = set(state.get(f"{name}::twitter", []))
        # parseforge/x-com-scraper uses "usernames" (array) + "maxItems"
        input_data = {
            "usernames": [handle],
            "maxItems": max_items,
        }
        print(f"[Apify Twitter] Fetch @{handle} ({name}) ...")
        try:
            results = run_apify_actor(actor_id, input_data, poll_int, max_poll)
        except Exception as e:
            print(f"[Apify Twitter Error] {name}: {e}")
            continue

        if not isinstance(results, list):
            print(f"[Apify Twitter] Unexpected response type for {name}: {type(results)}")
            continue

        new_ids = []
        is_first_run = len(seen_ids) == 0
        for r in results[:max_items]:
            # Handle flexible field names
            tid = r.get("id") or r.get("url") or r.get("tweetLink") or ""
            if not tid or tid in seen_ids:
                continue
            if is_first_run:
                created_at = r.get("createdAt") or r.get("date") or r.get("timestamp") or ""
                if created_at:
                    try:
                        tweet_date = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        days_ago = (datetime.datetime.now(datetime.timezone.utc) - tweet_date).days
                        if days_ago > monitor_days:
                            continue
                    except Exception:
                        pass
            seen_ids.add(tid)
            new_ids.append(tid)
            snippet = r.get("text") or r.get("fullText") or r.get("content") or ""
            tweet_url = r.get("url") or r.get("tweetLink") or f"https://x.com/{handle}/status/{tid}"
            pub_date = r.get("createdAt") or r.get("date") or r.get("timestamp") or ""
            items.append({
                "company": name, "source": "apify_twitter",
                "title": f"[X/Twitter Post] {name} (@{handle})",
                "url": tweet_url,
                "snippet": snippet[:300],
                "published_date": pub_date[:10] if pub_date else "",
                "importance": "NORMAL",
            })
        if new_ids:
            state[f"{name}::twitter"] = list(seen_ids)
        print(f"  → {len(new_ids)} new tweets")

        if len(results) == 0:
            print(f"  [Apify Twitter Warning] @{handle} 返回 0 条结果 — 可能是账号不存在或该用户无公开推文")

    save_json(APIFY_STATE, state)
    return items

def run_apify_linkedin(companies: List[Dict], config: Dict) -> List[Dict]:
    if not APIFY_TOKEN:
        print("[Apify LinkedIn] 跳过 — 未配置 APIFY_TOKEN")
        return []
    actor_id = config.get("apify", {}).get("linkedin_actor_id", "supreme_coder/linkedin-post")
    max_items = config.get("apify", {}).get("max_linkedin_posts_per_run", 10)
    poll_int = config.get("apify", {}).get("poll_interval_sec", 5)
    max_poll = config.get("apify", {}).get("max_poll_sec", 120)
    monitor_days = config.get("tavily", {}).get("search_days_back", 7)
    state = load_json(APIFY_STATE, {})
    items = []

    for comp in companies:
        linkedin_url = comp.get("linkedin_url", "").strip()
        if not linkedin_url:
            continue
        name = comp["name"]
        seen_ids = set(state.get(f"{name}::linkedin", []))
        # supreme_coder/linkedin-post uses urls (string array)
        input_data = {
            "urls": [linkedin_url],
            "limit": max_items,
        }
        print(f"[Apify LinkedIn] Fetch {name} ...")
        try:
            results = run_apify_actor(actor_id, input_data, poll_int, max_poll)
        except Exception as e:
            print(f"[Apify LinkedIn Error] {name}: {e}")
            continue

        if not isinstance(results, list):
            print(f"[Apify LinkedIn] Unexpected response type for {name}: {type(results)}")
            continue

        new_ids = []
        is_first_run = len(seen_ids) == 0
        for r in results[:max_items]:
            if is_first_run:
                days_ago = parse_linkedin_time(r.get("timeSincePosted", "") or r.get("postedAt", ""))
                if days_ago > monitor_days:
                    continue
            # Flexible ID fields
            pid = r.get("urn") or r.get("url") or r.get("shareUrn") or r.get("id") or ""
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            new_ids.append(pid)
            # Flexible text fields
            snippet = r.get("text") or r.get("content") or r.get("postText") or ""
            post_url = r.get("url") or linkedin_url
            # Handle relative time strings vs ISO dates from supreme_coder/linkedin-post
            time_since = r.get("timeSincePosted") or r.get("postedAtISO") or r.get("postedAt") or ""
            pub_date = ""
            if time_since:
                # If it looks like a relative time (contains d/w/mo/yr), convert it
                if re.search(r'\d+\s*[dwym]', time_since, re.I) or re.search(r'\d+[dwmoyr]', time_since.lower()):
                    pub_date = relative_time_to_date(time_since)
                else:
                    # Treat as ISO or absolute date
                    pub_date = time_since[:10]
            items.append({
                "company": name, "source": "apify_linkedin",
                "title": f"[LinkedIn Post] {name}",
                "url": post_url,
                "snippet": snippet[:300] if snippet else "(No text)",
                "published_date": pub_date,
                "importance": "NORMAL",
            })
        if new_ids:
            state[f"{name}::linkedin"] = list(seen_ids)
        print(f"  → {len(new_ids)} new posts")

    save_json(APIFY_STATE, state)
    return items

def main():
    # ── Handle --list and --validate commands ────────────────────────────────
    if args.list or args.validate:
        status = check_config_status()

        if args.list:
            print("\n=== Startup Tracker Config ===\n")
            if not status["config_exists"]:
                print(f"[Error] No config found at {DEFAULT_CONFIG}")
                sys.exit(1)

            try:
                cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
                companies = cfg.get("companies", [])
                print(f"Companies ({len(companies)}):")
                for i, c in enumerate(companies, 1):
                    twitter = c.get("x_handle", "")
                    linkedin = c.get("linkedin_url", "")
                    print(f"  {i}. {c.get('name',''):<25} | Twitter: {'@'+twitter if twitter else '—':<15} | LinkedIn: {'✅' if linkedin else '—'}")
            except Exception as e:
                print(f"[Error] Failed to read config: {e}")
                sys.exit(1)

            print(f"\nAPI Keys:")
            print(f"  Tavily:    {'✅ Valid' if status['tavily']['valid'] else '❌ Invalid' if status['tavily']['configured'] else '⚪ Not configured'}")
            print(f"  Apify:     {'✅ Valid' if status['apify']['valid'] else '❌ Invalid' if status['apify']['configured'] else '⚪ Not configured'}")
            print(f"  Firecrawl: {'✅ Configured' if status['firecrawl']['configured'] else '⚪ Not configured'}")
            print(f"\nData Sources:")
            print(f"  {'✅' if status['tavily']['valid'] else '❌'}  News (Tavily)")
            print(f"  {'✅' if CRAWL4AI_AVAILABLE else '❌'}  Website (Crawl4AI)")
            print(f"  {'✅' if status['apify']['valid'] and status['twitter_enabled'] else '❌'}  Twitter (Apify)")
            print(f"  {'✅' if status['apify']['valid'] and status['linkedin_enabled'] else '❌'}  LinkedIn (Apify)")
            print(f"\nConfig: {DEFAULT_CONFIG}")
            print(f"State:   {STATE_DIR}\n")
            sys.exit(0)

        if args.validate:
            print("\n=== Startup Tracker Configuration Check ===\n")
            if not status["config_exists"]:
                print("❌ Config file not found")
                print(f"   Expected: {DEFAULT_CONFIG}")
                sys.exit(1)

            print(f"✅ Config file exists")
            print(f"   Companies: {status['companies_count']}\n")

            print("Tavily (News Search):")
            if status["tavily"]["configured"]:
                if status["tavily"]["valid"]:
                    print("  ✅ Key configured and valid")
                else:
                    print(f"  ⚠️  Key configured but invalid: {status['tavily']['error']}")
            else:
                print("  ❌ Not configured (required for news monitoring)")

            print("\nApify (Twitter/LinkedIn):")
            if status["apify"]["configured"]:
                if status["apify"]["valid"]:
                    print("  ✅ Token configured and valid")
                    print(f"  📱 Twitter: {'Enabled' if status['twitter_enabled'] else 'No handles configured'}")
                    print(f"  💼 LinkedIn: {'Enabled' if status['linkedin_enabled'] else 'No URLs configured'}")
                else:
                    print(f"  ⚠️  Token configured but invalid: {status['apify']['error']}")
            else:
                print("  ⚪ Not configured (optional - social media monitoring disabled)")

            print("\nCrawl4AI (Website Monitoring):")
            if status["crawl4ai_available"]:
                print("  ✅ Installed and available")
            else:
                print("  ⚠️  Not installed (run: pip install crawl4ai)")
            print()
            sys.exit(0)

    # ── Normal execution ────────────────────────────────────────────────────
    # Check if config exists
    if not DEFAULT_CONFIG.exists():
        print("\n[Startup Tracker] 配置文件不存在")
        print(f"期望路径: {DEFAULT_CONFIG}")
        print("\n首次使用？通过对话输入 '/startup-tracker' 或发送公司名称开始配置。")
        sys.exit(1)

    # Load config
    try:
        config, companies = load_config()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[Error] 加载配置失败: {e}")
        sys.exit(1)

    all_items: List[Dict] = []

    # Check configuration status
    status = check_config_status()

    print(f"\n=== Startup Tracker ===")
    print(f"监控公司: {len(companies)} 家")
    print(f"数据源状态:")
    print(f"  • Tavily新闻: {'✅' if status['tavily']['valid'] else '❌'}")
    print(f"  • 网站监控: {'✅' if CRAWL4AI_AVAILABLE else '❌'}")
    print(f"  • Twitter: ⏭️  请使用 apify-ultimate-scraper skill")
    print(f"  • LinkedIn: ⏭️  请使用 apify-ultimate-scraper skill")
    print()

    # 1. News search via Tavily
    if status['tavily']['valid']:
        try:
            all_items.extend(run_tavily(companies, config))
        except Exception as e:
            print(f"[Tavily Error] {e}")
    else:
        print("[Tavily] 跳过 - API Key 未配置或无效")

    # 2. Website monitor
    if CRAWL4AI_AVAILABLE:
        try:
            all_items.extend(run_crawl4ai(companies, config))
        except Exception as e:
            print(f"[Crawl4AI Error] {e}")
    else:
        print("[Crawl4AI] 跳过 - 未安装 (pip install crawl4ai)")

    # 3. Website monitor via Firecrawl
    if status['firecrawl']['configured']:
        try:
            all_items.extend(run_firecrawl(companies, config))
        except Exception as e:
            print(f"[Firecrawl Error] {e}")
    else:
        print("[Firecrawl] 跳过 - API Key 未配置")

    # 4. Twitter via Apify — DISABLED (api.apify.com unreachable from China)
    #   Use apify-ultimate-scraper skill instead for Twitter/LinkedIn scraping
    # if status['apify']['valid'] and status['twitter_enabled']:
    #     try:
    #         all_items.extend(run_apify_twitter(companies, config))
    #     except Exception as e:
    #         print(f"[Apify Twitter Error] {e}")
    # else:
    #     if not status['apify']['valid']:
    #         print("[Apify Twitter] 跳过 - API Token 未配置")
    #     elif not status['twitter_enabled']:
    #         print("[Apify Twitter] 跳过 - 没有配置 Twitter 账号")

    # 5. LinkedIn via Apify — DISABLED (api.apify.com unreachable from China)
    #   Use apify-ultimate-scraper skill instead for Twitter/LinkedIn scraping
    # if status['apify']['valid'] and status['linkedin_enabled']:
    #     try:
    #         all_items.extend(run_apify_linkedin(companies, config))
    #     except Exception as e:
    #         print(f"[Apify LinkedIn Error] {e}")
    # else:
    #     if not status['apify']['valid']:
    #         print("[Apify LinkedIn] 跳过 - API Token 未配置")
    #     elif not status['linkedin_enabled']:
    #         print("[Apify LinkedIn] 跳过 - 没有配置 LinkedIn URL")

    # Console summary
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== 监控报告 {today_str} ===")
    print(f"总计新增 {len(all_items)} 条动态\n")
    for it in all_items:
        tag = "🔴" if it["importance"] == "MAJOR" else "⚪"
        print(f"{tag} [{it['source']}] {it['company']}: {it['title']}")
        print(f"   {it['url']}")

    # Save raw items for LLM to generate report
    save_json(NEW_ITEMS_PATH, {"items": all_items})
    print(f"\n[Items] 已保存: {NEW_ITEMS_PATH}")
    print("[Report] 报告由 LLM 根据 SKILL.md 中的格式指引动态生成。")

    return all_items


if __name__ == "__main__":
    items = main()
