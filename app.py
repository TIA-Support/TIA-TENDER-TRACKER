"""
ICT Tender Crawler — Flask backend
"""
import json
import os
import sqlite3
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from crawler.etenders_scraper import ETendersScraper

app = Flask(__name__)

DB_PATH = os.path.join(
    os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__))),
    "tenders.db",
)


def _parse_closing_date_iso(date_str: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD for proper SQL sorting. Returns '' on failure."""
    if not date_str:
        return ""
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


crawl_state = {
    "running": False,
    "last_crawl": None,
    "message": "No crawl has run yet.",
    "found": 0,
}
_crawl_lock = threading.Lock()


# ------------------------------------------------------------------ #
#  Database helpers                                                    #
# ------------------------------------------------------------------ #

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tenders (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            tender_number    TEXT,
            title            TEXT    NOT NULL,
            issuing_org      TEXT,
            closing_date     TEXT,
            closing_time     TEXT,
            briefing_details TEXT,
            document_url     TEXT,
            source_url       TEXT,
            category         TEXT,
            advertised_date  TEXT,
            closing_date_iso TEXT,
            created_at       TEXT DEFAULT (datetime('now','localtime')),
            updated_at       TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(title, issuing_org)
        )
        """
    )
    # Migrate existing databases: add closing_date_iso if absent
    try:
        c.execute("ALTER TABLE tenders ADD COLUMN closing_date_iso TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    # Migrate: add document_urls column if absent
    try:
        c.execute("ALTER TABLE tenders ADD COLUMN document_urls TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    # Backfill ISO dates for all records that are missing them
    rows = c.execute(
        "SELECT id, closing_date FROM tenders WHERE closing_date_iso IS NULL OR closing_date_iso = ''"
    ).fetchall()
    for rid, cd in rows:
        iso = _parse_closing_date_iso(cd)
        if iso:
            c.execute("UPDATE tenders SET closing_date_iso = ? WHERE id = ?", (iso, rid))
    conn.commit()
    conn.close()


def _upsert_tenders(tenders: list) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    count = 0
    for t in tenders:
        try:
            closing_date_iso = _parse_closing_date_iso(t.get("closing_date", ""))
            # Normalise document_urls: prefer the scraped JSON list, else wrap single URL
            raw_doc_urls = t.get("document_urls")
            if raw_doc_urls is None:
                single = t.get("document_url", "")
                raw_doc_urls = json.dumps([single] if single else [])
            c.execute(
                """
                INSERT INTO tenders
                    (tender_number, title, issuing_org, closing_date, closing_time,
                     briefing_details, document_url, source_url, category, advertised_date,
                     closing_date_iso, document_urls, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                ON CONFLICT(title, issuing_org) DO UPDATE SET
                    tender_number    = excluded.tender_number,
                    closing_date     = excluded.closing_date,
                    closing_time     = excluded.closing_time,
                    briefing_details = excluded.briefing_details,
                    document_url     = excluded.document_url,
                    source_url       = excluded.source_url,
                    category         = excluded.category,
                    advertised_date  = excluded.advertised_date,
                    closing_date_iso = excluded.closing_date_iso,
                    document_urls    = excluded.document_urls,
                    updated_at       = excluded.updated_at
                """,
                (
                    t.get("tender_number", ""),
                    t.get("title", ""),
                    t.get("issuing_org", ""),
                    t.get("closing_date", ""),
                    t.get("closing_time", ""),
                    t.get("briefing_details", ""),
                    t.get("document_url", ""),
                    t.get("source_url", ""),
                    t.get("category", "ICT"),
                    t.get("advertised_date", ""),
                    closing_date_iso,
                    raw_doc_urls,
                ),
            )
            count += 1
        except Exception as exc:
            print(f"[DB] Insert error: {exc}")
    conn.commit()
    conn.close()
    return count


# ------------------------------------------------------------------ #
#  Background crawl worker                                            #
# ------------------------------------------------------------------ #

def _crawl_worker():
    global crawl_state
    with _crawl_lock:
        crawl_state["running"] = True
        crawl_state["message"] = "Crawling eTenders portal…"

    try:
        scraper = ETendersScraper()
        tenders = scraper.scrape()
        saved = _upsert_tenders(tenders)

        with _crawl_lock:
            crawl_state["found"] = saved
            crawl_state["last_crawl"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            crawl_state["message"] = (
                f"Crawl complete — {len(tenders)} ICT tenders found, {saved} saved/updated."
            )
    except Exception as exc:
        with _crawl_lock:
            crawl_state["message"] = f"Crawl failed: {exc}"
        print(f"[Crawl] Error: {exc}")
    finally:
        with _crawl_lock:
            crawl_state["running"] = False


# ------------------------------------------------------------------ #
#  Routes                                                             #
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tenders")
def api_tenders():
    search   = request.args.get("search", "").strip()
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(50, max(6, int(request.args.get("per_page", 12))))
    offset   = (page - 1) * per_page

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if search:
        like = f"%{search}%"
        rows = c.execute(
            """
            SELECT * FROM tenders
            WHERE (closing_date_iso IS NULL OR closing_date_iso = ''
                   OR closing_date_iso >= date('now','localtime'))
              AND (title LIKE ? OR tender_number LIKE ?
                   OR issuing_org LIKE ? OR briefing_details LIKE ? OR category LIKE ?)
            ORDER BY CASE WHEN closing_date_iso IS NULL OR closing_date_iso = ''
                          THEN '9999-12-31' ELSE closing_date_iso END ASC,
                     updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, like, like, like, per_page, offset),
        ).fetchall()
        total = c.execute(
            """
            SELECT COUNT(*) FROM tenders
            WHERE (closing_date_iso IS NULL OR closing_date_iso = ''
                   OR closing_date_iso >= date('now','localtime'))
              AND (title LIKE ? OR tender_number LIKE ?
                   OR issuing_org LIKE ? OR briefing_details LIKE ? OR category LIKE ?)
            """,
            (like, like, like, like, like),
        ).fetchone()[0]
    else:
        rows = c.execute(
            """
            SELECT * FROM tenders
            WHERE closing_date_iso IS NULL OR closing_date_iso = ''
               OR closing_date_iso >= date('now','localtime')
            ORDER BY CASE WHEN closing_date_iso IS NULL OR closing_date_iso = ''
                          THEN '9999-12-31' ELSE closing_date_iso END ASC,
                     updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
        total = c.execute(
            """
            SELECT COUNT(*) FROM tenders
            WHERE closing_date_iso IS NULL OR closing_date_iso = ''
               OR closing_date_iso >= date('now','localtime')
            """
        ).fetchone()[0]

    conn.close()

    return jsonify(
        {
            "tenders": [dict(r) for r in rows],
            "total":   total,
            "page":    page,
            "per_page": per_page,
            "pages":   max(1, (total + per_page - 1) // per_page),
        }
    )


@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    with _crawl_lock:
        if crawl_state["running"]:
            return jsonify({"status": "already_running", "message": "A crawl is already in progress."})

    t = threading.Thread(target=_crawl_worker, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Crawl started in background."})


@app.route("/api/tenders/<int:tender_id>", methods=["DELETE"])
def api_delete_tender(tender_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tenders WHERE id = ?", (tender_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    if affected:
        return jsonify({"status": "deleted"})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/status")
def api_status():
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    conn.close()
    with _crawl_lock:
        state = dict(crawl_state)
    state["total_tenders"] = total
    return jsonify(state)


# ------------------------------------------------------------------ #
#  Entry point                                                        #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    init_db()
    # Kick off first crawl automatically
    t = threading.Thread(target=_crawl_worker, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False)
