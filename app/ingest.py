from __future__ import annotations
import logging

from .config import get_settings, CATEGORY_URLS
from .db import init_db, upsert_gigs
from .scraper import scrape_fiverr

logger = logging.getLogger(__name__)


def run_ingest() -> int:
    cfg = get_settings()
    init_db(cfg.db_path)
    logger.info("Starting Fiverr scrape (%d categories, %d pages each)", len(CATEGORY_URLS), cfg.scrape_pages)

    gigs = scrape_fiverr(
        bta=cfg.affiliate_bta,
        brand=cfg.affiliate_brand,
        category_urls=CATEGORY_URLS,
        pages_per_category=cfg.scrape_pages,
        min_rating=cfg.min_rating,
        min_reviews=cfg.min_reviews,
    )
    logger.info("Scraped %d gigs total", len(gigs))

    inserted = upsert_gigs(cfg.db_path, gigs)
    logger.info("Inserted %d new gigs", inserted)
    return inserted
