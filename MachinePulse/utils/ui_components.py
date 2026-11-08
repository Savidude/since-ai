import base64
from pathlib import Path
import streamlit as st


def setup_page(title, icon="🏭"):
    st.set_page_config(
        page_title=f"MachinePulse | {title}",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded"
    )

    st.markdown(
        """
        <style>
        :root {
            --pg-navy: #10233f;
            --pg-blue: #1769e0;
            --pg-text: #10213d;
            --pg-muted: #64748b;
            --pg-border: #cbd5e1;
            --pg-bg: #f4f7fb;
        }

        .stApp {
            background: var(--pg-bg);
            color: var(--pg-text);
        }

        .block-container {
            padding-top: 1.35rem;
            padding-bottom: 2.2rem;
            max-width: 1800px;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #10213a 0%, #142b49 100%);
            border-right: 1px solid #243b59;
        }

        [data-testid="stSidebar"] * {
            color: #e8f0fb;
        }

        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            color: #e8f0fb;
        }

        [data-testid="stSidebar"] hr {
            border-color: #344963;
        }

        [data-testid="stSidebar"] [data-testid="stAlert"] {
            border-radius: 10px;
        }

        h1, h2, h3, h4 {
            color: var(--pg-text);
            letter-spacing: -0.02em;
        }

        p, label, .stCaption {
            color: #52647b;
        }

        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid var(--pg-border);
            border-radius: 14px;
            padding: 8px 12px;
            box-shadow: 0 2px 8px rgba(15, 35, 65, 0.045);
        }

        [data-testid="stMetricLabel"] {
            color: #52647b;
        }

        [data-testid="stMetricValue"] {
            color: var(--pg-text);
            font-weight: 700;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--pg-border);
            border-radius: 13px;
            background: #ffffff;
        }

        div[class*="st-key-machine-group-"] {
            border: 1px solid #cbd5e1;
            border-radius: 14px;
            background: #ffffff;
            padding: 14px 16px;
            margin-bottom: 14px;
        }

        div[class*="st-key-machine-group-"] [data-testid="stExpander"] {
            background: #f3f7fc;
            border: 1px solid #d5e0ee;
            border-radius: 10px;
        }

        div[class*="st-key-machine-group-"] [data-testid="stExpander"] summary {
            background: #f3f7fc;
            border-radius: 10px;
            color: #18395f;
        }

        div[class*="st-key-pump-card-"] {
            border: 2px solid #aebed0 !important;
            border-radius: 13px;
            background: #ffffff;
            padding: 12px;
        }

        .pg-machine-heading {
            display: flex;
            align-items: center;
            gap: 10px;
            padding-bottom: 10px;
            border-bottom: 1px solid #e2e8f0;
        }

        .pg-machine-kicker {
            color: #1769e0;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.08em;
        }

        .pg-machine-title {
            color: #10213d;
            font-size: 17px;
            font-weight: 750;
        }

        .pg-tree-component {
            display: flex;
            align-items: center;
            gap: 9px;
            margin: 13px 0 5px 10px;
            padding: 7px 10px;
            border-left: 3px solid #1769e0;
            border-radius: 0 8px 8px 0;
            background: #dce7f3;
            color: #18395f;
            font-size: 14px;
            font-weight: 700;
        }

        .pg-tree-branch {
            color: #7b8da4;
            font-family: monospace;
        }

        .stButton > button {
            border-radius: 9px;
            font-weight: 600;
            border-color: #d5dfec;
        }

        [class*="st-key-monitor_"] button,
        button[class*="st-key-monitor_"] {
            color: #15395f !important;
            background: #e8f1ff !important;
            border-color: #bfd1e5 !important;
        }

        [class*="st-key-monitor_"] button:hover,
        button[class*="st-key-monitor_"]:hover {
            color: #10213d !important;
            background: #dceaff !important;
            border-color: #a9bfd9 !important;
        }

        .stButton > button[kind="primary"] {
            background: var(--pg-blue);
            border-color: var(--pg-blue);
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--pg-border);
            border-radius: 10px;
        }

        .pg-hero {
            position: relative;
            overflow: hidden;
            min-height: 205px;
            display: flex;
            align-items: center;
            padding: 28px 34px;
            margin: 0 0 24px 0;
            border-radius: 18px;
            color: #ffffff;
            background-color: #102747;
            background-position: center right;
            background-size: cover;
            box-shadow: 0 8px 24px rgba(13, 35, 65, 0.14);
        }

        .pg-hero:before {
            content: "";
            position: absolute;
            inset: 0;
            background: linear-gradient(
                90deg,
                rgba(8, 27, 53, 0.98) 0%,
                rgba(8, 31, 61, 0.92) 43%,
                rgba(8, 31, 61, 0.32) 100%
            );
        }

        .pg-hero-content {
            position: relative;
            z-index: 1;
            max-width: 720px;
        }

        .pg-hero-kicker {
            display: inline-block;
            background: rgba(9, 112, 75, 0.88);
            color: #e7fff4;
            border: 1px solid rgba(134, 239, 172, 0.25);
            border-radius: 999px;
            padding: 6px 12px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.04em;
            margin-bottom: 12px;
        }

        .pg-hero-title {
            color: #ffffff !important;
            font-size: clamp(30px, 3.1vw, 46px);
            line-height: 1.05;
            font-weight: 800;
            margin: 0 0 8px 0;
        }

        .pg-hero-subtitle {
            color: #d9e7f8 !important;
            font-size: 20px;
            font-weight: 650;
            margin: 0 0 8px 0;
        }

        .pg-hero-description {
            color: #d5e1ef !important;
            font-size: 14px;
            line-height: 1.5;
            margin: 0;
        }

        .pg-status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 11px;
            line-height: 1.2;
            font-weight: 700;
            white-space: nowrap;
        }

        .pg-online {
            color: #087443;
            background: #d9f5e6;
        }

        .pg-offline {
            color: #475569;
            background: #e8edf3;
        }

        .pg-locked {
            color: #b42318;
            background: #fee4e2;
        }

        .pg-unknown {
            color: #9a6700;
            background: #fff1cc;
        }

        .pg-pump-title {
            font-size: 18px;
            font-weight: 750;
            color: #10213d;
        }

        .pg-pump-meta {
            font-size: 12px;
            color: #64748b;
            margin-top: 7px;
        }

        .pg-section-caption {
            color: #64748b;
            font-size: 13px;
            margin-top: -8px;
            margin-bottom: 14px;
        }

        @media (max-width: 700px) {
            .pg-hero {
                min-height: 190px;
                padding: 22px;
                background-position: 62% center;
            }
            .pg-hero-subtitle {
                font-size: 16px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def render_sidebar():
    st.sidebar.markdown(
        """
        <div style="padding: 8px 0 4px 0;">
            <div style="font-size: 23px; font-weight: 800; color: #ffffff;">
                🏭 MachinePulse
            </div>
            <div style="font-size: 11px; color: #b8c8dc; margin-top: 5px;">
                Feel the pulse. Prevent the Failure
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.sidebar.divider()
    st.sidebar.subheader("Navigation")

    st.sidebar.page_link("app.py", label="Overview", icon="🏠")
    st.sidebar.page_link(
        "pages/2_Pump_Monitoring.py",
        label="Pump Monitoring",
        icon="📊"
    )
    st.sidebar.page_link(
        "pages/3_Fault_Diagnosis.py",
        label="Fault Diagnosis",
        icon="🔍"
    )
    st.sidebar.page_link(
        "pages/5_History_Replay.py",
        label="History Replay",
        icon="📅"
    )

    st.sidebar.divider()
    st.sidebar.success("System: Demo Ready")
    st.sidebar.info("Operating Mode: READ ONLY")


def page_header(title, description):
    st.title(title)
    st.caption(description)
    st.divider()


def render_hero_banner(
    title="MachinePulse",
    subtitle="Intelligent Industrial Monitoring",
    description=(
        "Predict equipment failures. Understand machine health. "
        "Support smarter maintenance decisions."
    )
):
    image_path = (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "pumpguard_hero_background.png"
    )

    background_style = ""
    if image_path.exists():
        encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        background_style = (
            "background-image: "
            f"url('data:image/png;base64,{encoded}');"
        )

    st.markdown(
        f"""
        <section class="pg-hero" style="{background_style}">
            <div class="pg-hero-content">
                <div class="pg-hero-kicker">● SYSTEM ONLINE · READ ONLY</div>
                <h1 class="pg-hero-title">{title}</h1>
                <div class="pg-hero-subtitle">{subtitle}</div>
                <p class="pg-hero-description">{description}</p>
            </div>
        </section>
        """,
        unsafe_allow_html=True
    )


def render_status_badge(status):
    status_text = str(status or "UNKNOWN").strip().upper()

    if status_text == "ONLINE":
        css_class = "pg-online"
        label = "● Online"
    elif status_text == "OFFLINE":
        css_class = "pg-offline"
        label = "● Offline"
    elif status_text in {"LOCKED_OUT", "LOCKED OUT"}:
        css_class = "pg-locked"
        label = "● Locked Out"
    else:
        css_class = "pg-unknown"
        label = f"● {status_text.replace('_', ' ').title()}"

    st.markdown(
        f'<span class="pg-status-badge {css_class}">{label}</span>',
        unsafe_allow_html=True
    )
