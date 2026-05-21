import asyncio
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup


class ETendersScraper:
    BASE_URL = "https://www.etenders.gov.za"
    OPPORTUNITIES_URL = "https://www.etenders.gov.za/Home/opportunities"

    # ---------------------------------------------------------------
    # Keywords aligned to TIA Solutions' service offerings:
    #   1. Cloud Solutions (servers, storage, databases, networking,
    #      software, analytics, virtualisation)
    #   2. Network & Cyber Security (firewalls, endpoint, SIEM, VPN,
    #      Cisco, Fortinet, CheckPoint, cabling infrastructure)
    #   3. Internet Service Provider (Fibre, Wireless, LTE, broadband)
    #   4. VoIP – Business Voice Solutions (PABX, SIP, unified comms)
    #   5. IT Support / Managed Services / Consulting / Maintenance
    #   6. Software Solutions (development, integration, ERP, CRM,
    #      Microsoft 365, SAP, Oracle)
    # ---------------------------------------------------------------
    ICT_KEYWORDS = [
        # --- Cloud Solutions ---
        "cloud", "saas", "paas", "iaas",
        "azure", "aws ", "amazon web services",
        "microsoft 365", "office 365", "m365", "google workspace",
        "server", "storage solution", "backup solution",
        "disaster recovery", "business continuity",
        "virtualisation", "virtualization", "vmware", "hyper-v",
        "data centre", "data center", "datacentre",

        # --- Network & Cyber Security ---
        "network", "networking", "cyber", "cybersecurity", "cyber security",
        "firewall", "vpn", "intrusion detection", "intrusion prevention",
        "endpoint protection", "endpoint security", "antivirus",
        "security operations", "vulnerability assessment", "penetration test",
        "patch management", "siem", "information security",
        "cisco", "fortinet", "checkpoint", "check point", "palo alto",
        "juniper", "network switch", "network cabling", "structured cabling",
        "lan ", "wan ", "sd-wan", "mpls", "network infrastructure",

        # --- Internet Service Provider / Connectivity ---
        "fibre", "fiber", "broadband", "bandwidth", "connectivity",
        "internet service", "internet access", "isp ",
        "lte", "4g ", "5g ", "wireless broadband", "fixed wireless",
        "wi-fi", "wifi", "wireless access point", "satellite internet",

        # --- VoIP / Unified Communications ---
        "voip", "pabx", "pbx ", "unified communication",
        "telephony", "sip trunk", "voice over ip", "business voice",
        "ip telephony", "microsoft teams voice",

        # --- IT Support / Managed Services / Consulting ---
        "it support", "managed service", "helpdesk", "help desk",
        "it service", "it consulting", "it management",
        "it maintenance", "technical support", "desktop support",
        "service desk", "sita",

        # --- Software Solutions & Development ---
        "software", "application development", "app development",
        "web development", "mobile app", "mobile application",
        "system integration", "erp", "crm", "enterprise resource",
        "database", "microsoft", "oracle", "sap",
        "digital transformation", "digitisation", "digitization",
        "e-government", "e-services",

        # --- IT Hardware (direct supply) ---
        "computer equipment", "it equipment", "it hardware",
        "laptop", "desktop computer", "workstation",
        "it infrastructure",

        # --- General ICT ---
        "information and communication",
        "information communication technology",
        "ict", " it ", "i.t.",
    ]

    # Tenders matching these phrases are excluded even if they also
    # match an ICT keyword (false positives due to eTenders category
    # assignment or incidental keyword matches).
    EXCLUDE_KEYWORDS = [
        "office accommodation",
        "accommodation for the department",
        "pest control", "rodent control",
        "security guard", "security services", "physical security",
        "guarding services", "armed response",
        "cleaning service", "cleaning of ", "janitorial",
        "grass cutting", "landscaping", "gardening",
        "road construction", "road infrastructure",
        "water and sewer", "bulk water", "bulk sewer",
        "wastewater", "sanitation", "plumbing",
        "irrigation", "piggery", "poultry", "animal feed",
        "building construction", "construction of ",
        "building maintenance", "building refurbishment",
        "electrical maintenance", "electrical contractor",
        "waterproofing", "roof painting",
        "catering", "canteen", "food supply",
        "furniture supply", "office furniture",
        "medical equipment", "medical testing",
        "health facility", "hospital construction",
        "toilet units", "mobile toilet",
        "alien invasive", "vegetation clearing",
    ]

    ICT_CATEGORIES = [
        "information and communication",
        "information communication",
    ]

    def scrape(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self._scrape_async())
        except Exception as exc:
            print(f"[Scraper] Fatal error: {exc}")
            return []
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def _is_ict(self, tender: dict) -> bool:
        haystack = " ".join([
            tender.get("title", ""),
            tender.get("category", ""),
            tender.get("issuing_org", ""),
        ]).lower()

        # Check exclusion list first — drop obvious false positives
        for excl in self.EXCLUDE_KEYWORDS:
            if excl in haystack:
                return False

        for cat in self.ICT_CATEGORIES:
            if cat in haystack:
                return True
        for kw in self.ICT_KEYWORDS:
            if kw in haystack:
                return True
        return False

    async def _scrape_async(self):
        tenders = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            try:
                print("[Scraper] Loading eTenders portal...")
                await page.goto(self.OPPORTUNITIES_URL, timeout=90000, wait_until="networkidle")
                await page.wait_for_timeout(3000)
                await self._dismiss_popup(page)

                # Set page size to 100 via JS
                changed = await page.evaluate("""
                    () => {
                        var sel = document.querySelector('select[name$="_length"]');
                        if (!sel) return false;
                        sel.value = '100';
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    }
                """)
                if changed:
                    await page.wait_for_timeout(3000)
                    print("[Scraper] Set page size to 100")

                MAX_PAGES = 10
                for page_num in range(MAX_PAGES):
                    print(f"[Scraper] Page {page_num + 1}...")
                    rows_data = await page.evaluate("""
                        () => {
                            var tbody = (
                                document.querySelector('#tendeList tbody') ||
                                document.querySelector('table tbody')
                            );
                            if (!tbody) return [];
                            return Array.from(tbody.querySelectorAll('tr:not(.child):not(.detail)')).map((row, idx) => {
                                var cells = Array.from(row.querySelectorAll('td'));
                                return {
                                    index: idx,
                                    cells: cells.map(cell => ({
                                        text: (cell.innerText || '').trim(),
                                        links: Array.from(cell.querySelectorAll('a'))
                                                    .map(a => a.href)
                                                    .filter(h => h && !h.startsWith('javascript'))
                                    }))
                                };
                            });
                        }
                    """)
                    print(f"[Scraper]   {len(rows_data)} rows on page")

                    ict_indices = []
                    for row_info in rows_data:
                        tender_basic = self._parse_row_data(row_info)
                        if tender_basic and self._is_ict(tender_basic):
                            ict_indices.append((row_info["index"], tender_basic))

                    print(f"[Scraper]   {len(ict_indices)} matched ICT filter")

                    for dom_idx, tender_basic in ict_indices:
                        details = await self._expand_row_details(page, dom_idx)
                        tender_basic.update(details)
                        # Skip tenders whose closing date has already passed
                        cd = tender_basic.get("closing_date", "")
                        if cd:
                            try:
                                from datetime import date as _date, datetime as _dt
                                if _dt.strptime(cd, "%d/%m/%Y").date() < _date.today():
                                    print(f"  - SKIP (closed {cd}): {tender_basic.get('title', '')[:55]}")
                                    continue
                            except ValueError:
                                pass
                        tenders.append(tender_basic)
                        print(f"  + {tender_basic.get('title', '')[:70]}")

                    try:
                        await self._dismiss_popup(page)
                        next_btn = page.locator("#tendeList_next, .paginate_button.next").first
                        if await next_btn.count() == 0:
                            print("[Scraper] No next button.")
                            break
                        cls = await next_btn.get_attribute("class") or ""
                        if "disabled" in cls:
                            print("[Scraper] Last page.")
                            break
                        await next_btn.click(force=True)
                        await page.wait_for_timeout(3000)
                    except Exception as nav_err:
                        print(f"[Scraper] Pagination: {nav_err}")
                        break

            except Exception as exc:
                import traceback
                print(f"[Scraper] Error: {exc}")
                traceback.print_exc()
            finally:
                await browser.close()

        print(f"[Scraper] Done - {len(tenders)} ICT tenders.")
        return tenders

    async def _dismiss_popup(self, page) -> None:
        try:
            is_blocking = await page.evaluate("""
                () => document.querySelector(
                    '#educationalPopup.show, .modal.show, [role="dialog"].show'
                ) !== null
            """)
            if not is_blocking:
                return
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)
            for sel in [
                "#educationalPopup .btn-close",
                "#educationalPopup [data-bs-dismiss='modal']",
                "#educationalPopup .close",
                "#educationalPopup button:last-of-type",
            ]:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click(force=True)
                    await page.wait_for_timeout(400)
                    break
            print("[Scraper] Dismissed popup")
        except Exception:
            pass

    def _parse_row_data(self, row_info: dict):
        cells = row_info.get("cells", [])
        if len(cells) < 3:
            return None
        cell_texts = [c["text"] for c in cells]
        all_links = [lnk for c in cells for lnk in c.get("links", [])]
        category = ""
        title = ""
        closing_date = ""
        source_url = self.OPPORTUNITIES_URL
        for idx, text in enumerate(cell_texts):
            if not text:
                continue
            if re.search(r"\d{1,2}/\d{1,2}/\d{4}|in\s+\d+\s+day", text, re.I):
                if not closing_date:
                    closing_date = text
            elif len(text) > len(title) and idx > 0:
                title = text
            elif not category and idx in (1, 2) and len(text) < 80:
                category = text
        for lnk in all_links:
            if "/Tender/" in lnk or "/tender/" in lnk or "/Detail" in lnk:
                source_url = lnk
                break
        if not title:
            return None
        return {
            "title": title,
            "category": category,
            "closing_date": closing_date,
            "source_url": source_url,
            "tender_number": "",
            "issuing_org": "",
            "closing_time": "",
            "briefing_details": "",
            "document_url": "",
            "advertised_date": "",
        }

    async def _expand_row_details(self, page, dom_idx: int) -> dict:
        details = {}
        try:
            await page.evaluate(f"""
                () => {{
                    var tbody = (
                        document.querySelector('#tendeList tbody') ||
                        document.querySelector('table tbody')
                    );
                    if (!tbody) return;
                    var rows = Array.from(tbody.querySelectorAll('tr:not(.child):not(.detail)'));
                    if ({dom_idx} < rows.length) {{
                        var firstCell = rows[{dom_idx}].querySelector('td');
                        if (firstCell) firstCell.click();
                    }}
                }}
            """)
            await page.wait_for_timeout(400)

            child_data = await page.evaluate(f"""
                () => {{
                    var tbody = (
                        document.querySelector('#tendeList tbody') ||
                        document.querySelector('table tbody')
                    );
                    if (!tbody) return ['', ''];
                    var dataRows = Array.from(tbody.querySelectorAll('tr:not(.child):not(.detail)'));
                    if ({dom_idx} >= dataRows.length) return ['', ''];
                    var next = dataRows[{dom_idx}].nextElementSibling;
                    // Child row has no class; detect by presence of a colspan td (expanded detail)
                    if (next && next.querySelector('td[colspan]')) {{
                        return [next.innerHTML, next.innerText];
                    }}
                    return ['', ''];
                }}
            """)

            if child_data and child_data[1]:
                details = self._parse_child_content(child_data[1], child_data[0])

            # Collapse
            await page.evaluate(f"""
                () => {{
                    var tbody = (
                        document.querySelector('#tendeList tbody') ||
                        document.querySelector('table tbody')
                    );
                    if (!tbody) return;
                    var rows = Array.from(tbody.querySelectorAll('tr:not(.child):not(.detail)'));
                    if ({dom_idx} < rows.length) {{
                        var firstCell = rows[{dom_idx}].querySelector('td');
                        if (firstCell) firstCell.click();
                    }}
                }}
            """)
            await page.wait_for_timeout(200)

        except Exception as exc:
            print(f"[Scraper] Expand row {dom_idx}: {exc}")
        return details

    def _parse_child_content(self, text: str, html: str) -> dict:
        """Parse eTenders expanded child row.

        The child row uses a nested table with rows like:
          <tr><td><b>Tender Number:</b></td><td>Znq 06/26/27</td></tr>
          <tr><td><b>Organ Of State:</b></td><td>Department Name</td></tr>
          <tr><td><b>Closing Date:</b></td><td>Tuesday, 26 May 2026 - 11:00</td></tr>
        """
        from datetime import datetime
        details = {}
        soup = BeautifulSoup(html, "lxml")

        # Parse the nested label:value table rows
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True).rstrip(":").lower()
            value = cells[1].get_text(strip=True)
            if not value or value == "N/A":
                continue

            if "tender number" in label or "bid number" in label:
                details["tender_number"] = value
            elif "organ of state" in label:
                details["issuing_org"] = value
            elif "closing date" in label:
                # "Tuesday, 26 May 2026 - 11:00"
                if " - " in value:
                    date_part, time_part = value.rsplit(" - ", 1)
                else:
                    date_part, time_part = value, ""
                try:
                    dt = datetime.strptime(date_part.strip(), "%A, %d %B %Y")
                    details["closing_date"] = dt.strftime("%d/%m/%Y")
                except ValueError:
                    details["closing_date"] = date_part.strip()
                if time_part:
                    details["closing_time"] = time_part.strip()
            elif "date published" in label:
                try:
                    dt = datetime.strptime(value, "%A, %d %B %Y")
                    details["advertised_date"] = dt.strftime("%d/%m/%Y")
                except ValueError:
                    details["advertised_date"] = value
            elif "briefing date and time" in label:
                details["briefing_details"] = value
            elif "briefing venue" in label:
                if "briefing_details" in details:
                    details["briefing_details"] += f" | Venue: {value}"
                else:
                    details["briefing_details"] = f"Venue: {value}"

        # Extract ALL document download links
        import json as _json
        doc_urls = []
        seen_hrefs = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            abs_href = href if href.startswith("http") else self.BASE_URL + href
            if abs_href in seen_hrefs:
                continue
            if any(ext in href.lower() for ext in [".pdf", ".doc", ".docx", ".zip", ".xlsx", ".xls"]):
                doc_urls.append(abs_href)
                seen_hrefs.add(abs_href)
            elif "download" in href.lower() or "/Document/" in href:
                doc_urls.append(abs_href)
                seen_hrefs.add(abs_href)
        if doc_urls:
            details["document_url"] = doc_urls[0]
        details["document_urls"] = _json.dumps(doc_urls)

        # Source URL from any tender detail link inside the child row
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/Tender/" in href or "/tender/" in href:
                details["source_url"] = href if href.startswith("http") else self.BASE_URL + href
                break

        return details
