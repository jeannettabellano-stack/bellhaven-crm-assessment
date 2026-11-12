# Bellhaven Senior Living — CRM Reconciliation

Local Streamlit assessment implementation. This is a new, corrected project built from the user's existing CSVs and the public Bellhaven website. It is **not** a copy of a hosted app. No credentials are bundled.

## What's inside

- `scraper.py` follows the paginated community directory **and homepage links** to include newly advertised Findlay, parses facility detail pages, and stores name, address, city, state, ZIP, and care offerings.
- `bellhaven_locations.csv` is a verified 35-facility starting snapshot, repairing five split street/city fields, filling care offerings, and including Kettering.
- `review_engine.py` compares geography before names, checks ambiguous candidates, identifies CRM-only records and possible duplicates, and builds cautious change proposals.
- `app.py` shows matching evidence, lets a reviewer approve or reject, connects to the authenticated CRM, and **writes only after explicit approval**.
- `crm_client.py` implements GET/POST/PATCH and the protected CHOW workflow.
- `data/review.sqlite` is created automatically for persistent decisions and CHOW recovery. **Keep this folder when updating the app.**
- `run_daily.py` and `daily_schedule.cron` document the read-only daily pipeline.

## Run on Windows — one-time setup

1. **Back up** your existing Bellhaven-Assessment folder. Extract this ZIP into a **new** folder, e.g. `Desktop\Bellhaven-Assessment-Complete`. Keep all files together.
2. Open that extracted folder, click the address bar, type `cmd`, then Enter.
3. **Easy option:** double-click `START_HERE.bat` to create a Python environment, install requirements, and launch the app automatically. If you prefer manual commands, create its own environment: `py -m venv .venv` (if `py` is unavailable, use `python -m venv .venv`).
4. **Manual option only:** Install: `.venv\Scripts\python.exe -m pip install -r requirements.txt`
5. **Manual option only:** Start: `.venv\Scripts\python.exe -m streamlit run app.py`
6. Open the browser's Streamlit app. The bundled CSVs permit **offline review**, but **cannot** update CRM.
7. To make real changes: enter your **personal CRM token into the password input in the sidebar**, click **Connect / refresh LIVE CRM**, select a proposal, examine evidence and alternatives, check the confirmation box, and click **Approve & apply selected action to CRM**. Rejecting does not write.
8. Confirm every approved change using the assessment's read-only CRM browser. Export the findings and decision history CSVs for the writeup.

Do **not** paste your personal token into code, this ZIP, screenshots, or your submission. You previously shared it in a screenshot; rotate it if the assessment provides that option.

## Important safeguards and assumptions

- The original site scraper found 34 locations, but **Kettering** was missed; the directory lists 34, and homepage-promoted **Findlay** adds a 35th facility. Check the site if the dataset changes.
- The CRM export includes 121 records, including the corporate parent and unrelated operator accounts.
- Geographic matching outweighs name similarity; multiple plausible accounts (notably Kettering) require a human choice.
- When **both** `lifetime_revenue > 0` and `outstanding_ar > 0` and an account needs a new parent, **do not change its parent or other fields**. Create a fresh Bellhaven-owned facility and set **only** `chow_current_account` on the old account.
- Otherwise, an approved reparent PATCH may update the existing account.
- A reviewer-approved duplicate update sets `duplicate_of_account`, `status=Inactive`, and explanatory `note`, never merges/deletes.
- CRM-only records are potential inactivations that must be reviewed individually; website absence alone is not proof of closure.
- Rejections, reviewed no-ops, and successful approvals are written to local SQLite and skipped on subsequent runs. After a partially successful CHOW create, the new ID is journaled before attempting to link the old account to avoid duplicate creation on retry.
- `run_daily.py` is **read-only** with respect to CRM. Daily cron is illustrative, not preinstalled. If deployed, keep the SQLite file persistent (e.g. a local scheduled job, not an ephemeral runner).
- The OpenAPI spec advertises POST and PATCH but omits body schemas; field names are based on the CRM CSV and the assessment's conventions. **Test a low-risk approved change first and verify it in the CRM browser.** Do not claim production API integration is validated until that succeeds.
- If the CRM data changes between snapshot and approval, the app stops the operation and asks for a refresh.

## Demo / submission checklist

1. Load 35 website locations and the live CRM into the local app.
2. Review a few evidence rows, including a wrong parent, an outdated name, or a missing CRM facility.
3. **Reject** one proposal and **approve/apply** a different safe proposal.
4. Show the changed record in the assessment's read-only CRM browser.
5. Show `daily_schedule.cron`, the SQLite decision history, and CHOW handling.
6. Record a ~2-minute narrated screen demo; share via a viewable link according to the assessment's instructions.
7. Explain the 35-location discrepancy, ambiguous match handling, CRM-only records, duplicate decisions, and protected parent moves in the writeup.

## Daily manual test

From the project folder:

`.venv\Scripts\python.exe run_daily.py`

This refreshes the public sites and a pending-proposal snapshot. It does **not** automatically approve any record.
