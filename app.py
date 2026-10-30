"""NagrikSetu sign-in shell and role-aware top navigation."""
import streamlit as st

from core.auth import (LoginLockedError, authenticate_identifier, begin_public_phone_registration,
                       begin_staff_registration, demo_mode, get_active_user, get_user_by_email,
                       oidc_available, register_user, refresh_session, resend_public_phone_otp,
                       resend_staff_email_code, start_session, verify_public_phone_registration,
                       verify_staff_registration, staff_directory_preview)
from core.auth import verify_permission
from core.db import init_db
from core.config import feature_enabled
from core.geo_ui import render_geography_picker
from core.geo import configure_staff_location, sync_allowlisted_assignments
from core.password_policy import score_password
from core.ui import apply_demo_style, render_hero, render_userbar

st.set_page_config(page_title="NagrikSetu | Civic grievance support", page_icon="🧭",
                   layout="wide", initial_sidebar_state="collapsed")
st.session_state["_nagriksetu_userbar_rendered"] = False
st.session_state["_nagriksetu_style_rendered"] = False
init_db()


def _sync_oidc_account():
    try:
        oidc_user = st.user
        if not getattr(oidc_user, "is_logged_in", False):
            return
        profile = oidc_user.to_dict()
        email = profile.get("email", "")
        account = get_user_by_email(email)
        if account is None:
            st.session_state["google_pending_profile"] = {
                "email": email, "name": profile.get("name") or email.split("@")[0]
            }
            return
        if account.get("role") != "public" or account.get("auth_provider") != "google":
            # Staff must use their verified email/password account; external sign-in
            # is only accepted for an account originally created through OIDC.
            st.session_state.pop("authenticated_user", None)
            st.session_state.pop("authenticated_user_id", None)
            try:
                st.logout()
            except Exception:
                pass
            return
        start_session(st.session_state, get_active_user(account["id"]))
    except Exception:
        # OIDC is optional in local development; configuration is shown below.
        return


_sync_oidc_account()
cached = st.session_state.get("authenticated_user")
active = refresh_session(st.session_state) if cached else None

user = st.session_state.get("authenticated_user")


def _page(path, title, icon, *, default=False, visibility="visible"):
    return st.Page(path, title=title, icon=icon, default=default, visibility=visibility)


if user and user.get("role") == "public":
    nav_pages = [
        _page("pages/0_Home.py", "Overview", "🏠", default=True),
        _page("pages/10_Public_Portal.py", "Submit & track", "📝"),
        _page("pages/8_Request_Review.py", "My complaints", "🙋"),
        _page("pages/1_Intake.py", "Complain here", "📝", visibility="hidden"),
        _page("pages/6_Inbox.py", "Inbox", "📥", visibility="hidden"),
        _page("pages/2_Review.py", "Review", "🔎", visibility="hidden"),
        _page("pages/9_Duplicates.py", "Duplicates", "🔗", visibility="hidden"),
        _page("pages/3_Challenge.py", "Challenge / request queue", "⚖️", visibility="hidden"),
        _page("pages/11_Request_Review_Queue.py", "Request review queue", "📨", visibility="hidden"),
        _page("pages/7_Replay.py", "Case replay", "🎞️", visibility="hidden"),
        _page("pages/4_Evaluation.py", "Evaluation", "📊", visibility="hidden"),
        _page("pages/5_Relabel.py", "Relabel", "🏷️", visibility="hidden"),
        _page("pages/9_Security.py", "Security status", "🔐", visibility="hidden"),
        _page("pages/12_Area_Access_Admin.py", "Area access requests", "📍", visibility="hidden"),
        _page("pages/13_Hotspots.py", "Hotspots", "📈", visibility="hidden"),
        _page("pages/14_Correction_Dataset.py", "Correction dataset", "🧪", visibility="hidden"),
        _page("pages/15_Model_Evaluation.py", "Model evaluation", "📊", visibility="hidden"),
        _page("pages/16_Users_Roles.py", "Users & roles", "👥", visibility="hidden"),
    ]
    router = st.navigation(nav_pages, position="top")
elif user:
    can_review = verify_permission(user["role"], "can_review")
    can_audit = verify_permission(user["role"], "can_audit")
    router = st.navigation({
        "Complaints": [
            _page("pages/0_Home.py", "Overview", "🏠", default=True),
            _page("pages/1_Intake.py", "Complain here", "📝",
                  visibility="visible" if verify_permission(user["role"], "can_intake") else "hidden"),
            _page("pages/6_Inbox.py", "Inbox", "📥", visibility="visible" if can_audit else "hidden"),
            _page("pages/10_Public_Portal.py", "Submit & track", "📝", visibility="hidden"),
            _page("pages/8_Request_Review.py", "My complaints", "🙋", visibility="hidden"),
        ],
        "Review": [
            _page("pages/2_Review.py", "Review", "🔎", visibility="visible" if can_review else "hidden"),
            _page("pages/9_Duplicates.py", "Duplicates", "🔗", visibility="visible" if can_review else "hidden"),
            _page("pages/3_Challenge.py", "Challenge", "⚖️", visibility="visible" if can_review else "hidden"),
            _page("pages/11_Request_Review_Queue.py", "Request review queue", "📨", visibility="visible" if can_review else "hidden"),
            _page("pages/7_Replay.py", "Case replay", "🎞️", visibility="visible" if can_audit else "hidden"),
        ],
        "Insights": [
            _page("pages/4_Evaluation.py", "Evaluation", "📊", visibility="visible" if can_audit else "hidden"),
            _page("pages/5_Relabel.py", "Relabel", "🏷️", visibility="visible" if can_review else "hidden"),
            _page("pages/9_Security.py", "Security status", "🔐",
                  visibility="visible" if verify_permission(user["role"], "can_view_security") else "hidden"),
            _page("pages/12_Area_Access_Admin.py", "Area access requests", "📍", visibility="hidden"),
            _page("pages/13_Hotspots.py", "Hotspots", "📈",
                  visibility="visible" if feature_enabled("hotspots") and verify_permission(user["role"], "can_view_hotspots") else "hidden"),
            _page("pages/14_Correction_Dataset.py", "Correction dataset", "🧪",
                  visibility="visible" if feature_enabled("learning") and verify_permission(user["role"], "can_manage_corrections") else "hidden"),
            _page("pages/15_Model_Evaluation.py", "Model evaluation", "📊",
                  visibility="visible" if feature_enabled("learning") and verify_permission(user["role"], "can_view_model_eval") else "hidden"),
            _page("pages/16_Users_Roles.py", "Users & roles", "👥",
                  visibility="visible" if verify_permission(user["role"], "can_audit") else "hidden"),
        ],
    }, position="top")
else:
    # Keep the sign-in view clean: no application pages are exposed before login.
    guest_pages = [
        st.Page(lambda: None, title="Sign in", icon="🔐", url_path="sign-in",
                default=True, visibility="hidden"),
        _page("pages/0_Home.py", "Overview", "🏠", visibility="hidden"),
        _page("pages/10_Public_Portal.py", "Submit & track", "📝", visibility="hidden"),
        _page("pages/8_Request_Review.py", "My complaints", "🙋", visibility="hidden"),
        _page("pages/1_Intake.py", "Complain here", "📝", visibility="hidden"),
        _page("pages/6_Inbox.py", "Inbox", "📥", visibility="hidden"),
        _page("pages/2_Review.py", "Review", "🔎", visibility="hidden"),
        _page("pages/9_Duplicates.py", "Duplicates", "🔗", visibility="hidden"),
        _page("pages/3_Challenge.py", "Challenge / request queue", "⚖️", visibility="hidden"),
        _page("pages/11_Request_Review_Queue.py", "Request review queue", "📨", visibility="hidden"),
        _page("pages/7_Replay.py", "Case replay", "🎞️", visibility="hidden"),
        _page("pages/4_Evaluation.py", "Evaluation", "📊", visibility="hidden"),
        _page("pages/5_Relabel.py", "Relabel", "🏷️", visibility="hidden"),
        _page("pages/9_Security.py", "Security status", "🔐", visibility="hidden"),
        _page("pages/12_Area_Access_Admin.py", "Area access requests", "📍", visibility="hidden"),
        _page("pages/13_Hotspots.py", "Hotspots", "📈", visibility="hidden"),
        _page("pages/14_Correction_Dataset.py", "Correction dataset", "🧪", visibility="hidden"),
        _page("pages/15_Model_Evaluation.py", "Model evaluation", "📊", visibility="hidden"),
        _page("pages/16_Users_Roles.py", "Users & roles", "👥", visibility="hidden"),
    ]
    router = st.navigation(guest_pages,
                           position="hidden")


if user:
    apply_demo_style()
    render_userbar(user)
else:
    apply_demo_style()
    render_hero("Civic grievance support", "NagrikSetu",
                "Report a civic problem, follow your case, and request a human review when needed.")
    info_col, form_col = st.columns([1.05, 1], gap="large")
    with info_col:
        st.subheader("A person reviews every suggestion")
        st.write("NagrikSetu helps residents share civic problems. Staff review department suggestions; nothing is closed automatically.")
        st.markdown("- Report in Hindi, English, or Hinglish.\n- Add an optional photo.\n- Track your complaint with its case ID.")
        st.caption("Public users can use email or an Indian mobile number. Staff accounts are verified against the synthetic staff directory.")
        if demo_mode():
            st.warning("DEMO MODE · Verification codes shown here are simulated; no email or SMS is sent.", icon="🧪")
        try:
            if oidc_available(st.secrets):
                st.button("Continue with Google", on_click=st.login, args=("google",), width="stretch")
            else:
                st.caption("Google sign-in is optional. Configure [auth] in .streamlit/secrets.toml to enable it.")
        except Exception:
            st.caption("Google sign-in is optional. Configure [auth] in .streamlit/secrets.toml to enable it.")
    with form_col:
        google_pending = st.session_state.get("google_pending_profile")
        if google_pending:
            st.subheader("Finish public account setup")
            st.caption(f"Google account: {google_pending['email']} · Google sign-in is only available for public accounts.")
            with st.form("google_profile"):
                google_name = st.text_input("Your display name", value=google_pending["name"])
                google_submitted = st.form_submit_button("Continue", type="primary", width="stretch")
            if google_submitted:
                try:
                    account = register_user(google_pending["email"], google_name, None,
                                            requested_role="public", auth_provider="google")
                    start_session(st.session_state, account)
                    st.session_state.pop("google_pending_profile", None)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        else:
            login_tab, register_tab = st.tabs(["Sign in", "Create account"])
            with login_tab:
                with st.form("email_login"):
                    identifier = st.text_input("Email or mobile number", autocomplete="username",
                                               placeholder="name@example.com or 9876543210")
                    password = st.text_input("Password", type="password", autocomplete="current-password")
                    submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
                if submitted:
                    account = None
                    config_error = False
                    try:
                        account = authenticate_identifier(identifier, password)
                    except LoginLockedError:
                        pass
                    except RuntimeError as exc:
                        st.error(str(exc))
                        config_error = True
                    if account:
                        start_session(st.session_state, account)
                        st.rerun()
                    if not config_error:
                        st.error("Invalid credentials")
                st.caption("Password strength guidance: use 10+ characters, 3 character types, or a 4-word passphrase. Existing accounts can still sign in with their current password.")
            with register_tab:
                issued_code = None
                account_kind = st.selectbox("Account type", ["public", "staff"],
                                            format_func=lambda value: "Resident / public user" if value == "public" else "Government staff",
                                            key="registration-account-type")
                if account_kind == "public":
                    public_method = st.radio("Register with", ["Email", "Mobile number"], horizontal=True,
                                             key="public-registration-method")
                    citizen_name = st.text_input("Your name", key="citizen-register-name")
                    citizen_identifier = st.text_input("Email address" if public_method == "Email" else "Indian mobile number",
                                                        key="citizen-register-identifier", autocomplete="email")
                    citizen_password = st.text_input("Create password", type="password", key="citizen-register-password")
                    citizen_confirm = st.text_input("Confirm password", type="password", key="citizen-register-confirm")
                    password_report = score_password(
                        citizen_password, email=citizen_identifier if public_method == "Email" else None,
                        name=citizen_name, phone=citizen_identifier if public_method == "Mobile number" else None)
                    st.progress(password_report["score"] / 4, text=f"Password strength · {password_report['score']}/4")
                    for suggestion in password_report["suggestions"]:
                        st.caption(suggestion)
                    if st.button("Create public account", type="primary", width="stretch", key="create-public-account"):
                        try:
                            if public_method == "Email":
                                account = register_user(citizen_identifier, citizen_name, citizen_password,
                                                        password_confirm=citizen_confirm)
                                start_session(st.session_state, account)
                                st.rerun()
                            else:
                                delivery = begin_public_phone_registration(citizen_identifier, citizen_name,
                                                                           citizen_password, citizen_confirm)
                                st.session_state["phone_registration_pending"] = {
                                    "phone": citizen_identifier, "message": delivery["message"]}
                                issued_code = delivery.get("code")
                                st.success("OTP request created. Verify your number to finish registration.")
                        except (ValueError, RuntimeError) as exc:
                            st.error(str(exc))
                    pending_phone = st.session_state.get("phone_registration_pending")
                    if pending_phone:
                        st.info(pending_phone["message"])
                        if issued_code:
                            st.code(issued_code, language=None)
                        otp = st.text_input("Six-digit SMS OTP", max_chars=6, key="public-phone-otp")
                        verify_col, resend_col = st.columns(2)
                        if verify_col.button("Verify number", key="verify-public-phone"):
                            try:
                                account = verify_public_phone_registration(pending_phone["phone"], otp)
                                st.session_state.pop("phone_registration_pending", None)
                                start_session(st.session_state, account)
                                st.rerun()
                            except (ValueError, RuntimeError) as exc:
                                st.error(str(exc))
                        if resend_col.button("Resend OTP", key="resend-public-phone"):
                            try:
                                delivery = resend_public_phone_otp(pending_phone["phone"])
                                st.session_state["phone_registration_pending"] = {
                                    "phone": pending_phone["phone"], "message": delivery["message"]}
                                issued_code = delivery.get("code")
                            except (ValueError, RuntimeError) as exc:
                                st.error(str(exc))
                else:
                    st.caption("Staff roles and departments come only from the protected directory. An email code verifies this demo registration; it does not prove real employment.")
                    staff_email = st.text_input("Staff email address", key="staff-register-email", autocomplete="email")
                    staff_preview = staff_directory_preview(staff_email)
                    if staff_preview:
                        st.caption(f"Directory record · {staff_preview['full_name']} · {staff_preview['role'].title()} · {staff_preview['department']} · SYNTHETIC")
                    else:
                        st.caption("Enter an active, unregistered address from the synthetic staff directory.")
                    staff_password = st.text_input("Create password", type="password", key="staff-register-password")
                    staff_confirm = st.text_input("Confirm password", type="password", key="staff-register-confirm")
                    staff_report = score_password(
                        staff_password, email=staff_email,
                        name=staff_preview["full_name"] if staff_preview else None)
                    st.progress(staff_report["score"] / 4, text=f"Password strength · {staff_report['score']}/4")
                    for suggestion in staff_report["suggestions"]:
                        st.caption(suggestion)
                    if st.button("Send staff verification code", type="primary", width="stretch", key="start-staff-registration"):
                        try:
                            delivery = begin_staff_registration(staff_email, "", staff_password, staff_confirm)
                            st.session_state["staff_registration_pending"] = {
                                "email": staff_email, "message": delivery["message"]}
                            issued_code = delivery.get("code")
                            st.success("Code request created. Verify your email address to finish registration.")
                        except (ValueError, RuntimeError) as exc:
                            st.error(str(exc))
                    pending_staff = st.session_state.get("staff_registration_pending")
                    if pending_staff:
                        st.info(pending_staff["message"])
                        if issued_code:
                            st.code(issued_code, language=None)
                        staff_code = st.text_input("Six-digit email verification code", max_chars=6,
                                                   key="staff-registration-code")
                        verify_col, resend_col = st.columns(2)
                        if verify_col.button("Verify and create staff account", key="verify-staff-registration"):
                            try:
                                account = verify_staff_registration(pending_staff["email"], staff_code)
                                st.session_state.pop("staff_registration_pending", None)
                                start_session(st.session_state, account)
                                st.rerun()
                            except (ValueError, RuntimeError) as exc:
                                st.error(str(exc))
                        if resend_col.button("Resend code", key="resend-staff-code"):
                            try:
                                delivery = resend_staff_email_code(pending_staff["email"])
                                st.session_state["staff_registration_pending"] = {
                                    "email": pending_staff["email"], "message": delivery["message"]}
                                issued_code = delivery.get("code")
                            except (ValueError, RuntimeError) as exc:
                                st.error(str(exc))
    st.divider()
    st.caption("Prototype authentication. Email and SMS delivery, multifactor authentication, and password reset are not configured.")

router.run()

# --- Non-invasive Floating Contact Widget (Forces Gmail Web) ---
st.markdown("""
<style>
.floating-contact-btn {
    position: fixed;
    bottom: 25px;
    right: 25px;
    background: linear-gradient(135deg, #2563eb, #1d4ed8);
    color: white !important;
    padding: 10px 18px;
    border-radius: 50px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    z-index: 999999;
    font-family: sans-serif;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: transform 0.2s ease;
}
.floating-contact-btn:hover {
    transform: scale(1.05);
    color: white !important;
}
</style>
<a href="https://mail.google.com/mail/?view=cm&fs=1&to=aarohigupta2124@gmail.com&su=NyayaFlow%20Support%20Inquiry" target="_blank" class="floating-contact-btn">
    💬 Contact Us (Gmail)
</a>
""", unsafe_allow_html=True)