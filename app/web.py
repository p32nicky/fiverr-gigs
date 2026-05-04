from __future__ import annotations
import logging
import math
import threading
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .db import init_db, list_gigs, get_gig_by_slug, list_categories, get_feed_gigs

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Top Fiverr Gigs")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
def on_startup():
    cfg = get_settings()
    init_db(cfg.db_path)

    # Start background scheduler (skipped on Vercel - uses /api/cron instead)
    import os
    if not os.environ.get("VERCEL"):
        from .scheduler import get_scheduler
        sched = get_scheduler()
        sched.start()

    # Trigger first ingest in background if DB is empty
    def _initial_ingest():
        from .db import list_gigs as _list
        gigs, total = _list(cfg.db_path, page=1, per_page=1)
        if total == 0:
            logger.info("Empty DB — running initial ingest")
            from .ingest import run_ingest
            run_ingest()

    t = threading.Thread(target=_initial_ingest, daemon=True)
    t.start()


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = "",
    category: str = "",
    page: int = 1,
):
    cfg = get_settings()
    per_page = 24
    gigs, total = list_gigs(cfg.db_path, query=q, category=category, page=page, per_page=per_page)
    categories = list_categories(cfg.db_path)
    total_pages = max(1, math.ceil(total / per_page))

    return templates.TemplateResponse(request, "index.html", {
        "gigs": gigs,
        "categories": categories,
        "query": q,
        "category": category,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "site_title": cfg.site_title,
        "site_url": cfg.site_url,
    })


@app.get("/gig/{slug}", response_class=HTMLResponse)
def gig_detail(request: Request, slug: str):
    cfg = get_settings()
    gig = get_gig_by_slug(cfg.db_path, slug)
    if not gig:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)

    return templates.TemplateResponse(request, "gig.html", {
        "gig": gig,
        "site_title": cfg.site_title,
        "site_url": cfg.site_url,
    })


@app.get("/feed.xml")
def rss_feed():
    cfg = get_settings()
    now = datetime.now(timezone.utc)
    day_of_year = now.timetuple().tm_yday
    gigs = get_feed_gigs(cfg.db_path, limit=10000, offset=0)

    site_url = cfg.site_url or "https://example.com"
    items_xml = ""
    for i, g in enumerate(gigs):
        pub_dt = now - timedelta(hours=i * 2)
        pub_str = format_datetime(pub_dt)
        guid = f"{site_url}/gig/{escape(g['slug'])}?d={now.date()}"

        image_url = g.get("image_url", "")
        media_tag = f'<media:content url="{escape(image_url)}" medium="image"/>' if image_url else ""

        title = escape(g["title"])
        affiliate = escape(g["affiliate_url"])
        seller = escape(g.get("seller", ""))
        rating = g.get("rating", 0)
        price = g.get("starting_price", 0)
        category = escape(g.get("category", ""))

        rating_str = f"⭐ {rating:.1f}" if rating else "Top Rated"
        price_str = f" | From ${price:.0f}" if price else ""

        desc_parts = [
            f"{rating_str}{price_str}",
            f"Seller: {seller}" if seller else "",
            f"Category: {category}" if category else "",
            g.get("description", ""),
            f'<a href="{affiliate}">Get this Fiverr gig →</a>',
        ]
        description = " | ".join(p for p in desc_parts if p)

        items_xml += f"""
  <item>
    <title>{title}</title>
    <link>{affiliate}</link>
    <description><![CDATA[{description}]]></description>
    <pubDate>{pub_str}</pubDate>
    <guid isPermaLink="false">{guid}</guid>
    {media_tag}
  </item>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:media="http://search.yahoo.com/mrss/"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(cfg.site_title)}</title>
    <link>{escape(site_url)}</link>
    <description>Top-rated Fiverr gigs updated daily</description>
    <language>en-us</language>
    <lastBuildDate>{format_datetime(now)}</lastBuildDate>
    <atom:link href="{escape(site_url)}/feed.xml" rel="self" type="application/rss+xml"/>
    {items_xml}
  </channel>
</rss>"""

    return Response(
        content=xml,
        media_type="application/rss+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/cron")
def cron():
    """Called daily by Vercel cron job."""
    from .ingest import run_ingest
    try:
        inserted = run_ingest()
        return JSONResponse({"ok": True, "inserted": inserted})
    except Exception as e:
        logger.error("Cron ingest error: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/ingest")
def manual_ingest():
    import os
    if os.environ.get("VERCEL"):
        from .ingest import run_ingest
        inserted = run_ingest()
        return JSONResponse({"ok": True, "inserted": inserted})
    from .scheduler import get_scheduler
    get_scheduler().trigger_now()
    return JSONResponse({"ok": True, "message": "Ingest triggered"})


@app.get("/api/status")
def status():
    import os
    if os.environ.get("VERCEL"):
        return JSONResponse({"mode": "vercel_cron"})
    from .scheduler import get_scheduler
    return JSONResponse(get_scheduler().get_state())


# ── Helpers ───────────────────────────────────────────────────────────────────


def _total_gigs(db_path: str) -> int:
    _, total = list_gigs(db_path, page=1, per_page=1)
    return total or 1
