"""Compatibility entry point for the packaged odds scraper."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from football_analysis.scrape_odds_pages import main


if __name__ == "__main__":
    raise SystemExit(main())
