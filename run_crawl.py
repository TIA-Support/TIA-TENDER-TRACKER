"""
Runs the eTenders crawler and sends the tenders it finds to the
Tender Tracker website (POST /api/ingest).

Settings come from environment variables (set as GitHub secrets):
  INGEST_SECRET  - must match INGEST_SECRET on the cPanel app
  SITE_URL       - optional, defaults to https://tenders.tia-solutions.co.za
"""
import json
import os
import sys
import urllib.error
import urllib.request

from crawler.etenders_scraper import ETendersScraper

SITE_URL = os.environ.get("SITE_URL", "https://tenders.tia-solutions.co.za")
INGEST_SECRET = os.environ.get("INGEST_SECRET", "")
BATCH_SIZE = 50


def send(batch):
    body = json.dumps({"tenders": batch}).encode("utf-8")
    req = urllib.request.Request(
        SITE_URL.rstrip("/") + "/api/ingest",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {INGEST_SECRET}",
            "User-Agent": "TIA-Tender-Crawler/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    if not INGEST_SECRET:
        print("ERROR: INGEST_SECRET is not set.")
        return 1

    tenders = ETendersScraper().scrape()
    print(f"Crawler finished: {len(tenders)} ICT tenders found.")

    if not tenders:
        print("ERROR: no tenders found (the portal may have blocked the crawler).")
        return 1

    for start in range(0, len(tenders), BATCH_SIZE):
        batch = tenders[start:start + BATCH_SIZE]
        try:
            result = send(batch)
            print(f"Sent {len(batch)} tenders -> {result}")
        except urllib.error.HTTPError as exc:
            print(f"ERROR: website answered {exc.code} {exc.reason}")
            return 1
        except Exception as exc:
            print(f"ERROR: could not reach the website: {exc}")
            return 1

    print("All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
