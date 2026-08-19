from flask import Flask, render_template, jsonify, request
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import threading

app = Flask(__name__)

TIMETREE_URL = (
    "https://timetreeapp.com/public_calendars/"
    "greek_community_calendar"
)

SL_TIMEZONE = ZoneInfo("America/Los_Angeles")

CACHE_MINUTES = 15

cached_events = []
last_successful_refresh = None
refresh_in_progress = False

cache_lock = threading.Lock()


# =========================================================
# PARSE CALENDAR EVENT TEXT
# =========================================================

def parse_event_text(raw_text):

    text = " ".join(raw_text.split()).strip()

    text = re.sub(
        r"\s+Like\s*$",
        "",
        text,
        flags=re.IGNORECASE
    )

    pattern = re.compile(
        r"^(.*?)\s+"
        r"((?:Sun|Mon|Tue|Wed|Thu|Fri|Sat),\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
        r"\d{1,2},\s+\d{4})\s+"
        r"(\d{1,2}:\d{2}\s+[AP]M)\s*-\s*"
        r"(\d{1,2}:\d{2}\s+[AP]M)$"
    )

    match = pattern.match(text)

    if not match:
        return None

    title = match.group(1).strip()
    date_text = match.group(2).strip()
    start_time = match.group(3).strip()
    end_time = match.group(4).strip()

    try:
        start_datetime = datetime.strptime(
            f"{date_text} {start_time}",
            "%a, %b %d, %Y %I:%M %p"
        )

        start_datetime = start_datetime.replace(
            tzinfo=SL_TIMEZONE
        )

    except ValueError:
        return None

    return {
        "title": title,
        "date": date_text,
        "start_time": start_time,
        "end_time": end_time,
        "start_datetime": start_datetime
    }


# =========================================================
# SCRAPE MAIN TIMETREE CALENDAR
# =========================================================

def scrape_timetree():

    events = []

    try:
        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            context = browser.new_context(
                viewport={
                    "width": 390,
                    "height": 844
                },
                locale="en-US",
                timezone_id="America/Los_Angeles"
            )

            page = context.new_page()

            page.goto(
                TIMETREE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(5000)

            seen = set()

            links = page.locator("a").all()

            for link in links:

                try:

                    href = link.get_attribute("href")

                    if not href:
                        continue

                    if "/events/" not in href:
                        continue

                    full_url = urljoin(
                        "https://timetreeapp.com",
                        href
                    )

                    if full_url in seen:
                        continue

                    raw_text = link.inner_text().strip()

                    if not raw_text:
                        continue

                    parsed = parse_event_text(
                        raw_text
                    )

                    if not parsed:
                        continue

                    seen.add(full_url)

                    events.append({
                        "title": parsed["title"],
                        "date": parsed["date"],
                        "start_time": parsed["start_time"],
                        "end_time": parsed["end_time"],
                        "start_datetime": parsed["start_datetime"],
                        "url": full_url
                    })

                except Exception:
                    continue

            context.close()
            browser.close()

    except Exception as error:

        print(
            "TIMETREE SCRAPE ERROR:",
            repr(error)
        )

        return []


    today_slt = datetime.now(
        SL_TIMEZONE
    ).date()


    events = [
        event
        for event in events
        if event["start_datetime"].date()
        >= today_slt
    ]


    events.sort(
        key=lambda event:
        event["start_datetime"]
    )


    return events[:40]


# =========================================================
# CACHE
# =========================================================

def refresh_cache():

    global cached_events
    global last_successful_refresh
    global refresh_in_progress

    with cache_lock:

        if refresh_in_progress:
            return False

        refresh_in_progress = True


    try:

        new_events = scrape_timetree()

        if new_events:

            with cache_lock:

                cached_events = new_events

                last_successful_refresh = (
                    datetime.now(
                        SL_TIMEZONE
                    )
                )

            print(
                "Calendar cache updated:",
                len(new_events),
                "events"
            )

            return True


        print(
            "Calendar scrape returned no events. "
            "Keeping old cache."
        )

        return False


    finally:

        with cache_lock:
            refresh_in_progress = False


def cache_is_stale():

    with cache_lock:

        if last_successful_refresh is None:
            return True

        age = (
            datetime.now(SL_TIMEZONE)
            -
            last_successful_refresh
        )

    return age > timedelta(
        minutes=CACHE_MINUTES
    )


def start_background_refresh():

    if not cache_is_stale():
        return

    with cache_lock:

        if refresh_in_progress:
            return

    thread = threading.Thread(
        target=refresh_cache
    )

    thread.daemon = True

    thread.start()


# =========================================================
# SCRAPE ONE EVENT DETAIL PAGE
# =========================================================

def scrape_event_details(event_url):

    details = {
        "title": "",
        "date": "",
        "start_time": "",
        "end_time": "",
        "description": "",
        "image": "",
        "original_url": event_url
    }

    try:

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            context = browser.new_context(
                viewport={
                    "width": 390,
                    "height": 844
                },
                locale="en-US",
                timezone_id="America/Los_Angeles"
            )

            page = context.new_page()

            page.goto(
                event_url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(4000)


            # =================================================
            # GET ALL VISIBLE TEXT
            # =================================================

            body_text = page.locator(
                "body"
            ).inner_text()

            lines = [
                " ".join(line.split()).strip()
                for line in body_text.splitlines()
                if line.strip()
            ]


            # =================================================
            # REMOVE TIMETREE / PRIVACY JUNK
            # =================================================

            junk_exact = {
                "We value your privacy",
                "Do Not Sell or Share My Personal Information",
                "Like",
                "Copy URL",
                "Contact us",
                "Check other events",
                "Acceptable Use Policy",
                "Public Calendar Acceptable Use Policy",
                "Privacy Policy",
                "Cookie Settings",
                "Greek Matrix Central Event Calendar"
            }

            clean_lines = []

            for line in lines:

                if line in junk_exact:
                    continue

                lower = line.lower()

                if (
                    "this website or its third-party tools "
                    "process personal data"
                    in lower
                ):
                    continue

                if (
                    "you can opt out of the sale of your "
                    "personal information"
                    in lower
                ):
                    continue

                clean_lines.append(
                    line
                )


            # =================================================
            # DATE / TIME PATTERNS
            # =================================================

            date_regex = re.compile(
                r"(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat),\s+"
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
                r"\d{1,2},?\s+\d{4}",
                re.IGNORECASE
            )

            time_range_regex = re.compile(
                r"(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*"
                r"(\d{1,2}:\d{2}\s*[AP]M)",
                re.IGNORECASE
            )


            # =================================================
            # TITLE / DATE / TIME
            #
            # Pull these from the already-clean calendar cache.
            # =================================================

            with cache_lock:

                for cached in cached_events:

                    if cached["url"] == event_url:

                        details["title"] = (
                            cached["title"]
                        )

                        details["date"] = (
                            cached["date"]
                        )

                        details["start_time"] = (
                            cached["start_time"]
                        )

                        details["end_time"] = (
                            cached["end_time"]
                        )

                        break


            # =================================================
            # FALLBACK TITLE
            # =================================================

            if not details["title"]:

                for line in clean_lines:

                    if date_regex.search(line):
                        continue

                    if time_range_regex.search(line):
                        continue

                    if len(line) < 4:
                        continue

                    details["title"] = line

                    break


            # =================================================
            # FALLBACK DATE
            # =================================================

            if not details["date"]:

                for line in clean_lines:

                    match = date_regex.search(
                        line
                    )

                    if match:

                        details["date"] = (
                            match.group(0)
                        )

                        break


            # =================================================
            # FALLBACK TIMES
            # =================================================

            if (
                not details["start_time"]
                or
                not details["end_time"]
            ):

                for line in clean_lines:

                    match = (
                        time_range_regex.search(
                            line
                        )
                    )

                    if match:

                        details["start_time"] = (
                            match.group(1)
                        )

                        details["end_time"] = (
                            match.group(2)
                        )

                        break


            # =================================================
            # DESCRIPTION
            # =================================================

            description_lines = []

            for line in clean_lines:

                # Skip event title
                if line == details["title"]:
                    continue


                # Skip exact cached date
                if (
                    details["date"]
                    and
                    details["date"].lower()
                    in line.lower()
                ):
                    continue


                # Skip date/time-only lines
                if date_regex.fullmatch(line):
                    continue

                if time_range_regex.fullmatch(line):
                    continue


                lower = line.lower()


                # Skip navigation / legal junk
                if lower in {
                    "share",
                    "calendar",
                    "public calendar",
                    "open in timetree"
                }:
                    continue


                if "cookie" in lower:
                    continue

                if "privacy policy" in lower:
                    continue

                if "acceptable use policy" in lower:
                    continue

                if "check other events" in lower:
                    continue

                if "do not sell" in lower:
                    continue


                if len(line) < 3:
                    continue


                description_lines.append(
                    line
                )


            # Remove duplicate consecutive lines
            deduped = []

            for line in description_lines:

                if (
                    not deduped
                    or
                    deduped[-1] != line
                ):
                    deduped.append(
                        line
                    )


            details["description"] = "\n".join(
                deduped[:18]
            )


            # =================================================
            # EVENT FLYER
            #
            # Prefer OpenGraph image.
            # =================================================

            og_image = page.locator(
                'meta[property="og:image"]'
            )

            if og_image.count() > 0:

                src = (
                    og_image
                    .first
                    .get_attribute(
                        "content"
                    )
                )

                if src:

                    details["image"] = (
                        urljoin(
                            "https://timetreeapp.com",
                            src
                        )
                    )


            # =================================================
            # FALLBACK IMAGE SEARCH
            # =================================================

            if not details["image"]:

                images = page.locator(
                    "img"
                )

                image_count = (
                    images.count()
                )

                for i in range(
                    image_count
                ):

                    image = images.nth(
                        i
                    )

                    src = (
                        image.get_attribute(
                            "src"
                        )
                    )

                    if not src:
                        continue


                    src_lower = (
                        src.lower()
                    )


                    if src.startswith(
                        "data:"
                    ):
                        continue

                    if "logo" in src_lower:
                        continue

                    if "icon" in src_lower:
                        continue

                    if "avatar" in src_lower:
                        continue

                    if "cookie" in src_lower:
                        continue


                    details["image"] = (
                        urljoin(
                            "https://timetreeapp.com",
                            src
                        )
                    )

                    break


            context.close()
            browser.close()


    except Exception as error:

        print(
            "EVENT DETAIL ERROR:",
            repr(error)
        )


    return details


# =========================================================
# HOME PAGE
# =========================================================

@app.route("/")
def home():

    with cache_lock:

        events = list(
            cached_events
        )

        last_refresh = (
            last_successful_refresh
        )


    # Serve cached events immediately and refresh
    # stale data in the background.

    if cache_is_stale():
        start_background_refresh()


    # If service just started and cache is empty,
    # do one initial scrape.

    if not events:

        refresh_cache()

        with cache_lock:

            events = list(
                cached_events
            )

            last_refresh = (
                last_successful_refresh
            )


    return render_template(
        "index.html",
        events=events,
        updated=last_refresh
    )


# =========================================================
# GPHONE EVENT DETAIL PAGE
# =========================================================

@app.route("/event")
def event_detail():

    event_url = request.args.get(
        "url",
        ""
    )


    if not event_url:

        return (
            "Missing event URL",
            400
        )


    # Security: only permit TimeTree URLs.

    if not event_url.startswith(
        "https://timetreeapp.com/"
    ):

        return (
            "Invalid event URL",
            400
        )


    details = scrape_event_details(
        event_url
    )


    return render_template(
        "event.html",
        event=details
    )


# =========================================================
# MANUAL REFRESH
# =========================================================

@app.route("/refresh")
def refresh():

    success = refresh_cache()


    with cache_lock:

        count = len(
            cached_events
        )

        last_refresh = (
            last_successful_refresh
        )


    return jsonify({

        "success":
            success,

        "count":
            count,

        "last_refresh":
            (
                last_refresh.isoformat()
                if last_refresh
                else None
            )
    })


# =========================================================
# DEBUG
# =========================================================

@app.route("/debug")
def debug():

    with cache_lock:

        events = list(
            cached_events
        )

        last_refresh = (
            last_successful_refresh
        )


    safe_events = []


    for event in events:

        safe_events.append({

            "title":
                event["title"],

            "date":
                event["date"],

            "start_time":
                event["start_time"],

            "end_time":
                event["end_time"],

            "url":
                event["url"]
        })


    return jsonify({

        "count":
            len(safe_events),

        "cache_minutes":
            CACHE_MINUTES,

        "refresh_in_progress":
            refresh_in_progress,

        "last_refresh":
            (
                last_refresh.isoformat()
                if last_refresh
                else None
            ),

        "events":
            safe_events
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status":
            "GPhone Calendar Online"
    })


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
