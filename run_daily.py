"""Daily read-only discovery. Rejected/applied items remain suppressed in SQLite."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from crm_client import CRMClient
from review_engine import build_proposals, clean_websites, open_db, reviewed_keys
from scraper import scrape_all, save_csv

ROOT = Path(__file__).resolve().parent
CSV = ROOT / "bellhaven_locations.csv"
CRM = ROOT / "bellhaven_crm_accounts.csv"
DB = ROOT / "data" / "review.sqlite"


def main():
    try:
        websites = scrape_all()
        save_csv(websites, CSV)
        print(f"Public website scrape succeeded: {len(websites)} facilities")
    except Exception as exc:
        print(f"Website scrape failed; not overwriting the most recent verified CSV: {exc}")
        websites = clean_websites(pd.read_csv(CSV, dtype=str).fillna("").to_dict("records"))
    token = os.environ.get("BELLHAVEN_CRM_TOKEN", "")
    if token:
        accounts = CRMClient(token).list_accounts()
        print(f"Live CRM fetched: {len(accounts)} accounts")
    else:
        accounts = pd.read_csv(CRM, dtype=str).fillna("").to_dict("records")
        print("Using local CRM CSV snapshot; BELLHAVEN_CRM_TOKEN is not set")
    proposals, _ = build_proposals(websites, accounts)
    conn = open_db(DB)
    decided = reviewed_keys(conn)
    queue = [p for p in proposals if p["action"] != "none" and p["key"] not in decided]
    # The snapshot contains NO tokens. It is replaced atomically each day.
    output = ROOT / "data" / "pending_proposals.json"
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(),
                                "website_count": len(websites), "crm_count": len(accounts),
                                "pending": queue}, indent=2), encoding="utf-8")
    temp.replace(output)
    print(f"Pending new/undecided proposals: {len(queue)}; decisions preserved: {len(decided)}")
    print("No CRM POST or PATCH calls were made.")


if __name__ == "__main__":
    main()
