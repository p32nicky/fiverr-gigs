"""
seed.py — run locally to scrape Fiverr and populate the database.

Uses your real installed Chrome browser (not headless Chromium) with stealth
patches to bypass PerimeterX / Cloudflare bot detection.

Usage:
    python seed.py              # headed Chrome window (default)
    python seed.py --headless   # headless (may get blocked)
    python seed.py --pages 3    # scrape 3 pages per category (default: 2)
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import re
import sys
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

from app.config import get_settings, CATEGORY_URLS
from app.db import init_db, upsert_gigs
from app.scraper import make_affiliate_url, slugify, _deduplicate

FIVERR_BASE = "https://www.fiverr.com"


def _is_blocked(html: str) -> bool:
    return any(x in html for x in ("PXCR", "px-captcha", "Access Denied", "_pxCaptcha", "challenge-form"))


async def _wait_if_blocked(page) -> None:
    html = await page.content()
    if _is_blocked(html):
        print("\n" + "="*60)
        print("CAPTCHA detected! Solve it in the browser window.")
        print("="*60)
        input("Press ENTER once Fiverr loads normally... ")
        await page.wait_for_timeout(2000)


async def scrape_category(page, category_label: str, base_url: str, max_pages: int) -> list[dict]:
    gigs: list[dict] = []
    for pg in range(1, max_pages + 1):
        url = f"{base_url}?page={pg}" if pg > 1 else base_url
        logger.info("  → %s page %d", category_label, pg)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_timeout(4000)
        except Exception as e:
            logger.warning("  Navigation error: %s", e)
            break

        await _wait_if_blocked(page)

        page_gigs = await _extract_gigs(page, category_label)
        logger.info("  found %d gigs", len(page_gigs))
        gigs.extend(page_gigs)

        if pg < max_pages:
            await page.wait_for_timeout(3000)

    return gigs


async def _extract_gigs(page, category: str) -> list[dict]:
    # 1. Try __NEXT_DATA__ JSON
    nd_json = await page.evaluate("""() => {
        const el = document.getElementById('__NEXT_DATA__');
        return el ? el.textContent : null;
    }""")
    if nd_json:
        try:
            nd = json.loads(nd_json)
            gigs = _gigs_from_next_data(nd, category)
            if gigs:
                return gigs
        except Exception as e:
            logger.debug("__NEXT_DATA__ parse error: %s", e)

    # 2. Fall back: extract links from rendered DOM
    return await _gigs_from_dom(page, category)


def _gigs_from_next_data(data: dict, category: str) -> list[dict]:
    pp = data.get("props", {}).get("pageProps", {})
    candidates = [
        pp.get("initialData", {}).get("listings", []),
        pp.get("initialData", {}).get("results", {}).get("gigs", []),
        pp.get("results", {}).get("gigs", []),
        pp.get("gigs", []),
        pp.get("categoryData", {}).get("gigs", []),
    ]
    for lst in candidates:
        if lst:
            return [g for g in (_parse_next_gig(g, category) for g in lst) if g]
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
            rating = float(rating_obj.get("rating_star") or rating_obj.get("score") or 0)
            rating_count = int(rating_obj.get("rating_count") or rating_obj.get("count") or 0)
        else:
            rating = float(rating_obj or 0)
            rating_count = int(g.get("rating_count") or 0)

        price_obj = g.get("price") or {}
        if isinstance(price_obj, dict):
            starting_price = float(
                price_obj.get("starting_at", {}).get("value") or
                price_obj.get("value") or 0
            )
        else:
            starting_price = float(price_obj or 0)

        return {
            "title": str(title)[:300],
            "slug": slugify(f"{seller} {title}"),
            "url": gig_url,
            "seller": str(seller),
            "seller_level": str(g.get("seller_level") or g.get("sellerLevel") or ""),
            "rating": rating,
            "rating_count": rating_count,
            "starting_price": starting_price,
            "image_url": str(g.get("gig_image_url") or g.get("image_url") or g.get("gigImageUrl") or g.get("thumbnail") or ""),
            "category": category,
            "description": str(g.get("description") or "")[:500],
        }
    except Exception as e:
        logger.debug("_parse_next_gig: %s", e)
        return None


async def _gigs_from_dom(page, category: str) -> list[dict]:
    try:
        cards = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const skip = new Set(['categories','search','pro','pages','about','signin','register','jobs']);
            for (const a of document.querySelectorAll('a[href^="/"]')) {
                const href = a.getAttribute('href').split('?')[0];
                if (!/^\\/[a-z0-9_-]+\\/[a-z0-9_-]{8,}$/.test(href)) continue;
                const parts = href.replace(/^\\//, '').split('/');
                if (skip.has(parts[0])) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                let node = a;
                for (let i = 0; i < 8; i++) { node = node.parentElement; if (!node) break; }
                const text = node ? node.innerText : a.innerText;
                // Only accept real Fiverr CDN images
                let imgUrl = '';
                const imgs = (node || a).querySelectorAll('img');
                for (const img of imgs) {
                    const src = img.src || img.dataset.src || '';
                    if (src.includes('fiverr') || src.includes('cloudinary')) {
                        imgUrl = src;
                        break;
                    }
                }
                results.push({ href, text: text || '', imgUrl });
            }
            return results;
        }""")
        gigs = []
        for c in (cards or []):
            href = c.get("href", "")
            parts = href.strip("/").split("/")
            if len(parts) < 2:
                continue
            seller, raw_slug = parts[0], parts[1]
            title = raw_slug.replace("-", " ").title()
            text = c.get("text", "")
            rating, rc = _rating(text)
            gigs.append({
                "title": title[:300],
                "slug": slugify(f"{seller} {title}"),
                "url": f"{FIVERR_BASE}{href}",
                "seller": seller,
                "seller_level": _level(text),
                "rating": rating,
                "rating_count": rc,
                "starting_price": _price(text),
                "image_url": c.get("imgUrl", ""),
                "category": category,
                "description": "",
            })
        return gigs
    except Exception as e:
        logger.warning("DOM extraction: %s", e)
        return []


def _rating(text: str) -> tuple[float, int]:
    m = re.search(r"\b(4\.[5-9]|5\.0)\b", text)
    rating = float(m.group(1)) if m else 0.0
    m2 = re.search(r"\((\d+(?:\.\d+)?[kK]?)\)", text)
    if m2:
        raw = m2.group(1).lower()
        try:
            count = int(float(raw.replace("k", "")) * 1000) if "k" in raw else int(raw)
        except ValueError:
            count = 0
    else:
        count = 0
    return rating, count


def _price(text: str) -> float:
    m = re.search(r"\$(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else 0.0


def _level(text: str) -> str:
    t = text.lower()
    for l in ("top rated seller", "level two seller", "level one seller", "pro"):
        if l in t:
            return l.title()
    return ""


async def run(headless: bool, max_pages: int):
    from playwright.async_api import async_playwright

    cfg = get_settings()
    init_db(cfg.db_path)
    all_gigs: list[dict] = []

    # Persistent profile — cookies/session saved between runs.
    # Solve CAPTCHA once; subsequent runs reuse the saved session.
    profile_dir = os.path.join(os.path.dirname(__file__), "fiverr_profile")
    os.makedirs(profile_dir, exist_ok=True)

    async with async_playwright() as pw:
        launch_kwargs = dict(
            user_data_dir=profile_dir,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = await pw.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
            logger.info("Using real Google Chrome with saved profile")
        except Exception:
            ctx = await pw.chromium.launch_persistent_context(**launch_kwargs)
            logger.info("Using bundled Chromium with saved profile")

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Warmup — let user solve any CAPTCHA before we start scraping
        logger.info("Loading fiverr.com...")
        await page.goto(FIVERR_BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        print("\n" + "="*60)
        print("Fiverr is open in the browser window.")
        print("If you see a CAPTCHA or block page, solve it now.")
        print("Once fiverr.com loads normally, come back here.")
        print("="*60)
        input("Press ENTER when ready to start scraping... ")
        print()

        for category_label, base_url in CATEGORY_URLS:
            logger.info("=== %s ===", category_label)
            gigs = await scrape_category(page, category_label, base_url, max_pages)
            for g in gigs:
                g["affiliate_url"] = make_affiliate_url(g["url"], cfg.affiliate_bta, cfg.affiliate_brand)
            all_gigs.extend(gigs)
            logger.info("Running total: %d", len(all_gigs))
            await page.wait_for_timeout(2500)

        await ctx.close()

    unique = _deduplicate(all_gigs)
    inserted = upsert_gigs(cfg.db_path, unique)
    print(f"\nDone — {inserted} new gigs added ({len(unique)} unique scraped).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="Run headless (may get blocked)")
    parser.add_argument("--pages", type=int, default=2, help="Pages per category")
    args = parser.parse_args()
    asyncio.run(run(headless=args.headless, max_pages=args.pages))
