"""Authenticated Bellhaven CRM adapter with approval-only writes and CHOW safeguards."""
import json
from datetime import datetime, timezone
import requests
from review_engine import (audit_marker, fields_for_site, money, norm, root_id, s,
                           store_decision, reviewed_keys)

API = "https://analyst-assessment-production.up.railway.app/api/v1/accounts"


class CRMClient:
    def __init__(self, token, session=None):
        if not s(token):
            raise ValueError("Enter your personal CRM token on your computer.")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + token.strip(),
                                     "Accept": "application/json"})

    def _call(self, method, path="", payload=None, params=None):
        response = self.session.request(method, API + path, json=payload, params=params, timeout=35)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            # Do not echo auth headers or token back to the UI.
            raise RuntimeError(f"CRM {method} returned HTTP {response.status_code}: {response.text[:500]}") from e
        try:
            return response.json()
        except ValueError as e:
            raise RuntimeError("CRM returned a response that wasn't JSON.") from e

    def list_accounts(self):
        found, seen = [], set()
        for page in range(1, 101):
            data = self._call("GET", params={"page": page, "page_size": 50})
            records = data.get("data", []) if isinstance(data, dict) else data
            if not isinstance(records, list):
                raise RuntimeError("Unexpected CRM accounts list format")
            fresh = 0
            for item in records:
                if not isinstance(item, dict):
                    continue
                uid = s(item.get("account_id"))
                if uid and uid not in seen:
                    seen.add(uid)
                    found.append(item)
                    fresh += 1
            if not records or len(records) < 50 or fresh == 0:
                break
        return found

    def get_account(self, uid):
        data = self._call("GET", "/" + s(uid))
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        if isinstance(data, dict):
            return data
        raise RuntimeError("Unexpected CRM account detail format")

    def create_account(self, payload):
        data = self._call("POST", payload=payload)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict):
            raise RuntimeError("POST returned an unexpected format. Check CRM before retrying, to avoid duplicates.")
        new_id = s(data.get("account_id") or data.get("id"))
        if not new_id:
            raise RuntimeError("POST succeeded but returned no account_id. Inspect CRM before retrying.")
        return new_id

    def update_account(self, uid, fields):
        if not uid or not fields:
            raise ValueError("Cannot update CRM without an account ID and fields")
        return self._call("PATCH", "/" + s(uid), payload=fields)


def _assert_unchanged(snapshot, live, fields):
    for field in fields:
        if s(snapshot.get(field)) != s(live.get(field)):
            raise RuntimeError(f"CRM account changed since review ({field}). Refresh the CRM and re-review before applying.")


def _note(old, addition):
    current = s(old.get("note"))
    return (current + "\n" if current else "") + addition


def _existing_with_marker(client, marker):
    records = [a for a in client.list_accounts() if marker in s(a.get("note"))]
    if len(records) > 1:
        raise RuntimeError("Several CRM records share the audit marker. Investigate before proceeding.")
    return s(records[0]["account_id"]) if records else ""


def apply_approved(client, conn, proposal, parent_id):
    """Call ONLY from a reviewer's explicit Approve click. No background writes.

    Decisions are persisted only after API success. CHOW creates are journalled
    before linking, so a failed link can be retried without creating a second copy.
    """
    key, action = proposal["key"], proposal["action"]
    if key in reviewed_keys(conn):
        raise RuntimeError("This proposal has already been decided. No repeat writes were made.")
    if action == "investigate":
        raise RuntimeError("Select the correct CRM candidate first; never auto-approve an ambiguous match.")
    if action == "none":
        store_decision(conn, key, "Reviewed — no change", "No field updates necessary", proposal)
        return "Reviewed; no CRM change needed."
    if s(parent_id) != root_id(client.list_accounts()):
        raise RuntimeError("Bellhaven parent ID changed. Stop and refresh before writing.")
    marker = audit_marker(key)
    url = s((proposal.get("site") or {}).get("source_url"))
    account = proposal.get("account")
    if action in ("patch", "chow", "inactivate", "duplicate"):
        if not account or not s(account.get("account_id")):
            raise RuntimeError("Proposal has no CRM account ID")
        live = client.get_account(account["account_id"])
        _assert_unchanged(account, live, ("name", "parent_id", "lifetime_revenue", "outstanding_ar", "status"))
    if action == "create":
        found = _existing_with_marker(client, marker)
        if not found:
            payload = dict(proposal["changes"])
            payload["note"] = f"Created after reviewer approval. Website: {url}. {marker}"
            payload["created_by_candidate"] = True
            found = client.create_account(payload)
        store_decision(conn, key, "Applied", f"Created CRM account {found}", {"new_id": found, "proposal": proposal})
        return f"Created account {found}."
    if action == "chow":
        # Only applies if the CURRENT old account has BOTH revenue and AR > 0.
        if s(live.get("parent_id")) == s(parent_id):
            raise RuntimeError("The original account is already under Bellhaven; refresh the proposal.")
        if not (money(live.get("lifetime_revenue")) > 0 and money(live.get("outstanding_ar")) > 0):
            raise RuntimeError("CHOW protection conditions changed. Refresh and re-review.")
        if s(live.get("chow_current_account")):
            raise RuntimeError("Old account already has a CHOW successor. Investigate; no write performed.")
        journal = conn.execute("SELECT new_account_id FROM partial_creates WHERE proposal_key=?", (key,)).fetchone()
        new_id = journal[0] if journal else _existing_with_marker(client, marker)
        if not new_id:
            payload = dict(proposal["changes"])
            payload["parent_id"] = parent_id
            payload["note"] = f"New Bellhaven-owned account for CHOW. Old account: {live['account_id']}. {url}. {marker}"
            payload["created_by_candidate"] = True
            new_id = client.create_account(payload)
        # Record the new ID before the separate PATCH, even if PATCH fails.
        conn.execute("INSERT OR REPLACE INTO partial_creates VALUES (?,?,?)",
                     (key, new_id, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        # SOP: ONLY this link changes on the OLD account. No parent, name, status, or note patch.
        client.update_account(live["account_id"], {"chow_current_account": new_id})
        store_decision(conn, key, "Applied", f"CHOW: new {new_id}; old {live['account_id']} linked; old account otherwise unchanged", {"new_id": new_id, "proposal": proposal})
        conn.execute("DELETE FROM partial_creates WHERE proposal_key=?", (key,))
        conn.commit()
        return f"CHOW completed: new {new_id}; old {live['account_id']} linked without reparenting."
    if action == "patch":
        patch = dict(proposal["changes"])
        if "parent_id" in patch and s(live.get("parent_id")) != s(patch["parent_id"]):
            if money(live.get("lifetime_revenue")) > 0 and money(live.get("outstanding_ar")) > 0:
                raise RuntimeError("Protected revenue AND A/R detected: cannot reparent. Refresh; CHOW required.")
        patch["note"] = _note(live, f"Bellhaven website audit verified: {url}. {marker}")
        client.update_account(live["account_id"], patch)
        store_decision(conn, key, "Applied", "Patched " + ", ".join(patch), proposal)
        return f"Updated CRM account {live['account_id']}: {', '.join(patch)}."
    if action == "inactivate":
        if s(live.get("parent_id")) != s(parent_id):
            raise RuntimeError("Record is no longer owned by Bellhaven; no inactivation performed.")
        client.update_account(live["account_id"], {
            "status": "Inactive",
            "note": _note(live, f"Reviewer-approved: absent from current Bellhaven website directory. {marker}")})
        store_decision(conn, key, "Applied", "Marked CRM-only account Inactive", proposal)
        return f"Marked {live['account_id']} Inactive."
    if action == "duplicate":
        survivor = proposal.get("survivor") or {}
        if not s(survivor.get("account_id")) or s(survivor.get("account_id")) == s(live.get("account_id")):
            raise RuntimeError("Invalid duplicate survivor")
        client.get_account(survivor["account_id"])  # Verify the surviving record exists.
        client.update_account(live["account_id"], {
            "duplicate_of_account": s(survivor["account_id"]), "status": "Inactive",
            "note": _note(live, f"Reviewer-confirmed duplicate of {survivor['account_id']}; no merge/delete. {marker}")})
        store_decision(conn, key, "Applied", f"Linked losing duplicate to {survivor['account_id']}", proposal)
        return f"Marked duplicate {live['account_id']} Inactive, linked to {survivor['account_id']}."
    raise RuntimeError("Unknown action; no write made")
