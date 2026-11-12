import streamlit as st
import pandas as pd
from matching import reconcile
st.set_page_config(page_title="Bellhaven CRM Review",layout="wide")
st.title("Bellhaven CRM Review")
st.caption("Local, read-only matching and approval simulation. This app does NOT write to the live CRM.")
st.warning("Review matches manually. Never automatically change account ownership, especially where revenue or A/R exists.")
crm_upload=st.file_uploader("CRM accounts CSV (optional; bundled sample used by default)",type="csv",key="crm")
website_upload=st.file_uploader("Website locations CSV (required)",type="csv",key="website")
crm=pd.read_csv(crm_upload if crm_upload is not None else "bellhaven_crm_accounts.csv",dtype=str).fillna("")
st.metric("CRM accounts loaded",len(crm))
if website_upload is None:
    st.info("Upload bellhaven_locations.csv from your earlier scraper or Google Sheets export to begin matching.")
    st.stop()
locations=pd.read_csv(website_upload,dtype=str).fillna("")
st.metric("Website locations loaded",len(locations))
if "review" not in st.session_state or st.session_state.get("website_filename")!=website_upload.name:
    st.session_state.review=reconcile(locations,crm)
    st.session_state.website_filename=website_upload.name
st.write("Review proposed matches, change decisions, and download the result. No changes are sent to the CRM.")
edited=st.data_editor(st.session_state.review,hide_index=True,use_container_width=True,
    column_config={"decision":st.column_config.SelectboxColumn("Decision",options=["Pending","Approve proposal","Reject proposal","Investigate"],required=True)},
    num_rows="fixed")
st.session_state.review=edited
st.download_button("Download reviewed matches CSV",edited.to_csv(index=False).encode("utf-8"),"bellhaven_reviewed_matches.csv","text/csv")
st.subheader("CRM-only records")
matched=set(edited["crm_account_id"].dropna().astype(str))
unmatched=crm[~crm["account_id"].astype(str).isin(matched)]
st.dataframe(unmatched,use_container_width=True)
st.download_button("Download unmatched CRM accounts",unmatched.to_csv(index=False).encode("utf-8"),"bellhaven_unmatched_crm.csv","text/csv")
