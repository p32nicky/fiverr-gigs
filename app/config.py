from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    db_path: str
    site_title: str
    site_url: str
    affiliate_bta: str
    affiliate_brand: str
    min_rating: float
    min_reviews: int
    scrape_pages: int
    timezone: str
    daily_hour: int
    daily_minute: int


def get_settings() -> Settings:
    db_path = os.environ.get("DB_PATH", "./data/fiverr.sqlite3")
    database_url = os.environ.get("DATABASE_URL", "")

    site_url = os.environ.get("SITE_URL", "")
    if not site_url and os.environ.get("VERCEL_URL"):
        site_url = f"https://{os.environ['VERCEL_URL']}"

    return Settings(
        db_path=database_url if database_url else db_path,
        site_title=os.environ.get("SITE_TITLE", "Top Fiverr Gigs"),
        site_url=site_url,
        affiliate_bta=os.environ.get("AFFILIATE_BTA", "1029859"),
        affiliate_brand=os.environ.get("AFFILIATE_BRAND", "fiverrmarketplace"),
        min_rating=float(os.environ.get("MIN_RATING", "4.8")),
        min_reviews=int(os.environ.get("MIN_REVIEWS", "5")),
        scrape_pages=int(os.environ.get("SCRAPE_PAGES", "2")),
        timezone=os.environ.get("TIMEZONE", "America/New_York"),
        daily_hour=int(os.environ.get("DAILY_HOUR_LOCAL", "7")),
        daily_minute=int(os.environ.get("DAILY_MINUTE_LOCAL", "0")),
    )


# Category browse pages — less protected than /search/gigs
CATEGORY_URLS = [
    ("Web Development",      "https://www.fiverr.com/categories/programming-tech/web-programming"),
    ("Logo Design",          "https://www.fiverr.com/categories/graphics-design/logo-design"),
    ("SEO",                  "https://www.fiverr.com/categories/digital-marketing/search-engine-optimization"),
    ("Social Media",         "https://www.fiverr.com/categories/digital-marketing/social-media-marketing"),
    ("Video Editing",        "https://www.fiverr.com/categories/video-animation/video-editing"),
    ("Graphic Design",       "https://www.fiverr.com/categories/graphics-design"),
    ("Content Writing",      "https://www.fiverr.com/categories/writing-translation/articles-blog-posts"),
    ("WordPress",            "https://www.fiverr.com/categories/programming-tech/wordpress-development"),
    ("eCommerce",            "https://www.fiverr.com/categories/programming-tech/ecommerce-development"),
    ("App Development",      "https://www.fiverr.com/categories/programming-tech/mobile-apps"),
    ("AI Services",          "https://www.fiverr.com/categories/ai-services"),
    ("Voice Over",           "https://www.fiverr.com/categories/music-audio/voice-over"),
    ("Translation",          "https://www.fiverr.com/categories/writing-translation/translation"),
    ("Business Consulting",  "https://www.fiverr.com/categories/business/business-plans"),
]
