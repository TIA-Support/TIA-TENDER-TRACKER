"""
ICT Tender Crawler — Flask backend (PostgreSQL / Neon)
"""
import json
import os
import threading
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, render_template, request

from crawler.etenders_scraper import ETendersScraper

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# South African date for closing-date comparisons (SAST = UTC+2).
# Using a SQL expression so the DB always computes the correct local date
# regardless of which timezone the Neon server runs in.
_SA_TODAY = "TO_CHAR(NOW() AT TIME ZONE 'Africa/Johannesburg', 'YYYY-MM-DD')"

# Regex pattern for online briefings — used by both online and physical filters
_ONLINE_PATTERN = (
    "microsoft teams|ms teams|teams meeting|teams link|join.{0,20}teams"
    "|zoom\\.us|zoom meeting|zoom link|join.{0,20}zoom"
    "|google meet|meet\\.google"
    "|webex|skype for business"
    "|virtual|virtual briefing|virtual meeting|virtual session"
    "|meeting link|briefing link|online meeting|online briefing"
    "|join via link|click.{0,20}join|link to join|teams invite"
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


def _get_conn():
    """Return a new psycopg2 connection to Neon."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tenders (
            id               SERIAL PRIMARY KEY,
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
            document_urls    TEXT DEFAULT '[]',
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            updated_at       TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(title, issuing_org)
        )
        """
    )
    conn.commit()

    # Backfill ISO dates for any rows that are missing them
    c.execute(
        "SELECT id, closing_date FROM tenders WHERE closing_date_iso IS NULL OR closing_date_iso = ''"
    )
    rows = c.fetchall()
    for rid, cd in rows:
        iso = _parse_closing_date_iso(cd)
        if iso:
            c.execute("UPDATE tenders SET closing_date_iso = %s WHERE id = %s", (iso, rid))
    conn.commit()
    conn.close()


def _upsert_tenders(tenders: list) -> int:
    conn = _get_conn()
    c = conn.cursor()
    count = 0
    for t in tenders:
        try:
            # Use a savepoint per row so one bad insert doesn't abort the
            # whole transaction (PostgreSQL aborts on any statement error).
            c.execute("SAVEPOINT sp_upsert")
            closing_date_iso = _parse_closing_date_iso(t.get("closing_date", ""))
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(title, issuing_org) DO UPDATE SET
                    tender_number    = EXCLUDED.tender_number,
                    closing_date     = EXCLUDED.closing_date,
                    closing_time     = EXCLUDED.closing_time,
                    briefing_details = EXCLUDED.briefing_details,
                    document_url     = EXCLUDED.document_url,
                    source_url       = EXCLUDED.source_url,
                    category         = EXCLUDED.category,
                    advertised_date  = EXCLUDED.advertised_date,
                    closing_date_iso = EXCLUDED.closing_date_iso,
                    document_urls    = EXCLUDED.document_urls,
                    updated_at       = EXCLUDED.updated_at
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
            c.execute("RELEASE SAVEPOINT sp_upsert")
            count += 1
        except Exception as exc:
            c.execute("ROLLBACK TO SAVEPOINT sp_upsert")
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
    search = request.args.get("search", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = min(50, max(6, int(request.args.get("per_page", 12))))
    except (ValueError, TypeError):
        per_page = 12

    # Validate filter values against allowed options to prevent injection
    closing_filter = request.args.get("closing_filter", "all")
    if closing_filter not in ("all", "week", "month"):
        closing_filter = "all"

    new_filter = request.args.get("new_filter", "all")
    if new_filter not in ("all", "today", "week"):
        new_filter = "all"

    briefing_filter = request.args.get("briefing_filter", "all")
    if briefing_filter not in ("all", "any", "physical", "online"):
        briefing_filter = "all"

    offset = (page - 1) * per_page
    sa_tz  = "AT TIME ZONE 'Africa/Johannesburg'"

    # Build WHERE conditions dynamically
    conditions = [
        # Always exclude expired tenders
        f"(closing_date_iso IS NULL OR closing_date_iso = '' OR closing_date_iso >= {_SA_TODAY})"
    ]

    if closing_filter == "week":
        conditions.append(
            f"closing_date_iso IS NOT NULL AND closing_date_iso != '' AND "
            f"closing_date_iso <= TO_CHAR((NOW() {sa_tz}) + INTERVAL '7 days', 'YYYY-MM-DD')"
        )
    elif closing_filter == "month":
        conditions.append(
            f"closing_date_iso IS NOT NULL AND closing_date_iso != '' AND "
            f"closing_date_iso <= TO_CHAR((NOW() {sa_tz}) + INTERVAL '30 days', 'YYYY-MM-DD')"
        )

    if new_filter == "today":
        conditions.append(f"created_at >= (NOW() {sa_tz})::date")
    elif new_filter == "week":
        conditions.append(f"created_at >= ((NOW() {sa_tz})::date - INTERVAL '7 days')")

    if briefing_filter == "any":
        conditions.append("briefing_details IS NOT NULL AND briefing_details != ''")
    elif briefing_filter == "online":
        conditions.append(f"briefing_details ~* '({_ONLINE_PATTERN})'")
    elif briefing_filter == "physical":
        # Catch-all: has briefing details but is not clearly online
        conditions.append(
            f"briefing_details IS NOT NULL AND briefing_details != '' "
            f"AND NOT (briefing_details ~* '({_ONLINE_PATTERN})')"
        )
        
    where = " AND ".join(f"({cond})" for cond in conditions)

    conn = _get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    order = """ORDER BY CASE WHEN closing_date_iso IS NULL OR closing_date_iso = ''
                             THEN '9999-12-31' ELSE closing_date_iso END ASC,
                    updated_at DESC"""

    if search:
        like = f"%{search}%"
        c.execute(
            f"""
            SELECT * FROM tenders
            WHERE {where}
              AND (title ILIKE %s OR tender_number ILIKE %s
                   OR issuing_org ILIKE %s OR briefing_details ILIKE %s OR category ILIKE %s)
            {order}
            LIMIT %s OFFSET %s
            """,
            (like, like, like, like, like, per_page, offset),
        )
        rows = c.fetchall()
        c.execute(
            f"""
            SELECT COUNT(*) FROM tenders
            WHERE {where}
              AND (title ILIKE %s OR tender_number ILIKE %s
                   OR issuing_org ILIKE %s OR briefing_details ILIKE %s OR category ILIKE %s)
            """,
            (like, like, like, like, like),
        )
    else:
        c.execute(
            f"SELECT * FROM tenders WHERE {where} {order} LIMIT %s OFFSET %s",
            (per_page, offset),
        )
        rows = c.fetchall()
        c.execute(f"SELECT COUNT(*) FROM tenders WHERE {where}")

    total = c.fetchone()["count"]
    conn.close()

    return jsonify(
        {
            "tenders":  [dict(r) for r in rows],
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "pages":    max(1, (total + per_page - 1) // per_page),
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
    conn = _get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM tenders WHERE id = %s", (tender_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    if affected:
        return jsonify({"status": "deleted"})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/status")
def api_status():
    conn = _get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tenders")
    total = c.fetchone()[0]
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
    t = threading.Thread(target=_crawl_worker, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False)
