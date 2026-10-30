"""Shared NagrikSetu branding, user bar, and responsive Streamlit styles."""
from html import escape
import streamlit as st

from .auth import enforce_rbac


def apply_demo_style():
    if st.session_state.get("_nagriksetu_style_rendered"):
        return

    # Injected Head Tags: Security Meta, Open Graph, SEO, and WCAG Viewport
    st.markdown(
        """
        <!-- Security Meta Equivalents -->
        <meta http-equiv="Referrer-Policy" content="strict-origin-when-cross-origin">
        <meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https:;">

        <!-- Accessibility: Viewport Zooming & Scaling Enabled (WCAG 2 AA) -->
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">

        <!-- SEO Metadata -->
        <meta name="description" content="NagrikSetu (NyayaFlow): AI-assisted, multilingual municipal grievance redressal and transparent governance platform for Madhya Pradesh.">
        <link rel="canonical" href="https://nagriksetufinal-nbkvccer5zf9jaz6icatqn.streamlit.app/">

        <!-- Open Graph Meta Tags -->
        <meta property="og:title" content="NagrikSetu - Municipal Grievance Redressal">
        <meta property="og:description" content="Transparent, multilingual civic grievance tracking and explainable ML routing for Madhya Pradesh.">
        <meta property="og:type" content="website">
        <meta property="og:url" content="https://nagriksetufinal-nbkvccer5zf9jaz6icatqn.streamlit.app/">
        <meta property="og:image" content="https://raw.githubusercontent.com/streamlit/brand/master/logo/streamlit-logo-primary-colormark-darktext.png">
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <style>
        :root {
            --ink: #172B23;
            --muted: #45564E;           /* Contrast ratio > 6.4:1 against background (WCAG AA PASS) */
            --navy: #174C3C;
            --green: #2B624D;
            --blue: #2B624D;
            --teal: #1F6B48;
            --mint: #E6F2EB;
            --sky: #EEF1EF;
            --saffron: #945F00;         /* Contrast ratio > 4.8:1 against background (WCAG AA PASS) */
            --warning-background: #FFF5DF;
            --danger: #B42318;
            --danger-background: #FDECEC;
            --success: #1F6B48;
            --success-background: #E6F4EC;
            --neutral-background: #EEF1EF;
            --line: #CBD6CF;
            --paper: #FFFFFF;
            --font-ui: Inter, "Noto Sans Devanagari", "Segoe UI", system-ui, sans-serif;
            --hero-gradient: linear-gradient(120deg, #174C3C 0%, #2B624D 58%, #1F6B48 100%);
        }
        html, body { font-family: var(--font-ui); }
        html { font-size: 16px; }
        .stApp {
            background: #F7F8F6;
            color: var(--ink);
        }
        header[data-testid="stHeader"] { background: transparent; }

        /* Landmark container for screen readers */
        .block-container {
            role: main;
            aria-label: "NagrikSetu Operations";
            max-width: 1220px;
            padding: 1.25rem 1.5rem 3rem;
        }

        h1, h2, h3, h4 { color: var(--ink); letter-spacing: -.025em; }
        h1 { font-size: clamp(1.9rem, 4vw, 3rem); line-height: 1.12; }
        h2 { font-size: clamp(1.45rem, 3vw, 2rem); }
        p, li { color: var(--ink); line-height: 1.65; }

        /* High contrast for caption and metric labels */
        [data-testid="stCaptionContainer"] p { color: var(--muted) !important; font-weight: 500; }
        [data-testid="stMetric"] {
            background: var(--paper); border: 1px solid var(--line); border-radius: 16px;
            padding: 14px 18px; box-shadow: 0 5px 18px rgba(23,43,35,.05);
        }
        [data-testid="stMetricLabel"] { color: var(--muted) !important; font-weight: 600; }
        [data-testid="stMetricValue"] { color: var(--ink) !important; }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            background: var(--paper); border: 1px solid var(--line) !important;
            border-radius: 18px !important; box-shadow: 0 7px 22px rgba(16,37,59,.055);
            transition: transform .18s ease, box-shadow .18s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div:hover {
            transform: translateY(-2px); box-shadow: 0 12px 26px rgba(23,43,35,.09);
        }
        .stButton > button, .stPageLink > a, [data-testid="stFormSubmitButton"] button {
            min-height: 44px; padding: .55rem 1rem; border-radius: 12px !important;
            border: 1px solid var(--line) !important; font-weight: 650;
            background: var(--paper) !important; color: var(--navy) !important;
            font-family: var(--font-ui) !important;
            box-shadow: 0 3px 10px rgba(23,76,60,.07);
            transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease,
                filter .18s ease;
        }
        @media (hover: hover) and (pointer: fine) {
            .stButton > button:hover:not(:disabled), .stPageLink > a:hover,
            [data-testid="stFormSubmitButton"] button:hover:not(:disabled),
            [data-testid="stDownloadButton"] button:hover:not(:disabled),
            [data-testid="stPageNav"] a:hover {
                transform: translateY(-2px); border-color: var(--teal) !important;
                box-shadow: 0 0 0 3px rgba(31,107,72,.12),
                    0 0 22px rgba(31,107,72,.28), 0 8px 20px rgba(23,76,60,.16);
                filter: saturate(1.08);
            }
            [data-testid="stTextInputRootElement"]:hover,
            [data-testid="stTextAreaRootElement"]:hover,
            [data-testid="stSelectbox"] div:has(> [role="combobox"]):hover {
                border-color: rgba(31,107,72,.72) !important;
                box-shadow: 0 0 0 3px rgba(31,107,72,.10), 0 0 18px rgba(31,107,72,.16);
                transition: box-shadow .18s ease, border-color .18s ease;
            }
            [data-testid="stExpander"] summary:hover,
            [data-testid="stTabs"] button:hover,
            [role="radio"]:hover, [role="checkbox"]:hover,
            [role="option"]:hover {
                filter: drop-shadow(0 0 7px rgba(31,107,72,.24));
                transition: filter .18s ease;
            }
        }
        .stButton > button:active:not(:disabled),
        [data-testid="stFormSubmitButton"] button:active:not(:disabled),
        [data-testid="stDownloadButton"] button:active:not(:disabled) {
            transform: translateY(0) scale(.985);
            box-shadow: 0 0 0 2px rgba(31,107,72,.16), 0 3px 10px rgba(23,76,60,.12);
        }
        .stButton > button[kind="primary"],
        button[kind*="primary"],
        [data-testid="stBaseButton-primary"],
        [data-testid="stFormSubmitButton"],
        [data-testid="stFormSubmitButton"] button[kind="primary"],
        [data-testid="stFormSubmitButton"] [data-testid="stBaseButton-primary"],
        [data-testid="stFormSubmitButton"] button,
        button[data-testid="stFormSubmitButton"],
        .stFormSubmitButton button,
        form button[type="submit"],
        [data-testid="stFormSubmitButton"] button:not(:disabled),
        [data-testid="stBaseButton-primary"] {
            background: linear-gradient(120deg, #174C3C 0%, #2B624D 58%, #1F6B48 100%) !important;
            background-color: #2B624D !important; border: 0 !important; color: #fff !important;
            -webkit-text-fill-color: #fff !important;
        }
        .stButton > button[kind="primary"] *,
        button[kind*="primary"] *,
        [data-testid="stBaseButton-primary"] *,
        [data-testid="stFormSubmitButton"] *,
        [data-testid="stFormSubmitButton"] button[kind="primary"] *,
        [data-testid="stFormSubmitButton"] [data-testid="stBaseButton-primary"] * { color: #fff !important; }
        button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible,
        [role="combobox"]:focus-visible {
            outline: 3px solid #1F6B48 !important; outline-offset: 3px !important;
            box-shadow: 0 0 0 2px #fff !important;
        }
        [data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"],
        [data-testid="stSelectbox"] div:has(> [role="combobox"]) {
            background: var(--paper) !important; border: 1px solid var(--line) !important;
            border-radius: 12px !important;
        }
        input, textarea, [role="combobox"] { color: var(--ink) !important; font-family: var(--font-ui); }
        input::placeholder, textarea::placeholder { color: var(--muted) !important; opacity: 1; }
        [data-testid="stIconMaterial"], .material-symbols-rounded, .material-icons {
            font-family: "Material Symbols Rounded", "Material Icons" !important;
            font-feature-settings: "liga" !important;
        }
        button[aria-label="Show password"], button[aria-label="Hide password"],
        [data-testid="stPasswordInput"] button {
            min-width: 2.75rem !important; overflow: hidden; color: transparent !important;
            -webkit-text-fill-color: transparent !important; font-size: 0 !important;
        }
        button[aria-label="Show password"]::after,
        button[aria-label="Hide password"]::after,
        [data-testid="stPasswordInput"] button::after {
            content: "◉"; color: var(--navy); -webkit-text-fill-color: var(--navy);
            font-size: 1rem; line-height: 1;
        }
        [data-testid="stFormSubmitButton"] button *,
        button[data-testid="stFormSubmitButton"] *,
        .stFormSubmitButton button *, form button[type="submit"] * {
            color: #fff !important; -webkit-text-fill-color: #fff !important;
        }
        [data-testid="stTextInputRootElement"]:has(input[type="password"]) [data-testid="stIconMaterial"] {
            font-size: 0 !important; line-height: 1 !important;
        }
        [data-testid="stTextInputRootElement"]:has(input[type="password"]) [data-testid="stIconMaterial"]::after {
            content: "◉"; font: 1.05rem/1 var(--font-ui); color: var(--navy);
        }
        [data-testid="stAlert"] { border-radius: 14px; }
        [data-testid="stAlert"] [data-testid="stMarkdownContainer"] { color: var(--ink); }
        [data-testid="stTabs"] button { min-height: 44px; font-weight: 650; color: var(--navy); }
        .nf-userbar {
            display: flex; align-items: center; gap: .55rem; color: var(--ink);
            font-size: .94rem; font-weight: 650; white-space: nowrap;
        }
        .nf-wordmark { color: var(--navy); font-size: 1.15rem; font-weight: 800; letter-spacing: -.03em; }
        .nf-role-pill {
            display: inline-flex; align-items: center; min-height: 28px; padding: 2px 11px;
            border-radius: 999px; background: var(--mint); color: var(--navy); font-size: .78rem;
            font-weight: 750; border: 1px solid var(--line);
        }
        .nf-hero {
            position: relative; overflow: hidden; border-radius: 22px; padding: clamp(24px, 4vw, 42px);
            color: #fff; background: var(--hero-gradient); box-shadow: 0 16px 36px rgba(23,76,60,.16);
            margin: 4px 0 24px;
        }
        .nf-hero:after {
            content: ''; position: absolute; width: 220px; height: 220px; right: -70px; top: -120px;
            border: 1px solid rgba(255,255,255,.2); border-radius: 50%;
            box-shadow: 0 0 0 28px rgba(255,255,255,.045), 0 0 0 58px rgba(255,255,255,.03);
        }
        .nf-hero h1 { color: #fff !important; font-size: clamp(2rem, 5vw, 3.2rem); margin: 0 0 12px; }
        .nf-hero p { color: #fff !important; max-width: 850px; margin: 0; font-size: 1.04rem; }
        .nf-eyebrow { color: #E6F2EB; text-transform: uppercase; letter-spacing: .14em;
            font-size: .75rem; font-weight: 800; margin-bottom: 12px; }
        .nf-section-kicker { color: var(--blue); text-transform: uppercase; letter-spacing: .13em;
            font-size: .72rem; font-weight: 800; margin-bottom: 6px; }
        .nf-score { font-size: 1.65rem; font-weight: 800; color: var(--blue); line-height: 1.1; }
        .nf-muted { color: var(--muted); }
        [data-testid="stPageNav"] { background: transparent; gap: .3rem; }
        [data-testid="stPageNav"] a {
            min-height: 44px; padding: .45rem .85rem; border: 1px solid transparent;
            border-radius: 12px; font-weight: 700; color: var(--navy);
            transition: background .18s ease, color .18s ease, box-shadow .18s ease,
                border-color .18s ease, transform .18s ease;
        }
        [data-testid="stPageNav"] a:hover {
            background: linear-gradient(115deg, rgba(230,242,235,.96), rgba(255,255,255,.94));
            border-color: rgba(31,107,72,.28); color: var(--navy);
            box-shadow: 0 0 0 3px rgba(31,107,72,.08), 0 7px 18px rgba(23,76,60,.12);
            transform: translateY(-1px);
        }
        [data-testid="stPageNav"] a[aria-current="page"] {
            background: linear-gradient(115deg, #174C3C 0%, #1F6B48 100%);
            border-color: rgba(23,76,60,.8); color: #fff !important;
            box-shadow: 0 6px 16px rgba(23,76,60,.22);
        }
        [data-testid="stPageNav"] a[aria-current="page"] * { color: #fff !important; }
        .st-key-resident-complaint-total [data-testid="stMetric"] {
            background: linear-gradient(130deg, #FFFFFF 0%, #F1F8F3 100%);
            border-color: rgba(31,107,72,.22);
            box-shadow: 0 12px 28px rgba(23,76,60,.09);
        }
        .st-key-resident-complaint-total [data-testid="stMetricValue"] {
            color: var(--green); font-weight: 800;
        }
        [data-testid="stSidebarNav"] a[aria-current="page"] {
            background: var(--mint); color: var(--green); border-left: 3px solid var(--green);
        }
        [data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid var(--line); }
        [data-testid="stExpander"] { background: var(--paper); border: 1px solid var(--line); border-radius: 14px; }
        @media (max-width: 700px) {
            .block-container { padding: .75rem .85rem 2rem; }
            [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: .8rem; }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                min-width: min(100%, 20rem) !important; flex: 1 1 100% !important;
            }
            [data-testid="stPageNav"] { max-width: 100%; overflow-x: visible; flex-wrap: wrap; }
            .nf-hero { border-radius: 18px; }
            .nf-userbar { white-space: normal; }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.session_state["_nagriksetu_style_rendered"] = True


def render_userbar(user: dict | None = None):
    """Render one responsive signed-in bar per Streamlit rerun."""
    if st.session_state.get("_nagriksetu_userbar_rendered"):
        return
    user = user or st.session_state.get("authenticated_user")
    if not user:
        return
    apply_demo_style()
    left, right, signout = st.columns([5, 3.3, 1.2], vertical_alignment="center")
    with left:
        st.markdown('<div class="nf-userbar nf-wordmark">🧭 NagrikSetu</div>', unsafe_allow_html=True)
    with right:
        specific_role = (user.get("role") or "public").title()
        role = escape("Public" if user.get("role") == "public" else "Government")
        role_accessible = escape("Public account" if user.get("role") == "public"
                                 else f"Government account, {specific_role} role")
        display_name = escape(str(user.get("name", "User")))
        st.markdown(
            f'<div class="nf-userbar">{display_name} '
            f'<span class="nf-role-pill" aria-label="{role_accessible}">{role}</span></div>', unsafe_allow_html=True)
    with signout:
        if st.button("Sign out", key="nagriksetu_signout", width="stretch"):
            from .auth import logout_session
            logout_session(st.session_state)
            try:
                if getattr(st.user, "is_logged_in", False):
                    st.logout()
            except Exception:
                pass
            st.rerun()
    st.divider()
    from .auth import demo_mode
    if demo_mode():
        st.warning("DEMO MODE · Synthetic accounts and simulated verification codes. No email or SMS is sent.",
                   icon="🧪")
    st.session_state["_nagriksetu_userbar_rendered"] = True


def render_hero(eyebrow: str, title: str, description: str):
    eyebrow, title, description = (escape(str(value)) for value in (eyebrow, title, description))
    st.markdown(
        f"""
        <section class="nf-hero" role="region" aria-label="{title}">
          <div class="nf-eyebrow">{eyebrow}</div>
          <h1>{title}</h1>
          <p>{description}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def require_permission(action: str):
    """Stop a page when no signed-in account has the required permission."""
    from .auth import authorize_user_action, refresh_session
    user = refresh_session(st.session_state)
    if not user:
        apply_demo_style()
        render_hero("Staff access", "Sign in to continue",
                    "This section is for authorized staff. Sign in from the NagrikSetu overview to continue.")
        st.info("This staff page requires a signed-in account. Return to NagrikSetu to sign in or create an account.")
        st.page_link("pages/0_Home.py", label="Go to NagrikSetu sign in", icon="🔐")
        st.stop()
    try:
        user = authorize_user_action(user["id"], action)
    except PermissionError:
        apply_demo_style()
        render_hero("Staff access", "This page isn't available to your account",
                    f"Your {user.get('role', 'current')} role does not include this action.")
        st.page_link("pages/0_Home.py", label="Return to NagrikSetu", icon="🏠")
        st.stop()
    render_userbar(user)
    return user