from apify import Actor
from crawlee.router import Router
from crawlee.crawlers import PlaywrightCrawlingContext
from datetime import datetime
import re
import asyncio

router = Router[PlaywrightCrawlingContext]()

# --- PAGE HELPERS ---
async def smooth_scroll(page, *, steps: int = 4, distance: int = 2000, delay_ms: int = 700):
    """Scrolls the page in increments so lazy-loaded lists render cards consistently."""
    for _ in range(steps):
        await page.mouse.wheel(0, distance)
        await page.wait_for_timeout(delay_ms)

# --- UTILS ---
def clean_text(text):
    if not text: return None
    cleaned = ' '.join(str(text).strip().split())
    
    # 1. Critical: Reject URLs/Paths (Fixes Yatra bug)
    if "http" in cleaned or "//" in cleaned or ".com" in cleaned or ".jpg" in cleaned: return None
    
    # 2. Reject Prices/Numbers
    if "₹" in cleaned or re.match(r'^[\d,]+$', cleaned): return None
    
    # 3. Reject "X rooms left" or "Reviews" patterns
    if re.search(r'\d+\s+rooms?\s+left', cleaned, re.IGNORECASE): return None
    if re.search(r'\d+\s+reviews?', cleaned, re.IGNORECASE): return None
    
    # 4. Reject UI Noise
    bad_phrases = [
        "filters", "sort by", "map", "amenities", "price", "modify", "hotels", 
        "per night", "view details", "book", "reviews", "star", "rating", "discount", 
        "off", "sold out", "click", "taxes", "fees", "refund", "free wifi", "breakfast",
        "candolim", "calangute", "baga", "goa", "india" # Reject pure location names
    ]
    if any(phrase == cleaned.lower() for phrase in bad_phrases): return None
    
    if len(cleaned) < 4: return None
    return cleaned

def extract_price(text):
    if not text: return None
    nums = re.findall(r'[\d,]+', str(text))
    return nums[0].replace(',', '') if nums else None

async def extract_image(card):
    try:
        img = card.locator('img').first
        if await img.count() > 0:
            src = await img.get_attribute('src')
            if not src or "data:image" in src:
                src = await img.get_attribute('data-src') or await img.get_attribute('data-lazy')
            return src or "N/A"
    except: pass
    return "N/A"

async def extract_rating(card):
    try:
        texts = await card.locator('div, span').all_inner_texts()
        for t in texts:
            if re.match(r'^\d(\.\d)?(/\d)?$', t.strip()) and len(t.strip()) < 4:
                return t.strip()
    except: pass
    return "N/A"

async def extract_amenities(card):
    try:
        items = await card.locator('ul li, .features, .amenity, span, div').all_inner_texts()
        valid = [t.strip() for t in items if len(t.strip()) > 3 and len(t.strip()) < 20 and clean_text(t)]
        # Filter duplicates and return top 3
        return ", ".join(list(set(valid))[:3])
    except: return "Standard"

# --- THE "SILVER BULLET" NAME FINDER ---
async def find_best_name(card):
    """
    Scans ALL text in the card and picks the best 'Hotel Name' candidate.
    Logic: Hotel Name is usually the longest text that isn't a description.
    """
    try:
        # Get every single piece of text in the card
        texts = await card.locator('*').all_inner_texts()
        best_name = None
        max_len = 0
        
        for text in texts:
            cleaned = clean_text(text)
            if cleaned:
                # Heuristic: Hotel names are usually 10-60 chars long
                if len(cleaned) > max_len and len(cleaned) < 65:
                    # Prefer text that starts with uppercase
                    if cleaned[0].isupper():
                        max_len = len(cleaned)
                        best_name = cleaned
        return best_name
    except: return None

# --- HANDLERS ---

@router.handler("BOOKING")
async def handle_booking(context: PlaywrightCrawlingContext) -> None:
    page = context.page
    data = context.request.user_data
    results = []
    try:
        await page.wait_for_selector('[data-testid="property-card"]', timeout=30000)
        cards = await page.locator('[data-testid="property-card"]').all()
        for card in cards[:20]:
            item = {"source": "Booking.com", "destination": data.get('destination'), "check_in": data.get('checkIn'), "check_out": data.get('checkOut')}
            try:
                name = await card.locator('[data-testid="title"]').first.inner_text(timeout=2000)
                price = await card.locator('[data-testid="price-and-discounted-price"]').first.inner_text(timeout=2000)
                item["hotel_name"] = clean_text(name)
                item["price_numeric"] = extract_price(price)
                item["price_display"] = price
                item["hotel_img"] = await extract_image(card)
                item["rating"] = await extract_rating(card)
                item["amenities"] = "Free WiFi"
                if item.get("hotel_name"): results.append(item)
            except: continue
    except: pass
    if results: await context.push_data(results)

@router.handler("YATRA")
async def handle_yatra(context: PlaywrightCrawlingContext) -> None:
    context.log.info("Processing Yatra...")
    page = context.page
    data = context.request.user_data
    results = []
    try:
        await page.wait_for_load_state("domcontentloaded")
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(2000)
        
        # 1. Find Cards by Price Anchors (Most reliable)
        price_els = await page.locator('text=/₹/').all()
        
        seen_names = set()
        for price_el in price_els[:30]:
            item = {"source": "Yatra", "destination": data.get('destination'), "check_in": data.get('checkIn'), "check_out": data.get('checkOut')}
            try:
                # Go up 3 levels to find the "Card" container
                card = price_el.locator('xpath=./ancestor::div[3]')
                
                # Use Visual Text Analysis to find the name
                name = await find_best_name(card)
                price = await price_el.inner_text(timeout=1000)

                item["hotel_name"] = name
                item["price_numeric"] = extract_price(price)
                item["price_display"] = price
                item["hotel_img"] = await extract_image(card)
                item["rating"] = await extract_rating(card)
                item["amenities"] = await extract_amenities(card)

                if item.get("hotel_name") and item.get("price_numeric") and item["hotel_name"] not in seen_names:
                    results.append(item)
                    seen_names.add(item["hotel_name"])
            except: continue
    except: pass
    if results: await context.push_data(results)

@router.handler("MMT")
async def handle_mmt(context: PlaywrightCrawlingContext) -> None:
    context.log.info("Processing MMT...")
    page = context.page
    data = context.request.user_data
    results = []
    seen = set()
    try:
        if "Access Denied" in await page.content(): return

        # Makemytrip sometimes renders into #hotelListingContainer first; fall back to
        # the older id-based cards. Waiting for either prevents the handler from timing out.
        try:
            await page.wait_for_selector('#hotelListingContainer, [id^="htl_id"], .listingRowOuter', timeout=30000)
        except:  # pragma: no cover - best effort
            pass

        # Prime the page by scrolling; cards load lazily.
        await smooth_scroll(page, steps=5, distance=1800, delay_ms=900)

        # Standard Selectors
        cards = await page.locator('#hotelListingContainer .listingRowOuter, [id^="htl_id"], .listingRowOuter, .listingRow, [class*="HotelCard" i]').all()

        # Fallback to Text Anchors if empty
        if len(cards) == 0:
            price_els = await page.locator('text=/₹|Rs|INR/').all()
            # MMT nesting is deep, usually 4-6 divs up
            cards = [p.locator('xpath=./ancestor::div[contains(@class,"listingRow") or contains(@class,"card")][1]') for p in price_els]

        for card in cards[:25]:
            item = {"source": "MakeMyTrip", "destination": data.get('destination'), "check_in": data.get('checkIn'), "check_out": data.get('checkOut')}
            try:
                # Try ID first (Fastest)
                name = None
                if await card.locator('[id*="hotel_name"]').count() > 0:
                    name = await card.locator('[id*="hotel_name"]').first.inner_text(timeout=1000)
                else:
                    # Fallback to Text Analysis
                    name = await find_best_name(card)
                
                # Get Price
                price = "0"
                if await card.locator('text=/₹/').count() > 0:
                    price = await card.locator('text=/₹/').first.inner_text(timeout=1000)
                elif await card.locator('[class*="price" i]').count() > 0:
                    price = await card.locator('[class*="price" i]').first.inner_text(timeout=1000)
                else:
                    # Deep fallback: grab any rupee-prefixed substring from the card text
                    raw_texts = await card.all_inner_texts()
                    price_text = next((t for t in raw_texts if "₹" in t or "Rs" in t), "0")
                    price = price_text

                item["hotel_name"] = clean_text(name)
                item["price_numeric"] = extract_price(price)
                item["price_display"] = price
                item["hotel_img"] = await extract_image(card)
                item["rating"] = await extract_rating(card)
                item["amenities"] = await extract_amenities(card)
                
                if item["hotel_name"] and item["hotel_name"] not in seen:
                    results.append(item)
                    seen.add(item["hotel_name"])
            except: continue
    except: pass
    if results: await context.push_data(results)

@router.handler("CLEARTRIP")
async def handle_cleartrip(context: PlaywrightCrawlingContext) -> None:
    context.log.info("Processing Cleartrip...")
    page = context.page
    data = context.request.user_data
    results = []
    seen = set()
    try:
        await page.wait_for_load_state("domcontentloaded")
        if "Access Denied" in await page.content(): return

        # Cleartrip uses virtualized lists; scroll to force first batch of cards.
        await smooth_scroll(page, steps=5, distance=1500, delay_ms=600)

        # Prefer structured cards; fall back to price anchors if the layout differs.
        cards = await page.locator('[data-testid="ResultCard"], [data-testid="hotelCard"], article, [class*="HotelCard" i], [class*="ResultCard" i]').all()

        # If no cards resolved, use price anchors and walk up the tree to locate a container.
        if len(cards) == 0:
            price_els = await page.locator('text=/₹|Rs|INR/').all()
            cards = [p.locator('xpath=./ancestor::div[contains(@class,"Card") or contains(@class,"result") or contains(@class,"hotel")][1]') for p in price_els]

        for card in cards:
            item = {"source": "Cleartrip", "destination": data.get('destination'), "check_in": data.get('checkIn'), "check_out": data.get('checkOut')}
            try:
                # Name candidates (ordered preference)
                name_locator = card.locator('[data-testid="hotelName"], [itemprop="name"], h3, h2, a')
                name = None
                if await name_locator.count() > 0:
                    name = await name_locator.first.inner_text(timeout=1000)
                if not name:
                    name = await find_best_name(card)

                # Price candidates
                price_locator = card.locator('text=/₹|Rs/, [class*="price" i]')
                price = None
                if await price_locator.count() > 0:
                    price = await price_locator.first.inner_text(timeout=1000)
                else:
                    raw_texts = await card.all_inner_texts()
                    price = next((t for t in raw_texts if "₹" in t or "Rs" in t or "INR" in t), None)

                item["hotel_name"] = clean_text(name)
                item["price_numeric"] = extract_price(price)
                item["price_display"] = price or "N/A"
                item["hotel_img"] = await extract_image(card)
                item["rating"] = await extract_rating(card)
                item["amenities"] = await extract_amenities(card)

                if item.get("hotel_name") and item.get("price_numeric") and item["hotel_name"] not in seen:
                    results.append(item)
                    seen.add(item["hotel_name"])
            except: continue
    except: pass
    if results: await context.push_data(results)
