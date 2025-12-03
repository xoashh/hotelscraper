#!/usr/bin/env python3
import asyncio
from datetime import datetime, timedelta
from urllib.parse import quote

from apify import Actor
from crawlee import Request
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext

try:
    # Allow running both as a package (Apify entrypoint) and as a script for local debugging
    from .routes import router  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    from routes import router

CITY_MAP = {
    "goa": "CTGOI", "mumbai": "CTBOM", "delhi": "CTDEL",
    "bangalore": "CTBLR", "london": "CTLON", "dubai": "CTDXB",
    "paris": "CTPAR", "new york": "CTNYC"
}

def format_date(date_str, fmt):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime(fmt)
    except Exception:
        return date_str

def build_booking_url(dest, ci, co):
    return f"https://www.booking.com/searchresults.html?ss={quote(dest)}&checkin={ci}&checkout={co}&group_adults=2&no_rooms=1"

def build_yatra_url(dest, ci, co):
    # Yatra PWA format
    c_in = format_date(ci, "%d/%m/%Y")
    c_out = format_date(co, "%d/%m/%Y")
    d = quote(dest)
    return (
        f"https://hotel.yatra.com/nextui/hotel-search/dom/search?"
        f"checkinDate={quote(c_in)}&checkoutDate={quote(c_out)}&"
        f"source=BOOKING_ENGINE&pg=1&tenant=PWA&isPersnldSrp=1&"
        f"city.name={d}&city.code={d}&state.name={d}&state.code={d}&"
        f"country.name=India&country.code=IND&"
        f"roomRequests%5B0%5D.id=1&roomRequests%5B0%5D.noOfAdults=2&roomRequests%5B0%5D.noOfChildren=0"
    )

def build_mmt_url(dest, ci, co):
    city_code = CITY_MAP.get(dest.lower(), dest)
    c_in = format_date(ci, "%m%d%Y")
    c_out = format_date(co, "%m%d%Y")
    return (
        f"https://www.makemytrip.com/hotels/hotel-listing/?"
        f"checkin={c_in}&checkout={c_out}&"
        f"city={city_code}&locusId={city_code}&"
        f"country=IN&locusType=city&"
        f"searchText={quote(dest)}&"
        f"roomStayQualifier=2e0e&rsc=1e2e0e"
    )

def build_cleartrip_url(dest, ci, co):
    c_in = format_date(ci, "%d/%m/%Y")
    c_out = format_date(co, "%d/%m/%Y")
    return f"https://www.cleartrip.com/hotels/results?city={quote(dest)}&chk_in={c_in}&chk_out={c_out}&adults=2&childs=0&rooms=1"

async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        destination = actor_input.get("destination", "Goa")
        check_in = actor_input.get("checkInDate", "2025-12-01")
        check_out = actor_input.get("checkOutDate", "2025-12-05")
        proxy_config = actor_input.get("proxyConfiguration", {})

        if proxy_config and proxy_config.get("useApifyProxy"):
            proxy_configuration = await Actor.create_proxy_configuration(
                groups=proxy_config.get("apifyProxyGroups", ["RESIDENTIAL"]),
                country_code="IN",
            )
        else:
            proxy_configuration = None

        user_context = {
            "destination": destination,
            "checkIn": check_in,
            "checkOut": check_out,
        }

        start_requests = [
            Request.from_url(build_booking_url(destination, check_in, check_out), label="BOOKING", user_data=user_context),
            Request.from_url(build_yatra_url(destination, check_in, check_out), label="YATRA", user_data=user_context),
            Request.from_url(build_mmt_url(destination, check_in, check_out), label="MMT", user_data=user_context),
            Request.from_url(build_cleartrip_url(destination, check_in, check_out), label="CLEARTRIP", user_data=user_context),
        ]

        crawler = PlaywrightCrawler(
            request_handler=router,
            proxy_configuration=proxy_configuration,
            headless=True,
            request_handler_timeout=timedelta(minutes=6),
            max_request_retries=1,
            max_requests_per_crawl=len(start_requests),
            browser_launch_options={
                "args": [
                    "--disable-http2",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                ],
                "ignore_https_errors": True,
            },
        )

        @crawler.pre_navigation_hook
        async def configure_browser(context: PlaywrightCrawlingContext) -> None:
            await context.page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            context.page.set_default_navigation_timeout(90000)
            context.page.set_default_timeout(60000)
            await context.page.set_extra_http_headers(
                {
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    "Referer": "https://www.google.com/",
                }
            )
            context.log.info(f"Navigating {context.request.label} :: {context.request.url}")

        await crawler.run(start_requests)

if __name__ == "__main__":
    asyncio.run(main())
