from __future__ import annotations
import json
import os
import re
import time
import logging
from urllib.parse import quote

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FIVERR_BASE = "https://www.fiverr.com"
_IMPERSONATE = "chrome131"


def make_affiliate_url(fiverr_url: str, bta: str, brand: str) -> str:
    clean = fiverr_url.split("?")[0]
    if not clean.startswith("http"):
        clean = FIVERR_BASE + clean
    once = quote(clean, safe="")
    twice = quote(once, safe="")
    return f"https://go.fiverr.com/visit/?bta={bta}&brand={brand}&landingPage={twice}"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text)[:120].strip("-")


def _make_session(session_cookie: str = ""):
    import curl_cffi.requests as cf
    session = cf.Session(impersonate=_IMPERSONATE)
    if session_cookie:
        for part in session_cookie.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                session.cookies.set(k.strip(), v.strip(), domain=".fiverr.com")
    return session


def _warmup(session) -> bool:
    """Hit Fiverr homepage first to acquire any challenge cookies."""
    try:
        r = session.get(
            FIVERR_BASE,
            headers=_headers(),
            timeout=20,
        )
        logger.info("Warmup status: %s", r.status_code)
        return r.status_code == 200
    except Exception as e:
        logger.warning("Warmup failed: %s", e)
        return False


def _headers(referer: str = FIVERR_BASE) -> dict:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _fetch(session, url: str, referer: str = FIVERR_BASE) -> str | None:
    try:
        r = session.get(url, headers=_headers(referer), timeout=25)
        if r.status_code == 200:
            return r.text
        logger.warning("HTTP %s for %s", r.status_code, url)
        if r.status_code == 403:
            logger.warning("403 — Fiverr blocked this request. Set FIVERR_SESSION_COOKIE env var.")
    except Exception as e:
        logger.error("Fetch error %s: %s", url, e)
    return None


# ── Data extraction ───────────────────────────────────────────────────────────

def _extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _gigs_from_next_data(data: dict, category: str) -> list[dict]:
    pp = data.get("props", {}).get("pageProps", {})
    candidates = [
        pp.get("initialData", {}).get("listings", []),
        pp.get("initialData", {}).get("results", {}).get("gigs", []),
        pp.get("results", {}).get("gigs", []),
        pp.get("gigs", []),
        pp.get("categoryData", {}).get("gigs", []),
        # category page structure
        pp.get("initialProps", {}).get("categoryItems", []),
    ]
    for lst in candidates:
        if lst:
            return [g for g in (_parse_next_gig(raw, category) for raw in lst) if g]
    return []


def _parse_next_gig(g: dict, category: str) -> dict | None:
    try:
        gig_url = (g.get("gig_url") or g.get("url") or g.get("gigUrl") or "").strip()
        if not gig_url:
            return None
        if not gig_url.startswith("http"):
            gig_url = FIVERR_BASE + gig_url

        title = g.get("title") or gig_url.split("/")[-1].replace("-", " ").title()
        seller = (
            g.get("seller_name") or g.get("username") or g.get("sellerName")
            or gig_url.rstrip("/").split("/")[-2]
        )

        rating_obj = g.get("rating") or {}
        if isinstance(rating_obj, dict):
            rating = float(rating_obj.get("rating_star", 0) or rating_obj.get("score", 0) or 0)
            rating_count = int(rating_obj.get("rating_count", 0) or rating_obj.get("count", 0) or 0)
        else:
            rating = float(rating_obj or 0)
            rating_count = int(g.get("rating_count", 0) or 0)

        price_obj = g.get("price") or {}
        starting_price = float(
            (price_obj.get("starting_at", {}).get("value") if isinstance(price_obj, dict) else None)
            or price_obj if isinstance(price_obj, (int, float)) else 0
        )

        image_url = (
            g.get("gig_image_url") or g.get("image_url") or
            g.get("gigImageUrl") or g.get("thumbnail") or ""
        )
        seller_level = g.get("seller_level") or g.get("sellerLevel") or ""
        description = g.get("description") or g.get("gig_description") or ""

        return {
            "title": str(title)[:300],
            "slug": slugify(f"{seller} {title}"),
            "url": gig_url,
            "seller": str(seller),
            "seller_level": str(seller_level),
            "rating": rating,
            "rating_count": rating_count,
            "starting_price": starting_price,
            "image_url": str(image_url),
            "category": category,
            "description": str(description)[:500],
        }
    except Exception as e:
        logger.debug("parse_next_gig error: %s", e)
        return None


def _gigs_from_soup(html: str, category: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    results = []

    # Strategy 1: data-impression-data JSON attributes
    for card in soup.find_all(attrs={"data-impression-data": True}):
        g = _parse_impression_card(card, category)
        if g:
            results.append(g)
    if results:
        return results

    # Strategy 2: article elements or divs with gig-card class
    cards = (
        soup.find_all("article")
        or soup.find_all(attrs={"data-testid": re.compile(r"gig|listing", re.I)})
        or soup.find_all("li", class_=re.compile(r"gig|card|listing", re.I))
    )
    for card in cards:
        g = _parse_card_element(card, category)
        if g:
            results.append(g)

    return results


def _parse_impression_card(card, category: str) -> dict | None:
    try:
        link = card.find("a", href=re.compile(r"^/[^/]+/[^/?]+"))
        if not link:
            return None
        href = link["href"].split("?")[0]
        gig_url = FIVERR_BASE + href if href.startswith("/") else href
        seller = href.strip("/").split("/")[0]

        img = card.find("img")
        image_url = (img.get("src") or img.get("data-src") or "") if img else ""

        title_el = card.find(["h3", "h2", "p"], class_=re.compile(r"title|name|gig", re.I))
        title = (title_el.get_text(strip=True) if title_el
                 else href.split("/")[-1].replace("-", " ").title())

        rating, rating_count = _extract_rating(card)
        price = _extract_price(card)
        level = _extract_level(card)

        return {
            "title": title[:300],
            "slug": slugify(f"{seller} {title}"),
            "url": gig_url,
            "seller": seller,
            "seller_level": level,
            "rating": rating,
            "rating_count": rating_count,
            "starting_price": price,
            "image_url": image_url,
            "category": category,
            "description": "",
        }
    except Exception:
        return None


def _parse_card_element(card, category: str) -> dict | None:
    try:
        link = card.find("a", href=re.compile(r"^/[^/]+/[^/?]+"))
        if not link:
            return None
        href = link["href"].split("?")[0]
        gig_url = FIVERR_BASE + href if href.startswith("/") else href
        seller = href.strip("/").split("/")[0]
        if not seller or seller in ("categories", "search", "pro"):
            return None

        img = card.find("img")
        image_url = (img.get("src") or img.get("data-src") or "") if img else ""

        title = (link.get("title") or link.get_text(strip=True)
                 or href.split("/")[-1].replace("-", " ").title())
        if len(title) < 5:
            title = href.split("/")[-1].replace("-", " ").title()

        rating, rating_count = _extract_rating(card)
        price = _extract_price(card)
        level = _extract_level(card)

        return {
            "title": title[:300],
            "slug": slugify(f"{seller} {title}"),
            "url": gig_url,
            "seller": seller,
            "seller_level": level,
            "rating": rating,
            "rating_count": rating_count,
            "starting_price": price,
            "image_url": image_url,
            "category": category,
            "description": "",
        }
    except Exception:
        return None


def _extract_rating(card) -> tuple[float, int]:
    text = card.get_text(" ", strip=True)
    m = re.search(r"\b(4\.[5-9]|5\.0)\b", text)
    rating = float(m.group(1)) if m else 0.0
    m2 = re.search(r"\((\d+(?:\.\d+)?[kK]?)\)", text)
    if m2:
        raw = m2.group(1).lower()
        try:
            rating_count = int(float(raw.replace("k", "")) * 1000) if "k" in raw else int(raw)
        except ValueError:
            rating_count = 0
    else:
        rating_count = 0
    return rating, rating_count


def _extract_price(card) -> float:
    m = re.search(r"\$(\d+(?:\.\d+)?)", card.get_text(" ", strip=True))
    return float(m.group(1)) if m else 0.0


def _extract_level(card) -> str:
    text = card.get_text(" ", strip=True).lower()
    for label in ("top rated seller", "level two seller", "level one seller", "pro"):
        if label in text:
            return label.title()
    return ""


def _deduplicate(gigs: list[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    seen_slugs: set[str] = set()
    out = []
    for g in gigs:
        url = g["url"].split("?")[0]
        if url in seen_urls:
            continue
        slug, i = g["slug"], 2
        while slug in seen_slugs:
            slug = f"{g['slug']}-{i}"
            i += 1
        g["slug"] = slug
        g["url"] = url
        seen_urls.add(url)
        seen_slugs.add(slug)
        out.append(g)
    return out


# ── Public entry point ────────────────────────────────────────────────────────

def scrape_fiverr(
    bta: str,
    brand: str,
    category_urls: list[tuple[str, str]],
    pages_per_category: int = 2,
    min_rating: float = 0.0,
    min_reviews: int = 0,
) -> list[dict]:
    session_cookie = os.environ.get("FIVERR_SESSION_COOKIE", "")
    session = _make_session(session_cookie)

    # Warmup: acquire CF cookies by hitting homepage first
    _warmup(session)
    time.sleep(1.5)

    all_gigs: list[dict] = []

    for category_label, base_url in category_urls:
        logger.info("Scraping: %s", category_label)

        for page in range(1, pages_per_category + 1):
            url = f"{base_url}?page={page}" if page > 1 else base_url
            html = _fetch(session, url, referer=FIVERR_BASE)
            if not html:
                break

            # Try __NEXT_DATA__ first, fall back to HTML soup
            nd = _extract_next_data(html)
            gigs = _gigs_from_next_data(nd, category_label) if nd else []
            if not gigs:
                gigs = _gigs_from_soup(html, category_label)

            logger.info("  page %d: %d gigs", page, len(gigs))

            for g in gigs:
                g["affiliate_url"] = make_affiliate_url(g["url"], bta, brand)

            filtered = [
                g for g in gigs
                if (g["rating"] == 0 or g["rating"] >= min_rating)
                and (g["rating_count"] == 0 or g["rating_count"] >= min_reviews)
            ]
            all_gigs.extend(filtered)

            if page < pages_per_category:
                time.sleep(2)

        time.sleep(3)

    return _deduplicate(all_gigs)
