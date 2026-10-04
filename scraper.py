import asyncio, json, os
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# All sources navigate to a single URL and use client-side tabs to switch sport
SOURCES = {
    "saved": {
        "url": "https://killersports.com/saved-trend-alerts",
        "skip": ["No Trends to Show"],
        "sports": ["NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "WNBA"],
        "sport_keys": {
            "NFL":"nfl","NBA":"nba","MLB":"mlb","NHL":"nhl",
            "NCAAF":"ncaaf","NCAAB":"ncaab","WNBA":"wnba"
        }
    },
    "ai": {
        "url": "https://killersports.com/trend-ware-matchup2",
        "skip": ["Vault Empty"],
        "sports": ["NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "WNBA"],
        "sport_keys": {
            "NFL":"nfl","NBA":"nba","MLB":"mlb","NHL":"nhl",
            "NCAAF":"ncaaf","NCAAB":"ncaab","WNBA":"wnba"
        }
    },
    "kspro": {
        "url": "https://killersports.com/ks-pro-alerts",
        "skip": ["No Indicators to Show"],
        "sports": ["NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "WNBA"],
        "sport_keys": {
            "NFL":"nfl","NBA":"nba","MLB":"mlb","NHL":"nhl",
            "NCAAF":"ncaaf","NCAAB":"ncaab","WNBA":"wnba"
        }
    },
    "gameday": {
        "url": "https://killersports.com/gameday-trends",
        "skip": ["No Trends to Show", "No Games", "No Data"],
        "sports": ["NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "WNBA"],
        "sport_keys": {
            "NFL":"nfl","NBA":"nba","MLB":"mlb","NHL":"nhl",
            "NCAAF":"ncaaf","NCAAB":"ncaab","WNBA":"wnba"
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

async def click_sport_tab(page, sport_label):
    """Click the tab for the given sport label. Returns what was clicked or 'not found'."""
    result = await page.evaluate(f"""() => {{
        const target = {json.dumps(sport_label)}.toUpperCase();
        // Try tab/filter elements
        const selectors = [
            'a', 'button', 'li', '[role=tab]',
            '.tab', '.filter-btn', '.sport-tab', '.nav-link',
            '.nav-item', '.pill', '.chip', 'span'
        ];
        for (const sel of selectors) {{
            for (const el of document.querySelectorAll(sel)) {{
                if (el.textContent.trim().toUpperCase() === target && el.offsetParent !== null) {{
                    el.click();
                    return 'clicked:' + sel + ':' + el.textContent.trim();
                }}
            }}
        }}
        return 'not found';
    }}""")
    return result

async def scrape_source(page, src_key, src_cfg, existing):
    """Navigate to source URL once, then click each sport tab and scrape."""
    print(f"\n====== Source: {src_key} ({src_cfg['url']}) ======")
    results = {}

    await page.goto(src_cfg["url"], wait_until="networkidle", timeout=60000)
    await asyncio.sleep(3)

    # Debug: show what tabs are available
    tabs = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll(
            'a, button, li, [role=tab], .tab, .filter-btn, .nav-link, .nav-item, .pill, .chip'
        ))
        .filter(el => el.offsetParent !== null && el.textContent.trim().length < 20)
        .map(el => el.textContent.trim())
        .filter((v, i, a) => v && a.indexOf(v) === i)
        .slice(0, 40);
    }""")
    print(f"  [DEBUG] Available tabs on page: {tabs}")

    for sport_label in src_cfg["sports"]:
        sport_key = src_cfg["sport_keys"][sport_label]
        label = f"{src_key}/{sport_key}"
        print(f"\n--- Tab: {label} (clicking '{sport_label}') ---")

        click_result = await click_sport_tab(page, sport_label)
        print(f"  [DEBUG] Tab click: {click_result}")

        if click_result == 'not found':
            print(f"  [WARN] Could not find tab for {sport_label}")
            results[sport_key] = existing.get(src_key, {}).get(sport_key) or {"headers": [], "rows": []}
            continue

        # Wait for table to update after tab click
        await asyncio.sleep(2)

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text()

        skip_hit = False
        for phrase in src_cfg["skip"]:
            if phrase.lower() in page_text.lower():
                print(f"  [INFO] Skip phrase found: '{phrase}' - no data for {label}")
                skip_hit = True
                break
        if skip_hit:
            results[sport_key] = existing.get(src_key, {}).get(sport_key) or {"headers": [], "rows": []}
            continue

        table = best_table(soup, src_cfg["skip"])
        if table is None:
            print(f"  [WARN] No table found for {label}")
            print(f"  [DEBUG] Page snippet: {page_text[:300]}")
            results[sport_key] = existing.get(src_key, {}).get(sport_key) or {"headers": [], "rows": []}
            continue

        headers = extract_headers(table)
        if not headers:
            print(f"  [WARN] No headers for {label}")
            results[sport_key] = existing.get(src_key, {}).get(sport_key) or {"headers": [], "rows": []}
            continue

        rows = extract_rows(table, headers)
        fresh = {"headers": headers, "rows": rows} if rows else None
        prev = existing.get(src_key, {}).get(sport_key)
        merged = merge(prev, fresh)
        results[sport_key] = merged if merged else {"headers": [], "rows": []}
        print(f"  [OK] {label}: {len(headers)} cols, {len(rows)} rows")

    return results

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

        await page.evaluate("document.getElementById('__abconsent-cmp')?.remove()")
        await asyncio.sleep(0.5)

        login_info = await page.evaluate("""() => {
            const results = {};
            results.modalLogin = !!document.querySelector('.modal-login');
            results.loginBtns = Array.from(document.querySelectorAll('a, button')).filter(
                el => el.textContent.toLowerCase().includes('log') || el.textContent.toLowerCase().includes('sign')
            ).map(el => ({ tag: el.tagName, text: el.textContent.trim().slice(0,40), cls: el.className }));
            results.inputs = Array.from(document.querySelectorAll('input[type=email], input[type=password], input[name=email], input[name=password]')).map(
                el => ({ type: el.type, name: el.name, cls: el.className, visible: el.offsetParent !== null })
            );
            return results;
        }""")
        print(f"  [DEBUG] Login page info: {login_info}")

        triggered = await page.evaluate("""() => {
            const candidates = Array.from(document.querySelectorAll('a, button, [role=button]'));
            for (const el of candidates) {
                const t = el.textContent.toLowerCase().trim();
                if ((t === 'login' || t === 'log in' || t === 'sign in') && el.offsetParent !== null) {
                    el.click();
                    return 'clicked: ' + el.tagName + ' "' + el.textContent.trim() + '"';
                }
            }
            const modal = document.querySelector('.modal-login');
            if (modal) {
                modal.style.cssText = 'display:block !important; opacity:1 !important; visibility:visible !important;';
                return 'forced modal visible';
            }
            return 'no login trigger found';
        }""")
        print(f"  [DEBUG] Login trigger result: {triggered}")
        await asyncio.sleep(2)

        await page.evaluate("document.getElementById('__abconsent-cmp')?.remove()")

        try:
            await page.wait_for_selector(
                "input[type='email'], input[name='email'], input.input",
                state="visible", timeout=15000
            )
        except Exception:
            visible = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('input')).map(el => ({
                    type: el.type, name: el.name, id: el.id,
                    cls: el.className, visible: el.offsetParent !== null
                }))
            """)
            print(f"  [DEBUG] All inputs on page: {visible}")
            raise

        await page.fill("input[type='email'], input[name='email'], input.input", email)
        await page.fill("input[type='password'], input.password", password)
        await page.evaluate("document.getElementById('__abconsent-cmp')?.remove()")
        await asyncio.sleep(0.3)

        await page.evaluate("""() => {
            const btn = document.querySelector('.modal-login .button-primary')
                     || document.querySelector('button[type=submit]')
                     || document.querySelector('input[type=submit]')
                     || Array.from(document.querySelectorAll('button')).find(
                            b => /log.?in|sign.?in|submit/i.test(b.textContent)
                        );
            if (btn) btn.click();
        }""")
        await asyncio.sleep(5)
        print("Login submitted")

        result = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "saved": {}, "ai": {}, "kspro": {}, "gameday": {}
        }

        for src_key, src_cfg in SOURCES.items():
            result[src_key] = await scrape_source(page, src_key, src_cfg, existing)

        await browser.close()

    with open("data.json", "w") as f:
        json.dump(result, f, separators=(",", ":"))
    print("\n=== data.json written ===")

asyncio.run(main())
