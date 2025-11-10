import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from extractors.reviews_parser import AmazonReviewsScraper
from pipelines.exporter import ReviewExporter
from pipelines.storage_manager import StorageManager

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("amazon_reviews_scraper")

def load_settings(settings_path: str) -> Dict[str, Any]:
    """
    Load settings from JSON file. If the file does not exist or is invalid,
    sensible defaults are returned.
    """
    default_settings: Dict[str, Any] = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "max_reviews_per_product": 100,
        "request_timeout": 20,
        "proxy": None,
        "retry_count": 3,
        "sleep_between_requests": 1.0,
    }

    if not os.path.exists(settings_path):
        logger.warning("Settings file not found at %s. Using default settings.", settings_path)
        return default_settings

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            user_settings = json.load(f)
        if not isinstance(user_settings, dict):
            raise ValueError("Settings file must contain a JSON object.")
        default_settings.update(user_settings)
        logger.info("Loaded settings from %s", settings_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load settings from %s: %s. Using defaults.", settings_path, exc)
    return default_settings

def load_inputs(inputs_path: str) -> List[Dict[str, Any]]:
    """
    Load input product definitions from a JSON file.
    Expected format:
    [
        {
            "productUrl": "...",
            "maxReviews": 150   # optional
        },
        ...
    ]
    """
    if not os.path.exists(inputs_path):
        raise FileNotFoundError(f"Inputs file not found at {inputs_path}")

    with open(inputs_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Inputs file must contain a JSON array.")

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            logger.warning("Skipping non-object item at index %d in inputs file.", idx)
            continue
        url = item.get("productUrl") or item.get("url")
        if not url:
            logger.warning("Skipping entry at index %d with no 'productUrl' or 'url'.", idx)
            continue
        normalized.append(
            {
                "productUrl": url,
                "maxReviews": int(item.get("maxReviews")) if item.get("maxReviews") else None,
            }
        )
    if not normalized:
        raise ValueError("Inputs file does not contain any valid product entries.")
    return normalized

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Amazon Reviews Scraper - extract reviews from Amazon product URLs."
    )
    parser.add_argument(
        "--settings",
        default=os.path.join(os.path.dirname(__file__), "config", "settings.example.json"),
        help="Path to JSON settings file (default: src/config/settings.example.json)",
    )
    parser.add_argument(
        "--input",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "inputs.sample.json"),
        help="Path to JSON file containing product URLs (default: data/inputs.sample.json)",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "sample_output.json"),
        help="Path to JSON file where scraped reviews will be saved (default: data/sample_output.json)",
    )
    parser.add_argument(
        "--csv-output",
        default=None,
        help="Optional path to CSV file where scraped reviews will also be saved.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Single product URL to scrape. When provided, --input is ignored.",
    )
    parser.add_argument(
        "--max-reviews",
        type=int,
        default=None,
        help="Maximum number of reviews per product (overrides settings and per-input values).",
    )
    return parser.parse_args(argv)

def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    settings = load_settings(args.settings)
    storage = StorageManager()

    # Inputs handling
    if args.url:
        logger.info("Using single URL from command-line.")
        inputs = [
            {
                "productUrl": args.url,
                "maxReviews": args.max_reviews,
            }
        ]
    else:
        logger.info("Loading inputs from %s", args.input)
        inputs = load_inputs(args.input)
        if args.max_reviews is not None:
            for it in inputs:
                it["maxReviews"] = args.max_reviews

    scraper = AmazonReviewsScraper(
        user_agent=settings.get("user_agent"),
        timeout=settings.get("request_timeout", 20),
        proxy=settings.get("proxy"),
        retry_count=settings.get("retry_count", 3),
        sleep_between_requests=float(settings.get("sleep_between_requests", 1.0)),
    )

    all_reviews: List[Dict[str, Any]] = []
    for item in inputs:
        url = item["productUrl"]
        max_reviews = item.get("maxReviews") or settings.get("max_reviews_per_product", 100)
        try:
            logger.info("Scraping product: %s (max_reviews=%s)", url, max_reviews)
            product_reviews = scraper.scrape_product_reviews(url, max_reviews=max_reviews)
            all_reviews.extend(product_reviews)
            logger.info("Collected %d reviews for %s", len(product_reviews), url)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to scrape %s: %s", url, exc, exc_info=True)

    if not all_reviews:
        logger.warning("No reviews were collected. Exiting without writing output.")
        return

    # Persist results
    out_json_path = os.path.abspath(args.output)
    storage.ensure_directory(os.path.dirname(out_json_path))
    exporter = ReviewExporter(storage=storage)

    exporter.to_json(all_reviews, out_json_path)
    logger.info("Saved %d reviews to %s", len(all_reviews), out_json_path)

    if args.csv_output:
        out_csv_path = os.path.abspath(args.csv_output)
        storage.ensure_directory(os.path.dirname(out_csv_path))
        exporter.to_csv(all_reviews, out_csv_path)
        logger.info("Saved %d reviews to %s", len(all_reviews), out_csv_path)

    logger.info("Scraping completed successfully.")

if __name__ == "__main__":
    main()