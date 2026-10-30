import streamlit as st

from core.auth import ROLES
from core.security import attachment_encryption_status, tls_status
from core.ui import apply_demo_style, render_hero, require_permission

user = require_permission("can_view_security")
apply_demo_style()
render_hero("Operational readiness", "Feature & security status",
            "See the implemented workflow paths and verify which security controls are configured for this deployment.")

photos = attachment_encryption_status()
tls = tls_status(st.get_option("server.sslCertFile"), st.get_option("server.sslKeyFile"))
left, right = st.columns(2)
left.metric("Photo encryption", photos["label"])
right.metric("TLS", tls["label"])

st.subheader("Selected feature paths")
features = [
    ("Speech-to-text", "SARVAM · KEY REQUIRED", "Short recordings are sent to Sarvam Saaras v4 for transcription. Residents can review and edit the transcript; NagrikSetu does not retain the audio."),
    ("Photo attachment", "INCLUDED", "JPEG, PNG, and WEBP evidence is size/type-checked, metadata-stripped, AES-GCM encrypted at rest, and visible to government staff only. Upload is disabled until a key is configured."),
    ("Hotspot detection", "INCLUDED · SYNTHETIC", "Counts compare the selected time window with an equal baseline period. Results are labelled synthetic when based on demo records."),
    ("Citizen-side challenge", "INCLUDED", "A signed-in resident can request review only for a complaint submitted from their account. Government staff resolve it; the request does not change routing."),
    ("Role-based access", "INCLUDED", "Public and government roles are loaded from server-side account records. Page guards enforce access; UI controls alone are not security boundaries."),
    ("Correction-driven model improvement", "PHASED · OFFLINE", "Corrections require reviewer verification before offline experiments. The live model is not retrained or updated automatically."),
]
for name, state, detail in features:
    with st.container(border=True):
        st.markdown(f"**{name}** · `{state}`")
        st.caption(detail)

st.subheader("Deployment checks")
st.warning("Complaint and decision data live in SQLite without application-level database encryption. Use a verified encrypted storage volume or managed encrypted database before storing real data.")
if not tls["configured"]:
    st.warning("TLS is not verified by this app. Terminate HTTPS at a trusted hosting layer and confirm the external configuration before deployment.")
else:
    st.success("Streamlit certificate and key files are present. Verify HTTPS at the public endpoint and the renewal process separately.")
st.caption("Permissions are enforced by the app service layer: " +
           ", ".join(k.removeprefix("can_") for k, enabled in ROLES[user["role"]].items() if enabled) + ".")

st.subheader("Open a workflow")
cols = st.columns(4)
for col, page, label, icon in zip(cols,
        ["pages/1_Intake.py", "pages/2_Review.py", "pages/3_Challenge.py", "pages/4_Evaluation.py"],
        ["Complaint intake", "Human review", "Challenge resolution", "Hotspots & evaluation"],
        ["📝", "🔎", "⚖️", "📊"]):
    col.page_link(page, label=label, icon=icon, width="stretch")
