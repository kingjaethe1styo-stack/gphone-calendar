from flask import Flask, render_template
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime

app = Flask(__name__)

TIMETREE_URL = (
    "https://timetreeapp.com/public_calendars/"
    "greek_community_calendar"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    )
}


def get_events():
    events = []

    try:
        response = requests.get(
            TIMETREE_URL,
            headers=HEADERS,
            timeout=15
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        seen = set()

        # Find links to public TimeTree event pages
        for link in soup.find_all("a", href=True):

            href = link.get("href", "")

            if "/events/" not in href:
                continue

            full_url = urljoin(
                "https://timetreeapp.com",
                href
            )

            if full_url in seen:
                continue

            seen.add(full_url)

            title = link.get_text(
                " ",
                strip=True
            )

            # Ignore useless/blank anchors
            if not title:
                continue

            events.append({
                "title": title,
                "url": full_url
            })

        # Keep page manageable inside Second Life
        return events[:20]

    except Exception as error:
        print("TimeTree error:", error)

        return []


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


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
