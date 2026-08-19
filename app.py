from flask import Flask, render_template, jsonify
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

# =========================================================
# CACHE SETTINGS
# =========================================================

CACHE_MINUTES = 15

cached_events = []
last_successful_refresh = None
refresh_in_progress = False

cache_lock = threading.Lock()


# =========================================================
# PARSE TIMETREE EVENT TEXT
# =========================================================

def parse_event_text(raw_text):
    text = " ".join(raw_text.split()).strip()

    # Remove trailing "Like"
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
# SCRAPE TIMETREE
# =========================================================

def scrape_timetree():
    events = []

    print("Starting TimeTree scrape...")

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

            # Wait for TimeTree JS
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


    # Remove dates before today
    today_slt = datetime.now(
        SL_TIMEZONE
    ).date()

    events = [
        event
        for event in events
        if event["start_datetime"].date()
        >= today_slt
    ]


    # Sort chronologically
    events.sort(
        key=lambda event:
        event["start_datetime"]
    )


    print(
        "TimeTree scrape complete:",
        len(events),
        "events"
    )

    return events[:40]


# =========================================================
# UPDATE CACHE
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
                "Calendar cache updated successfully."
            )

            return True


        print(
            "Scrape returned no events. "
            "Keeping previous cache."
        )

        return False


    finally:

        with cache_lock:
            refresh_in_progress = False


# =========================================================
# CHECK IF CACHE NEEDS REFRESH
# =========================================================

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


# =========================================================
# BACKGROUND REFRESH
# =========================================================

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
# HOME PAGE
# =========================================================

@app.route("/")
def home():

    # If we already have cached events,
    # show them immediately.

    with cache_lock:
        events = list(cached_events)
        last_refresh = last_successful_refresh


    # Refresh stale cache in background
    if cache_is_stale():
        start_background_refresh()


    # If cache is totally empty, first visit
    # needs an initial scrape.

    if not events:

        refresh_cache()

        with cache_lock:
            events = list(cached_events)
            last_refresh = last_successful_refresh


    return render_template(
        "index.html",
        events=events,
        updated=last_refresh
    )


# =========================================================
# MANUAL REFRESH
# =========================================================

@app.route("/refresh")
def refresh():

    success = refresh_cache()

    with cache_lock:
        count = len(cached_events)
        last_refresh = last_successful_refresh

    return jsonify({
        "success": success,
        "count": count,
        "last_refresh": (
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
        events = list(cached_events)
        last_refresh = last_successful_refresh

    safe_events = []

    for event in events:

        safe_events.append({
            "title": event["title"],
            "date": event["date"],
            "start_time": event["start_time"],
            "end_time": event["end_time"],
            "url": event["url"]
        })

    return jsonify({
        "count": len(safe_events),
        "cache_minutes": CACHE_MINUTES,
        "refresh_in_progress": refresh_in_progress,
        "last_refresh": (
            last_refresh.isoformat()
            if last_refresh
            else None
        ),
        "events": safe_events
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "GPhone Calendar Online"
    })


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
