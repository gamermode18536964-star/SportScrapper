import asyncio, json, os
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

SOURCES = {
    "saved": {
        "url": "https://killersports.com/saved-trend-alerts?filter={sport}",
        "skip": ["No Trends to Show"],
        "filters": {
            "mlb":"MLB","wnba":"WNBA","nfl":"NFL",
            "nba":"NBA","nhl":"NHL","ncaaf":"NCAAFB","ncaab":"NCAABB"
        }
    },
    "ai": {
        "url": "https://killersports.com/trend-ware-matchup2?filter={sport}",
        "skip": ["Vault Empty"],
        "filters": {
            "mlb":"MLB","wnba":"WNBA","nfl":"NFL",
            "nba":"NBA","nhl":"NHL","ncaaf":"NCAAFB","ncaab":"NCAABB"
        }
    },
    "kspro": {
        "url": "https://killersports.com/ks-pro-alerts?filter={sport}",
        "skip": ["No Indicators to Show"],
        "filters": {
            "mlb":"MLB","wnba":"WNBA","nfl":"NFL",
            "nba":"NBA","nhl":"NHL","ncaaf":"NCAAFB","ncaab":"NCAABB"
        }
    }
}

def best_table(soup, skip_phrases):
    tables = soup.find_all("table")
    print(f"  [DEBUG] Found {len(tables)} table(s) on page")
    candidates = []
    for i, t in enumerate(tables):
        t_text = t.get_text()
        if any(ph.lower() in t_text.lower() for ph in skip_phrases):
            print(f"  [DEBUG] Table {i}: skipped (contains skip phrase)")
            continue
        tbody = t.find("tbody")
        if tbody:
            rows = tbody.find_all("tr")
        else:
            rows = t.find_all("tr")
            if t.find("thead") or (rows and rows[0].find("th")):
                rows = rows[1:]
        print(f"  [DEBUG] Table {i} id={t.get('id','')} class={t.get('class','')} data_rows={len(rows)}")
        if len(rows) > 0:
            candidates.append((len(rows), i, t))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][2]

def extract_headers(table):
    thead = table.find("thead")
    if thead:
        header_rows = thead.find_all("tr")
        if header_rows:
            cells = header_rows[-1].find_all(["th", "td"])
            headers = [c.get_text(strip=True) for c in cells]
            print(f"  [DEBUG] Headers from thead: {headers}")
            return headers
    for row in table.find_all("tr"):
        cells = row.find_all("th")
        if cells:
            headers = [c.get_text(strip=True) for c in cells]
            print(f"  [DEBUG] Headers from th row: {headers}")
            return headers
    first_row = table.find("tr")
    if first_row:
        cells = first_row.find_all(["th", "td"])
        headers = [c.get_text(strip=True) for c in cells]
        print(f"  [DEBUG] Headers from first tr: {headers}")
        return headers
    return []

def extract_rows(table, headers):
    tbody = table.find("tbody")
    if tbody:
        trs = tbody.find_all("tr")
    else:
        all_trs = table.find_all("tr")
        trs = all_trs[1:] if all_trs else []
    rows = []
    for tr in trs:
        cells = tr.find_all(["td", "th"])
        row = [c.get_text(strip=True) for c in cells]
        if not any(row):
            continue
        while len(row) < len(headers):
            row.append("")
        row = row[:len(headers)]
        rows.append(row)
    print(f"  [DEBUG] Extracted {len(rows)} data rows")
    if rows:
        print(f"  [DEBUG] First row sample: {rows[0]}")
    return rows

async def scrape(page, url, skip_phrases, label):
    print(f"\n--- Scraping {label}: {url} ---")
    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text()
        for phrase in skip_phrases:
            if phrase.lower() in page_text.lower():
                print(f"  [INFO] Skip phrase found: '{phrase}' - returning empty")
                return None
        table = best_table(soup, skip_phrases)
        if table is None:
            print(f"  [WARN] No suitable table found for {label}")
            title = soup.find("title")
            print(f"  [DEBUG] Page title: {title.get_text() if title else 'N/A'}")
            print(f"  [DEBUG] Page text snippet: {page_text[:500]}")
            return None
        headers = extract_headers(table)
        if not headers:
            print(f"  [WARN] Could not extract headers for {label}")
            return None
        rows = extract_rows(table, headers)
        if not rows:
            print(f"  [INFO] Table found but no data rows for {label}")
            return None
        print(f"  [OK] {label}: {len(headers)} cols, {len(rows)} rows")
        return {"headers": headers, "rows": rows}
    except Exception as e:
        print(f"  [ERROR] {label}: {e}")
        return None

def merge(existing, fresh):
    if fresh is None:
        return existing
    if not fresh.get("rows"):
        return existing
    if not existing or not existing.get("rows"):
        return fresh
    today_labels = set(r[0] for r in fresh["rows"] if r[0] and r[0] != "@")
    kept, skip = [], False
    for row in existing["rows"]:
        if row[0] and row[0] != "@":
            skip = row[0] in today_labels
        if not skip:
            kept.append(row)
    return {"headers": fresh["headers"], "rows": kept + fresh["rows"]}

async def main():
    email    = os.environ["KS_EMAIL"]
    password = os.environ["KS_PASSWORD"]
    existing = {}
    try:
        with open("data.json") as f:
            existing = json.load(f)
        print("Loaded existing data.json")
    except Exception:
        print("No existing data.json - starting fresh")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print("\n=== Logging in ===")
        await page.goto("https://killersports.com", wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)

        # Remove cookie consent overlay if present
        await page.evaluate("document.getElementById('__abconsent-cmp')?.remove()")
        await asyncio.sleep(0.5)

        # Debug: log what login-related elements exist on the page
        login_info = await page.evaluate("""() => {
            const results = {};
            results.modalLogin = !!document.querySelector('.modal-login');
            results.modalLoginVisible = (() => {
                const el = document.querySelector('.modal-login');
                if (!el) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            })();
            results.loginBtns = Array.from(document.querySelectorAll('a, button')).filter(
                el => el.textContent.toLowerCase().includes('log') || el.textContent.toLowerCase().includes('sign')
            ).map(el => ({ tag: el.tagName, text: el.textContent.trim().slice(0,40), cls: el.className }));
            results.inputs = Array.from(document.querySelectorAll('input[type=email], input[type=password], input[name=email], input[name=password]')).map(
                el => ({ type: el.type, name: el.name, cls: el.className, visible: el.offsetParent !== null })
            );
            return results;
        }""")
        print(f"  [DEBUG] Login page info: {login_info}")

        # Try clicking a visible login link/button to open the modal
        triggered = await page.evaluate("""() => {
            // Look for a nav/header login link
            const candidates = Array.from(document.querySelectorAll('a, button, [role=button]'));
            for (const el of candidates) {
                const t = el.textContent.toLowerCase().trim();
                if ((t === 'login' || t === 'log in' || t === 'sign in') && el.offsetParent !== null) {
                    el.click();
                    return 'clicked: ' + el.tagName + ' "' + el.textContent.trim() + '"';
                }
            }
            // Fallback: force the modal visible directly
            const modal = document.querySelector('.modal-login');
            if (modal) {
                modal.style.cssText = 'display:block !important; opacity:1 !important; visibility:visible !important;';
                return 'forced modal visible';
            }
            return 'no login trigger found';
        }""")
        print(f"  [DEBUG] Login trigger result: {triggered}")
        await asyncio.sleep(2)

        # Remove overlay again in case it reappeared
        await page.evaluate("document.getElementById('__abconsent-cmp')?.remove()")

        # Wait for email input inside the modal (extended timeout)
        try:
            await page.wait_for_selector(
                "input[type='email'], input[name='email']",
                state="visible", timeout=15000
            )
        except Exception:
            # Last resort: dump visible inputs for debugging
            visible = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('input')).map(el => ({
                    type: el.type, name: el.name, id: el.id,
                    cls: el.className, visible: el.offsetParent !== null
                }))
            """)
            print(f"  [DEBUG] All inputs on page: {visible}")
            raise

        await page.fill("input[type='email'], input[name='email']", email)
        await page.fill("input[type='password'], input[name='password']", password)
        await page.evaluate("document.getElementById('__abconsent-cmp')?.remove()")
        await asyncio.sleep(0.3)

        # Click the submit button inside whatever form is visible
        await page.evaluate("""() => {
            const btn = document.querySelector('.modal-login .button-primary')
                     || document.querySelector('button[type=submit]')
                     || document.querySelector('input[type=submit]');
            if (btn) btn.click();
        }""")
        await asyncio.sleep(5)
        print("Login submitted")

        result = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "saved": {}, "ai": {}, "kspro": {}
        }

        for src_key, src_cfg in SOURCES.items():
            for sport_key, sport_label in src_cfg["filters"].items():
                url   = src_cfg["url"].replace("{sport}", sport_label)
                label = f"{src_key}/{sport_key}"
                fresh = await scrape(page, url, src_cfg["skip"], label)
                prev  = existing.get(src_key, {}).get(sport_key)
                merged = merge(prev, fresh)
                result[src_key][sport_key] = merged if merged else {"headers": [], "rows": []}

        await browser.close()

    with open("data.json", "w") as f:
        json.dump(result, f, separators=(",", ":"))
    print("\n=== data.json written ===")

asyncio.run(main())
