from flask import Flask, render_template
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin
from datetime import datetime

app = Flask(__name__)

TIMETREE_URL = (
    "https://timetreeapp.com/public_calendars/"
    "greek_community_calendar"
)


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

            page = browser.new_page(
                viewport={
                    "width": 390,
                    "height": 844
                }
            )

            page.goto(
                TIMETREE_URL,
                wait_until="networkidle",
                timeout=60000
            )

            # Give TimeTree's JavaScript a moment to finish
            page.wait_for_timeout(3000)

            links = page.locator("a").all()

            seen = set()

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

                    seen.add(full_url)

                    title = link.inner_text().strip()

                    if not title:
                        continue

                    # Clean excessive whitespace
                    title = " ".join(title.split())

                    events.append({
                        "title": title,
                        "url": full_url
                    })

                except Exception:
                    continue

            browser.close()

    except Exception as error:
        print(
            "PLAYWRIGHT / TIMETREE ERROR:",
            repr(error)
        )

    return events[:25]


@app.route("/")
def home():
    events = get_events()

    return render_template(
        "index.html",
        events=events,
        updated=datetime.now()
    )


@app.route("/health")
def health():
    return {
        "status": "GPhone Calendar Online",
        "source": "Greek Matrix Central Event Calendar"
    }


@app.route("/debug")
def debug():
    events = get_events()

    return {
        "count": len(events),
        "events": events
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
