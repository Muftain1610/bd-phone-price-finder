"""Refresh prices.json with the China 12GB/256GB price of the Vivo X300 from each shop.

Safety rules (a bad read must never damage the site):
  * a price is accepted only if it is a plausible phone price (MIN..MAX) and
    within MAX_JUMP of the last known price for that shop;
  * if a shop fails, its last known price and link are kept and the offer is
    marked "stale": true, so the page can say it was not re-checked;
  * prices.json is rewritten only when at least one shop was read successfully.
"""
import asyncio, datetime, html as htmllib, json, re, sys

PHONE = "Vivo X300 5G"
MIN_PRICE, MAX_PRICE = 40000, 200000
MAX_JUMP = 0.12          # reject a new price that differs by more than 12% from the last one
DELAY = 2                # seconds between shops, be polite
TZ = datetime.timezone(datetime.timedelta(hours=6))

SHOPS = {
    "KRY International": "https://kryinternational.com/products/vivo-x300",
    "SMS Gadget": "https://smsgadget.com/product/vivo-x300",
    "Like Telecom": "https://liketelecom.com.bd/vivo-x300-2",
    "Rio International": "https://riointernational.com.bd/product/vivo-x300-5g",
    "Apple Gadgets BD": "https://www.applegadgetsbd.com/product/vivo-x300",
    "Sumash Tech": "https://www.sumashtech.com/product/vivo-x300",
    "Dazzle": "https://dazzle.com.bd/product/vivo-x300-price-in-bangladesh",
}


# ---------- pure parsing helpers (tested offline against saved pages) ----------

def to_int(s):
    s = str(s).split(".")[0]
    d = re.sub(r"[^\d]", "", s)
    return int(d) if d else None


def visible_text(html):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S | re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t)


def ld_price(html):
    """Price of the first schema.org Product in the page's JSON-LD, or None."""
    for block in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop(0)
            if isinstance(d, dict):
                if d.get("@type") in ("Product", "ProductGroup"):
                    offers = d.get("offers")
                    offers = offers if isinstance(offers, list) else [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price") not in (None, ""):
                            return to_int(o["price"])
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
            elif isinstance(d, list):
                stack.extend(d)
    return None


def parse_like(html):
    t = visible_text(html)
    if "12/256GB" in t and "CHINA" in t:
        return ld_price(html)
    return None


def parse_sumash(html):
    t = visible_text(html)
    # the product line reads "China | 12/256GB | <colour>"; tolerate extra spacing or reordering
    if re.search(r"China", t) and re.search(r"12\s*/\s*256\s*GB", t):
        price = ld_price(html)
        if price is None:
            m = re.search(r"৳\s?([\d,]+)\s*\(Cash Price\)", t)
            price = to_int(m.group(1)) if m else None
        return price
    return None


def parse_dazzle(html):
    t = visible_text(html)
    if re.search(r"12/256GB\s*Region/Variant\s*:\s*China", t):
        return ld_price(html)
    return None


def parse_kry(html):
    """KRY embeds every variant (ram, storage, region, price, discount) in its page data."""
    s = html.replace('\\"', '"')
    for m in re.finditer(r'"CN"', s):
        before = s[max(0, m.start() - 160):m.start()]
        if '"12"' in before and "256GB" in before:
            p = re.search(r'"price":(\d+),"discountPrice":(\d+)', s[m.end():m.end() + 2500])
            if p:
                price = int(p.group(1)) - int(p.group(2))
                # the page also lists cheap related products; only a flagship-range price is ours
                if MIN_PRICE <= price <= MAX_PRICE:
                    return price
    return None


def parse_apple_text(text):
    m = re.search(r"৳\s?([\d,]+)\s*\(Cash Price\)", text)
    return to_int(m.group(1)) if m else None


def parse_rio_text(text):
    m = re.search(r"Vivo X300 5G\s*৳\s?([\d,]+(?:\.\d+)?)", text)
    return to_int(m.group(1)) if m else None


def parse_sms_text(text):
    if "12/256" not in text:
        return None
    m = re.search(r"Vivo X300\s*(?:\d+%\s*OFF)?\s*৳\s?([\d,]+)", text, re.I)
    return to_int(m.group(1)) if m else None


# ---------- browser reading ----------

async def open_page(ctx, url):
    page = await ctx.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)
    return page


async def click_if_present(page, label, role=None):
    try:
        loc = page.get_by_role(role, name=label, exact=True) if role else page.get_by_text(label, exact=True)
        if await loc.count():
            await loc.first.click(timeout=4000)
            await page.wait_for_timeout(800)
    except Exception:
        pass


async def read_shop(ctx, name, url):
    page = await open_page(ctx, url)
    try:
        if name == "KRY International":
            return parse_kry(await page.content())
        if name == "Like Telecom":
            return parse_like(await page.content())
        if name == "Sumash Tech":
            return parse_sumash(await page.content())
        if name == "Dazzle":
            return parse_dazzle(await page.content())
        if name == "Apple Gadgets BD":
            await click_if_present(page, "CN", role="button")
            await click_if_present(page, "12/256GB", role="button")
            return parse_apple_text(await page.inner_text("body"))
        if name == "Rio International":
            await click_if_present(page, "12/256 GB")
            await click_if_present(page, "CHN")
            return parse_rio_text(await page.inner_text("body"))
        if name == "SMS Gadget":
            html = await page.content()
            text = await page.inner_text("body")
            if "12/256" in text:
                return ld_price(html) or parse_sms_text(text)
            return None
    finally:
        await page.close()
    return None


def acceptable(new, old):
    if new is None or not (MIN_PRICE <= new <= MAX_PRICE):
        return False, "out of range or not found"
    if old and abs(new - old) / old > MAX_JUMP:
        return False, f"jumped from {old} to {new} (more than {int(MAX_JUMP*100)}%)"
    return True, ""


async def main():
    from playwright.async_api import async_playwright

    try:
        data = json.load(open("prices.json", encoding="utf8"))
    except Exception:
        data = {"updated": None, "phones": []}
    phone = next((p for p in data["phones"] if p["name"] == PHONE), None)
    if phone is None:
        phone = {"name": PHONE, "offers": []}
        data["phones"].append(phone)
    by_shop = {o["shop"]: o for o in phone["offers"]}

    now = datetime.datetime.now(TZ).isoformat(timespec="seconds")
    ok_count = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            locale="en-US", timezone_id="Asia/Dhaka")
        for name, url in SHOPS.items():
            offer = by_shop.setdefault(name, {"shop": name, "url": url, "ram": "12GB", "rom": "256GB",
                                              "variant": "china", "price": None})
            offer["url"] = url
            price, why = None, "no attempt"
            for attempt in (1, 2):
                try:
                    price = await read_shop(ctx, name, url)
                    good, why = acceptable(price, offer.get("price"))
                    if good:
                        break
                except Exception as e:
                    why = f"error: {str(e)[:80]}"
                    price = None
                await asyncio.sleep(DELAY)
            good, why = acceptable(price, offer.get("price"))
            if good:
                offer["price"] = price
                offer["checked"] = now
                offer.pop("stale", None)
                offer.pop("why", None)
                ok_count += 1
                print("ok   ", name, price)
            else:
                offer["stale"] = True
                offer["why"] = why[:120]   # reason, so a failing shop can be diagnosed from prices.json
                print("STALE", name, "-", why, "(kept", offer.get("price"), ")")
            await asyncio.sleep(DELAY)
        await browser.close()

    phone["offers"] = [by_shop[n] for n in SHOPS]  # keep only the tracked shops, in a stable order
    if ok_count == 0:
        print("No shop could be read; prices.json left untouched.")
        sys.exit(0)
    data["updated"] = now
    json.dump(data, open("prices.json", "w", encoding="utf8"), ensure_ascii=False, indent=2)
    print(f"saved {ok_count}/{len(SHOPS)} shops at {now}")


if __name__ == "__main__":
    asyncio.run(main())
