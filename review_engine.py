"""Pure reconciliation and proposal logic. Never writes to the CRM."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

BASE = "https://analyst-assessment-production.up.railway.app"
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"


def s(value):
    return "" if value is None else str(value).strip()


def norm(value):
    value = s(value).lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    replacements = {
        "street":"st", "road":"rd", "avenue":"ave", "drive":"dr", "lane":"ln",
        "boulevard":"blvd", "place":"pl", "north":"n", "south":"s", "east":"e",
        "west":"w", "northwest":"nw", "northeast":"ne", "southwest":"sw",
        "southeast":"se", "parkway":"pkwy", "highway":"hwy", "center":"centre",
    }
    return " ".join(replacements.get(x, x) for x in value.split())


def zipcode(value):
    found = re.search(r"\d{5}", s(value))
    return found.group() if found else ""


def money(value):
    try:
        return float(s(value).replace(",", "") or "0")
    except ValueError:
        return 0.0


def website_key(site):
    return "site:" + (s(site.get("source_url")) or norm(site.get("name")))


def account_key(account):
    return "crm_only:" + s(account.get("account_id"))


def care_for_crm(site):
    value = s(site.get("care_offerings")).lower()
    result = []
    if "assisted living" in value:
        result.append("Assisted Living")
    if "memory support" in value or "memory care" in value:
        result.append("Memory Care")
    if "nursing" in value or "rehabilitation" in value:
        result.append("Skilled Nursing")
    if "independent living" in value:
        result.append("Independent Living")
    return "; ".join(result) or s(site.get("care_offerings"))


def root_id(accounts):
    options = [a for a in accounts if s(a.get("name")) == BELLHAVEN_PARENT_NAME]
    if len(options) != 1:
        raise ValueError("Cannot uniquely identify Bellhaven's parent account. Stop and investigate.")
    return s(options[0]["account_id"])


def repair_site(site):
    site = dict(site)
    for prefix, ending in [("St ", "St"), ("Rd ", "Rd"), ("Lane ", "Lane"), ("W ", "W")]:
        if site.get("city", "").startswith(prefix):
            site["address"] = (s(site.get("address")) + " " + ending).strip()
            site["city"] = site["city"][len(prefix):]
            break
    return {k: s(v) for k, v in site.items()}


def clean_websites(records):
    sites = [repair_site(x) for x in records if s(x.get("name"))]
    ket = {
        "name": "Bellhaven of Kettering", "address": "3313 Wilmington Pike", "city": "Kettering",
        "state": "OH", "zip": "45429", "care_offerings": "Short-Term Rehabilitation & Nursing",
        "source_url": BASE + "/communities/bellhaven-of-kettering",
    }
    if not any(norm(x.get("name")) == norm(ket["name"]) for x in sites):
        sites.append(ket)
    dedup = {}
    for x in sites:
        key = x.get("source_url") or norm(x["name"])
        dedup[key] = x
    return sorted(dedup.values(), key=lambda x: x["name"].lower())


def geo_score(site, acc):
    name = SequenceMatcher(None, norm(site.get("name")), norm(acc.get("name"))).ratio()
    street = SequenceMatcher(None, norm(site.get("address")), norm(acc.get("billing_street"))).ratio()
    city = norm(site.get("city")) == norm(acc.get("billing_city"))
    state = norm(site.get("state")) == norm(acc.get("billing_state"))
    postal = zipcode(site.get("zip")) == zipcode(acc.get("billing_zip"))
    exact_street = norm(site.get("address")) == norm(acc.get("billing_street"))
    if exact_street and city and state and postal:
        return 100, "Same street, city, state, and ZIP"
    if city and state and postal and street >= 0.87:
        return 92, "Same city/state/ZIP and similar street spelling"
    if city and state and postal and name >= 0.90:
        return 85, "Same city/state/ZIP and closely matching name; check street"
    if city and state and name >= 0.91:
        return 72, "Same city/state and name; address requires verification"
    if city and state and postal and street >= 0.64 and name >= 0.6:
        return 70, "Possible name/street match in the same city and ZIP"
    return 0, ""


def candidates_for(site, accounts):
    options = []
    for account in accounts:
        if not s(account.get("billing_city")):
            continue
        score, explanation = geo_score(site, account)
        if score >= 70:
            options.append({"account": account, "score": score, "reason": explanation})
    options.sort(key=lambda r: (-r["score"], s(r["account"].get("name"))))
    return options


def fields_for_site(site):
    return {
        "name": s(site["name"]), "billing_street": s(site["address"]),
        "billing_city": s(site["city"]), "billing_state": s(site["state"]),
        "billing_zip": zipcode(site["zip"]), "care_type": care_for_crm(site),
    }


def build_site_proposal(site, account, parent, alternatives=None, score=0, evidence=""):
    key = website_key(site)
    result = dict(key=key, kind="website", site=site, account=account,
                  alternatives=alternatives or [], score=score, evidence=evidence)
    if account is None:
        result.update(action="create", classification="Missing in CRM",
                      changes={**fields_for_site(site), "parent_id": parent, "status": "Active"},
                      summary="Create new facility under Bellhaven (review for alternate CRM identities)")
        return result
    changes = {}
    desired = fields_for_site(site)
    # Patch only actual discrepancies; don't rewrite formatting-only variations.
    for col, value in desired.items():
        actual = s(account.get(col))
        if col == "billing_zip":
            differs = zipcode(actual) != zipcode(value)
        elif col == "care_type":
            differs = set(norm(x) for x in actual.split(";")) != set(norm(x) for x in value.split(";"))
        else:
            differs = norm(actual) != norm(value)
        if differs:
            changes[col] = value
    if s(account.get("status")) != "Active":
        changes["status"] = "Active"
    parent_changed = s(account.get("parent_id")) != parent
    if parent_changed:
        if money(account.get("lifetime_revenue")) > 0 and money(account.get("outstanding_ar")) > 0:
            result.update(action="chow", classification="Needs fix — protected ownership",
                          changes={**desired, "parent_id": parent, "status": "Active"},
                          summary="CHOW: create NEW Bellhaven account, then link old.chow_current_account; preserve all other old fields")
            return result
        changes["parent_id"] = parent
    if changes:
        result.update(action="patch", classification="Needs fix",
                      changes=changes, summary="Update verified CRM fields after approval")
    else:
        result.update(action="none", classification="Confident match",
                      changes={}, summary="No change needed")
    return result


def build_proposals(websites, accounts):
    parent = root_id(accounts)
    found = []
    candidate_linked = set()
    for site in clean_websites(websites):
        options = candidates_for(site, accounts)
        if options:
            best = options[0]["score"]
            # All close, high-scoring accounts must be reviewed, not silently selected.
            close = [x for x in options if x["score"] >= max(70, best - 10)]
            candidate_linked.update(s(x["account"].get("account_id")) for x in options)
            if len(close) > 1 or best < 80:
                found.append({"key": website_key(site), "kind": "website", "site": site,
                              "account": None, "alternatives": close,
                              "action": "investigate", "classification": "Review candidates",
                              "changes": {}, "score": best,
                              "evidence": "Multiple plausible accounts or an uncertain address/name. Select the correct record before approval.",
                              "summary": "Manual account selection required"})
            else:
                found.append(build_site_proposal(site, options[0]["account"], parent,
                                                 alternatives=options, score=best, evidence=options[0]["reason"]))
        else:
            found.append(build_site_proposal(site, None, parent, evidence="No sufficiently similar CRM account"))
    # Accounts currently owned by Bellhaven but not associated with the current web footprint.
    for account in accounts:
        aid = s(account.get("account_id"))
        if s(account.get("parent_id")) != parent or aid in candidate_linked:
            continue
        if s(account.get("status")) == "Inactive":
            continue
        found.append({"key": account_key(account), "kind": "crm_only", "site": None,
                      "account": account, "alternatives": [], "action": "inactivate",
                      "classification": "CRM only / not on site", "changes": {"status": "Inactive"},
                      "score": 0, "evidence": "CRM parent = Bellhaven; no community page with a plausible matching location.",
                      "summary": "Review potential closure/ownership change before marking Inactive"})
    # Same name, locality, ZIP, and equivalent street under the Bellhaven parent: review duplicate.
    grouped = {}
    for account in accounts:
        if s(account.get("parent_id")) != parent or not s(account.get("billing_street")):
            continue
        if s(account.get("status")) == "Inactive" or s(account.get("duplicate_of_account")):
            continue
        signature = (norm(account.get("name")), norm(account.get("billing_street")),
                     norm(account.get("billing_city")), zipcode(account.get("billing_zip")))
        grouped.setdefault(signature, []).append(account)
    for group in grouped.values():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda x: (-money(x.get("lifetime_revenue")),
                                               -money(x.get("outstanding_ar")),
                                               s(x.get("account_id"))))
        survivor = group[0]
        for losing in group[1:]:
            found.append({"key": "duplicate:" + s(losing["account_id"]),
                          "kind": "duplicate", "site": None, "account": losing,
                          "survivor": survivor, "alternatives": [], "action": "duplicate",
                          "classification": "Possible duplicate", "score": 100,
                          "changes": {"duplicate_of_account": s(survivor["account_id"]), "status": "Inactive"},
                          "evidence": "Same normalized name, address, city, and ZIP as survivor: " + s(survivor["account_id"]),
                          "summary": "Mark losing copy Inactive and set duplicate_of_account; never merge or delete"})
    return found, parent


def open_db(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
        proposal_key TEXT PRIMARY KEY, decision TEXT NOT NULL, description TEXT,
        payload TEXT, decided_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS partial_creates (
        proposal_key TEXT PRIMARY KEY, new_account_id TEXT NOT NULL, saved_at TEXT NOT NULL)""")
    conn.commit()
    return conn


def reviewed_keys(conn):
    return {x[0] for x in conn.execute("SELECT proposal_key FROM decisions").fetchall()}


def store_decision(conn, key, decision, description="", payload=None):
    if decision not in ("Rejected", "Applied", "Reviewed — no change"):
        raise ValueError("Unsupported decision")
    conn.execute("INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?)",
                 (key, decision, description, json.dumps(payload or {}, default=str),
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()


def status_rows(conn):
    return conn.execute("SELECT proposal_key, decision, description, decided_at FROM decisions ORDER BY decided_at DESC").fetchall()


def audit_marker(key):
    return "BH-AUDIT-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:14]
