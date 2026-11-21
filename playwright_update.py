import json, re, time, datetime, asyncio
from urllib.parse import urljoin
from playwright.async_api import async_playwright

PHONES = ["Vivo X300", "Vivo X200", "Samsung Galaxy S25", "iPhone 17"]
DELAY = 2

SHOPS = {
    "KRY International": "https://kryinternational.com",
    "Taham Tech": "https://tahamtech.com.bd",
    "Cell Tech BD": "https://www.celltechbd.com.bd",
    "SMS Gadget": "https://smsgadget.com",
    "Rio International": "https://riointernational.com.bd",
    "Apple Gadgets BD": "https://www.applegadgetsbd.com",
    "Sumash Tech": "https://www.sumashtech.com",
    "Dazzle": "https://dazzle.com.bd",
    "Like Telecom": "https://liketelecom.com.bd",
    "Gadget & Gear": "https://gadgetandgear.com",
}

# Known working search addresses are tried first, the search box is the fallback.
SEARCH_URLS = {
    "Star Tech": "/product/search?search={q}",
    "Mobile Buzz BD": "/?s={q}&post_type=product",
    "Taham Tech": "/?s={q}&post_type=product",
}

def price_from_text(text):
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d{2,3}(?:,\d{3})+|\d{5,6}", text)]
    nums = [n for n in nums if 8000 <= n <= 400000]
    return nums

async def read_price(page):
    # 1. JSON-LD structured data
    for block in await page.eval_on_selector_all(
            'script[type="application/ld+json"]', "els => els.map(e => e.textContent)"):
        try:
            data = json.loads(block)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                if "price" in d or "lowPrice" in d:
                    try:
                        v = float(str(d.get("price") or d.get("lowPrice")).replace(",", ""))
                        if 8000 <= v <= 400000:
                            return int(v)
                    except Exception:
                        pass
                stack.extend(d.values())
            elif isinstance(d, list):
                stack.extend(d)
    # 2. meta tags
    for sel in ['meta[property="product:price:amount"]', 'meta[property="og:price:amount"]']:
        el = await page.query_selector(sel)
        if el:
            try:
                v = float((await el.get_attribute("content")).replace(",", ""))
                if 8000 <= v <= 400000:
                    return int(v)
            except Exception:
                pass
    # 3. visible text near the title
    body = await page.inner_text("body")
    head = body[:3000]
    nums = price_from_text(head)
    return min(nums) if nums else None

def parse_ram_rom(text):
    ram = re.search(r"(\d{1,2})\s*GB\s*(?:RAM|/|\+)", text, re.I) or re.search(r"(\d{1,2})\s*/\s*\d{2,4}\s*GB", text, re.I)
    rom = re.search(r"(?:/|\+|\s)(\d{3,4})\s*(GB|TB)", text, re.I) or re.search(r"(\d{2,4})\s*(GB|TB)", text, re.I)
    return (ram.group(1) + "GB" if ram else "-",
            (rom.group(1) + rom.group(2).upper()) if rom else "-")

def parse_variant(text):
    t = text.lower()
    if "china" in t or "chinese" in t or "cn version" in t:
        return "china"
    if "official" in t:
        return "official"
    return "global"

def title_matches(phone, title):
    return all(w in title.lower() for w in phone.lower().split())

async def find_results(page, base, shop, phone):
    from urllib.parse import quote_plus
    q = quote_plus(phone)
    if shop in SEARCH_URLS:
        await page.goto(base + SEARCH_URLS[shop].format(q=q), timeout=45000)
    else:
        await page.goto(base, timeout=45000)
        box = await page.query_selector('input[type="search"], input[name="search"], input[name="q"], input[name="s"], input[placeholder*="earch"]')
        if not box:
            return []
        await box.fill(phone)
        await box.press("Enter")
    await page.wait_for_load_state("networkidle", timeout=30000)
    links = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => [e.href, (e.innerText || e.title || '').trim()])")
    out, seen = [], set()
    for href, text in links:
        if href in seen or not text:
            continue
        if title_matches(phone, text):
            seen.add(href)
            out.append((href, text))
    return out[:4]

async def scrape(browser, shop, base, phone):
    ctx = await browser.new_context(user_agent="Mozilla/5.0 (compatible; PhonePriceBD/1.0)")
    page = await ctx.new_page()
    try:
        for href, text in await find_results(page, base, shop, phone):
            await asyncio.sleep(DELAY)
            await page.goto(href, timeout=45000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            h1 = await page.query_selector("h1")
            title = (await h1.inner_text()) if h1 else text
            if not title_matches(phone, title):
                continue
            price = await read_price(page)
            if not price:
                continue
            ram, rom = parse_ram_rom(title)
            return {"shop": shop, "ram": ram, "rom": rom,
                    "variant": parse_variant(title), "price": price, "url": href}
    finally:
        await ctx.close()
    return None

async def main():
    try:
        old = {p["name"]: p["offers"] for p in json.load(open("prices.json"))["phones"]}
    except Exception:
        old = {}
    result = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for phone in PHONES:
            offers = []
            for shop, base in SHOPS.items():
                try:
                    o = await scrape(browser, shop, base, phone)
                    if o:
                        offers.append(o)
                        print("ok", shop, phone, o["price"])
                    else:
                        print("none", shop, phone)
                except Exception as e:
                    print("skip", shop, phone, str(e)[:80])
                await asyncio.sleep(DELAY)
            if not offers:
                offers = old.get(phone, [])
            if offers:
                result.append({"name": phone, "offers": offers})
        await browser.close()
    tz = datetime.timezone(datetime.timedelta(hours=6))
    out = {"updated": datetime.datetime.now(tz).isoformat(), "phones": result}
    json.dump(out, open("prices.json", "w"), ensure_ascii=False, indent=2)
    print("saved", len(result), "phones")

asyncio.run(main())
