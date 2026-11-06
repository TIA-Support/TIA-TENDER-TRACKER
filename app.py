"""
TIA Tender Tracker - Flask backend (MySQL / cPanel)

The crawler no longer runs inside this app. It runs elsewhere (GitHub Actions)
and sends the tenders it finds to POST /api/ingest.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import pymysql
import pymysql.cursors
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload limit

DATABASE_URL = os.environ.get("DATABASE_URL", "")
INGEST_SECRET = os.environ.get("INGEST_SECRET", "")

# South African time (SAST = UTC+2, no daylight saving)
SA_TZ = timezone(timedelta(hours=2))

# Pattern for online briefings - used by both the online and physical filters
_ONLINE_PATTERN = (
    "microsoft teams|ms teams|teams meeting|teams link|join.{0,20}teams"
    "|zoom[.]us|zoom meeting|zoom link|join.{0,20}zoom"
    "|google meet|meet[.]google"
    "|webex|skype for business"
    "|virtual|virtual briefing|virtual meeting|virtual session"
    "|meeting link|briefing link|online meeting|online briefing"
    "|join via link|click.{0,20}join|link to join|teams invite"
)


def _parse_closing_date_iso(date_str):
    """Convert DD/MM/YYYY to YYYY-MM-DD for sorting. Returns '' on failure."""
    if not date_str:
        return ""
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _tender_key(title, org):
    raw = f"{(title or '').strip().lower()}|{(org or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ #
#  Database helpers                                                    #
# ------------------------------------------------------------------ #


def _get_conn():
    """Return a new MySQL connection, built from DATABASE_URL."""
    u = urlparse(DATABASE_URL)
    return pymysql.connect(
        host=u.hostname or "localhost",
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        init_command="SET time_zone = '+00:00'",
        autocommit=False,
    )


def init_db():
    conn = _get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS tenders (
                    id               INT AUTO_INCREMENT PRIMARY KEY,
                    uniq_hash        CHAR(64) NOT NULL,
                    tender_number    TEXT,
                    title            TEXT NOT NULL,
                    issuing_org      TEXT,
                    closing_date     VARCHAR(50),
                    closing_time     VARCHAR(50),
                    briefing_details TEXT,
                    document_url     TEXT,
                    source_url       TEXT,
                    category         VARCHAR(100),
                    advertised_date  VARCHAR(50),
                    closing_date_iso VARCHAR(10),
                    document_urls    LONGTEXT,
                    created_at       DATETIME NOT NULL,
                    updated_at       DATETIME NOT NULL,
                    UNIQUE KEY uq_tender_hash (uniq_hash),
                    KEY idx_closing_date_iso (closing_date_iso)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        conn.commit()
    finally:
        conn.close()


def _upsert_tenders(tenders):
    conn = _get_conn()
    count = 0
    try:
        with conn.cursor() as c:
            for t in tenders:
                try:
                    title = (t.get("title") or "").strip()
                    if not title:
                        continue
                    org = t.get("issuing_org", "") or ""
                    closing_date_iso = _parse_closing_date_iso(t.get("closing_date", ""))

                    raw_doc_urls = t.get("document_urls")
                    if raw_doc_urls is None:
                        single = t.get("document_url", "")
                        raw_doc_urls = json.dumps([single] if single else [])
                    elif not isinstance(raw_doc_urls, str):
                        raw_doc_urls = json.dumps(raw_doc_urls)

                    c.execute(
                        """
                        INSERT INTO tenders
                            (uniq_hash, tender_number, title, issuing_org, closing_date,
                             closing_time, briefing_details, document_url, source_url,
                             category, advertised_date, closing_date_iso, document_urls,
                             created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                UTC_TIMESTAMP(), UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE
                            tender_number    = VALUES(tender_number),
                            closing_date     = VALUES(closing_date),
                            closing_time     = VALUES(closing_time),
                            briefing_details = VALUES(briefing_details),
                            document_url     = VALUES(document_url),
                            source_url       = VALUES(source_url),
                            category         = VALUES(category),
                            advertised_date  = VALUES(advertised_date),
                            closing_date_iso = VALUES(closing_date_iso),
                            document_urls    = VALUES(document_urls),
                            updated_at       = UTC_TIMESTAMP()
                        """,
                        (
                            _tender_key(title, org),
                            t.get("tender_number", ""),
                            title,
                            org,
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
    finally:
        conn.close()
    return count


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

    now_sa = datetime.now(SA_TZ)
    today_iso = now_sa.strftime("%Y-%m-%d")
    midnight_sa = now_sa.replace(hour=0, minute=0, second=0, microsecond=0)

    # Always exclude expired tenders
    conditions = [
        "closing_date_iso IS NULL OR closing_date_iso = '' OR closing_date_iso >= %s"
    ]
    params = [today_iso]

    if closing_filter in ("week", "month"):
        days = 7 if closing_filter == "week" else 30
        limit_iso = (now_sa + timedelta(days=days)).strftime("%Y-%m-%d")
        conditions.append(
            "closing_date_iso IS NOT NULL AND closing_date_iso != '' "
            "AND closing_date_iso <= %s"
        )
        params.append(limit_iso)

    if new_filter in ("today", "week"):
        start_sa = midnight_sa if new_filter == "today" else midnight_sa - timedelta(days=7)
        start_utc = start_sa.astimezone(timezone.utc).replace(tzinfo=None)
        conditions.append("created_at >= %s")
        params.append(start_utc)

    if briefing_filter == "any":
        conditions.append("briefing_details IS NOT NULL AND briefing_details != ''")
    elif briefing_filter == "online":
        conditions.append("briefing_details REGEXP %s")
        params.append(_ONLINE_PATTERN)
    elif briefing_filter == "physical":
        conditions.append(
            "briefing_details IS NOT NULL AND briefing_details != '' "
            "AND NOT (briefing_details REGEXP %s)"
        )
        params.append(_ONLINE_PATTERN)

    if search:
        like = f"%{search}%"
        conditions.append(
            "title LIKE %s OR tender_number LIKE %s OR issuing_org LIKE %s "
            "OR briefing_details LIKE %s OR category LIKE %s"
        )
        params.extend([like] * 5)

    where = " AND ".join(f"({cond})" for cond in conditions)

    order = """ORDER BY CASE WHEN closing_date_iso IS NULL OR closing_date_iso = ''
                             THEN '9999-12-31' ELSE closing_date_iso END ASC,
                    updated_at DESC"""

    conn = _get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                f"SELECT * FROM tenders WHERE {where} {order} LIMIT %s OFFSET %s",
                params + [per_page, offset],
            )
            rows = c.fetchall()
            c.execute(f"SELECT COUNT(*) AS total FROM tenders WHERE {where}", params)
            total = c.fetchone()["total"]
    finally:
        conn.close()

    for r in rows:
        r.pop("uniq_hash", None)

    return jsonify(
        {
            "tenders": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    )


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """Called by the crawler (GitHub Actions) to send in the tenders it found."""
    if not INGEST_SECRET:
        return jsonify({"status": "error", "message": "Ingest is not configured."}), 503

    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {INGEST_SECRET}"
    if not hmac.compare_digest(auth.encode("utf-8"), expected.encode("utf-8")):
        return jsonify({"status": "error", "message": "Unauthorized."}), 401

    data = request.get_json(silent=True) or {}
    tenders = data.get("tenders")
    if not isinstance(tenders, list):
        return jsonify({"status": "error", "message": "Expected a 'tenders' list."}), 400

    saved = _upsert_tenders(tenders)
    return jsonify({"status": "ok", "received": len(tenders), "saved": saved})


@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    # The crawl now runs automatically on a schedule, outside this server.
    return jsonify(
        {
            "status": "scheduled",
            "message": "The crawler runs automatically on a schedule. New tenders appear here after each run.",
        }
    )


@app.route("/api/tenders/<int:tender_id>", methods=["DELETE"])
def api_delete_tender(tender_id):
    conn = _get_conn()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM tenders WHERE id = %s", (tender_id,))
            affected = c.rowcount
        conn.commit()
    finally:
        conn.close()
    if affected:
        return jsonify({"status": "deleted"})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/status")
def api_status():
    conn = _get_conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) AS total, MAX(updated_at) AS last FROM tenders")
            row = c.fetchone()
    finally:
        conn.close()

    last = row["last"]
    last_crawl = None
    if last:
        last_crawl = (
            last.replace(tzinfo=timezone.utc).astimezone(SA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        )

    return jsonify(
        {
            "running": False,
            "last_crawl": last_crawl,
            "message": "The crawler runs automatically on a schedule.",
            "found": row["total"],
            "total_tenders": row["total"],
        }
    )


# ------------------------------------------------------------------ #
#  Startup                                                            #
# ------------------------------------------------------------------ #

try:
    init_db()
except Exception as exc:
    print(f"[DB] init_db failed: {exc}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port, use_reloader=False)
