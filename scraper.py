"""Scrape real Bellhaven community detail pages (including homepage-only new locations)."""
import csv
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = "https://analyst-assessment-production.up.railway.app"
FIELDS = ["name", "address", "city", "state", "zip", "care_offerings", "source_url"]
CITY_RE = re.compile(r"^\s*(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def detail_links(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(page_url, anchor.get("href", ""))
        path = urlparse(url).path.rstrip("/")
        if urlparse(url).netloc == urlparse(BASE).netloc and re.fullmatch(r"/communities/[a-z0-9-]+", path):
            links.add(BASE + path)
    return links


def parse_detail(html, url):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    if not heading:
        raise ValueError(f"No facility name on {url}")
    main = soup.select_one("main") or soup.body or soup
    lines = [" ".join(x.split()) for x in main.stripped_strings if x and x.strip()]
    # HTML layouts vary; use section labels rather than a fragile full-page regex.
    def after(label):
        for i, text in enumerate(lines):
            if text.casefold() == label.casefold():
                return i + 1
        raise ValueError(f"Missing {label} on {url}")
    index = after("Address")
    street = lines[index]
    if index + 1 >= len(lines):
        raise ValueError(f"Missing city/state/ZIP on {url}")
    m = CITY_RE.match(lines[index + 1])
    if not m:
        raise ValueError(f"Unrecognized city/state/ZIP: {lines[index+1]} on {url}")
    ci, state, postal = m.groups()
    care_start = after("Care Offerings")
    care_end = next((i for i in range(care_start, len(lines))
                     if lines[i].casefold() in ("administrator", "phone", "address", "back to all communities")), len(lines))
    care = "; ".join(lines[care_start:care_end])
    if not care:
        raise ValueError(f"Missing care offering on {url}")
    return dict(zip(FIELDS, [heading.get_text(" ", strip=True), street, ci, state, postal, care, url]))


def scrape_all(session=None):
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "Mozilla/5.0 (BellhavenLocationAudit/1.0)")
    def fetch(url):
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    # Homepage advertises Findlay even though this new site is absent from the paginated directory.
    links = detail_links(fetch(BASE + "/"), BASE + "/")
    for page in range(1, 11):
        url = BASE + "/communities" + (f"?page={page}" if page > 1 else "")
        html = fetch(url)
        found = detail_links(html, url)
        if not found:
            break
        count = len(links)
        links.update(found)
        # Directory currently has 3 pages. Stop at the last page when indicated.
        soup = BeautifulSoup(html, "html.parser")
        if not any("next" in a.get_text(" ", strip=True).lower() for a in soup.select("a[href]")):
            break
    if not links:
        raise RuntimeError("No facilities discovered. Do not overwrite a valid CSV.")
    records = []
    failures = []
    for url in sorted(links):
        try:
            records.append(parse_detail(fetch(url), url))
        except (ValueError, requests.RequestException) as exc:
            failures.append(f"{url}: {exc}")
    if failures:
        raise RuntimeError("Incomplete scrape (existing file left intact):\n" + "\n".join(failures))
    records.sort(key=lambda x: x["name"].lower())
    return records


def save_csv(rows, destination):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "bellhaven_locations.csv"
    locations = scrape_all()
    save_csv(locations, target)
    print(f"Scraped {len(locations)} unique facilities into {target}")
