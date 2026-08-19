from flask import Flask, render_template
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin
from datetime import datetime
from zoneinfo import ZoneInfo
import re

app = Flask(__name__)

TIMETREE_URL = (
    "https://timetreeapp.com/public_calendars/"
    "greek_community_calendar"
)

SL_TIMEZONE = ZoneInfo("America/Los_Angeles")


# =========================================================
# CLEAN / PARSE EVENT TEXT
# =========================================================

def parse_event_text(raw_text):
    """
    Example raw TimeTree text:

    Rep Your Block Tue, Aug 18, 2026 5:00 PM - 7:00 PM Like

    Returns:
    {
        "title": "Rep Your Block",
        "date": "Tue, Aug 18, 2026",
        "start_time": "5:00 PM",
        "end_time": "7:00 PM",
        "start_datetime": datetime(...)
    }
    """

    text = " ".join(raw_text.split()).strip()

    # Remove trailing Like
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
# GET LIVE TIMETREE EVENTS
# =========================================================

def get_events():
    events = []

    try:
        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )

            # Explicitly use SL / Pacific timezone.
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

            # Give TimeTree JavaScript time to populate events.
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

                    parsed = parse_event_text(raw_text)

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
            "PLAYWRIGHT / TIMETREE ERROR:",
            repr(error)
        )

        return []


    # -----------------------------------------------------
    # REMOVE EVENTS FROM BEFORE TODAY
    #
    # Keep all events dated today or later.
    # -----------------------------------------------------

    now_slt = datetime.now(SL_TIMEZONE)

    today_slt = now_slt.date()

    events = [
        event
        for event in events
        if event["start_datetime"].date() >= today_slt
    ]


    # -----------------------------------------------------
    # SORT BY DATE/TIME
    # -----------------------------------------------------

    events.sort(
        key=lambda event: event["start_datetime"]
    )


    return events[:30]


# =========================================================
# MAIN PAGE
# =========================================================

@app.route("/")
def home():

    events = get_events()

    return render_template(
        "index.html",
        events=events,
        updated=datetime.now(SL_TIMEZONE)
    )


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return {
        "status": "GPhone Calendar Online",
        "source": "Greek Matrix Central Event Calendar"
    }


# =========================================================
# DEBUG
# =========================================================

@app.route("/debug")
def debug():

    events = get_events()

    safe_events = []

    for event in events:
        safe_events.append({
            "title": event["title"],
            "date": event["date"],
            "start_time": event["start_time"],
            "end_time": event["end_time"],
            "url": event["url"]
        })

    return {
        "count": len(safe_events),
        "timezone": "America/Los_Angeles",
        "events": safe_events
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
