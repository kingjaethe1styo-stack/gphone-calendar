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

            return True

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

            page.wait_for_timeout(3500)

            body_text = page.locator("body").inner_text()

            lines = [
                line.strip()
                for line in body_text.splitlines()
                if line.strip()
            ]

            # -----------------------------------------
            # TITLE
            # -----------------------------------------

            if lines:
                details["title"] = lines[0]


            # -----------------------------------------
            # DATE / TIMES
            # -----------------------------------------

            date_pattern = re.compile(
                r"(Sun|Mon|Tue|Wed|Thu|Fri|Sat),\s+"
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
                r"\d{1,2}\s+\d{4}"
            )

            time_pattern = re.compile(
                r"\d{1,2}:\d{2}\s*[AP]M",
                re.IGNORECASE
            )

            found_dates = []
            found_times = []

            for line in lines:

                if date_pattern.search(line):
                    found_dates.append(line)

                matches = time_pattern.findall(line)

                for match in matches:
                    found_times.append(match)


            if found_dates:
                details["date"] = found_dates[0]

            if len(found_times) >= 1:
                details["start_time"] = found_times[0]

            if len(found_times) >= 2:
                details["end_time"] = found_times[1]


            # -----------------------------------------
            # DESCRIPTION
            # -----------------------------------------

            skip_words = [
                "Like",
                "Copy URL",
                "Contact us",
                "Greek Matrix Central Event Calendar"
            ]

            description_lines = []

            for line in lines:

                if line == details["title"]:
                    continue

                if line in found_dates:
                    continue

                if any(
                    word.lower() == line.lower()
                    for word in skip_words
                ):
                    continue

                if time_pattern.fullmatch(line):
                    continue

                if len(line) < 3:
                    continue

                description_lines.append(line)


            details["description"] = "\n".join(
                description_lines[:15]
            )


            # -----------------------------------------
            # EVENT IMAGE
            # -----------------------------------------

            images = page.locator("img").all()

            for image in images:

                try:

                    src = image.get_attribute("src")

                    if not src:
                        continue

                    if src.startswith("data:"):
                        continue

                    if "logo" in src.lower():
                        continue

                    details["image"] = urljoin(
                        "https://timetreeapp.com",
                        src
                    )

                    break

                except Exception:
                    continue


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

        events = list(cached_events)

        last_refresh = (
            last_successful_refresh
        )


    if cache_is_stale():
        start_background_refresh()


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

        return "Missing event URL", 400


    # Only allow TimeTree public calendar URLs
    if not event_url.startswith(
        "https://timetreeapp.com/"
    ):

        return "Invalid event URL", 400


    details = scrape_event_details(
        event_url
    )


    return render_template(
        "event.html",
        event=details
    )


# =========================================================
# REFRESH
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
