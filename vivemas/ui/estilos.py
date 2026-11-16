# -*- coding: utf-8 -*-
"""Estilos CSS para ergonomía táctil en campo, modo oscuro y modo nocturno 'Luz Baja'."""

import textwrap
import streamlit as st


def aplicar_estilos_tema(tema: str = "oscuro"):
    """Inyecta CSS responsivo optimizado para pantallas táctiles y entornos de baja luz."""
    
    # 1. Reglas base ergonómicas
    css_base_rules = """
div.stButton > button, div.stDownloadButton > button {
    min-height: 48px !important;
    font-size: 16px !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    touch-action: manipulation !important;
}

input, select, textarea, div[data-baseweb="select"] {
    font-size: 16px !important;
    min-height: 46px !important;
}

.stNumberInput input {
    text-align: center !important;
    font-weight: 700 !important;
    font-size: 18px !important;
}

.top-status-bar {
    padding: 8px 14px;
    margin-bottom: 12px;
    border-radius: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    font-size: 14px;
    font-weight: 500;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
}

@media (max-width: 768px) {
    .block-container {
        padding-top: 1rem !important;
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }
    .top-status-bar {
        flex-direction: column;
        gap: 6px;
        align-items: flex-start;
    }
}
"""

    # 2. Paletas por tema
    if tema == "luz_baja":
        css_tema_rules = """
.stApp {
    background-color: #080303 !important;
    color: #ffb74d !important;
}
p, span, label, h1, h2, h3, h4, h5, h6, .stMarkdown {
    color: #ffa726 !important;
}
div.stButton > button {
    background-color: #2b0b0b !important;
    color: #ff8a65 !important;
    border: 1px solid #d32f2f !important;
}
div.stButton > button:hover {
    background-color: #d32f2f !important;
    color: #ffffff !important;
}
input, select, textarea, div[data-baseweb="select"] {
    background-color: #1a0808 !important;
    color: #ffcc80 !important;
    border-color: #b71c1c !important;
}
.top-status-bar {
    background-color: #1e0909 !important;
    color: #ff8a80 !important;
    border: 1px solid #c62828 !important;
}
div[data-testid="stSidebar"] {
    background-color: #0c0404 !important;
    border-right: 1px solid #b71c1c !important;
}
"""
    elif tema == "alto_contraste":
        css_tema_rules = """
.stApp {
    background-color: #000000 !important;
    color: #ffffff !important;
}
p, span, label, h1, h2, h3, h4, h5, h6, .stMarkdown {
    color: #ffffff !important;
    font-weight: 600 !important;
}
div.stButton > button {
    background-color: #ffffff !important;
    color: #000000 !important;
    border: 2px solid #ffffff !important;
    font-weight: 800 !important;
}
.top-status-bar {
    background-color: #111111 !important;
    color: #ffffff !important;
    border: 2px solid #ffffff !important;
}
div[data-testid="stSidebar"] {
    background-color: #0a0a0a !important;
    border-right: 2px solid #ffffff !important;
}
"""
    else:
        # Modo oscuro estándar
        css_tema_rules = """
.top-status-bar {
    background-color: #1a2332;
    color: #e2e8f0;
    border: 1px solid #334155;
}
"""

    html_completo = f"<style>\n{css_base_rules.strip()}\n{css_tema_rules.strip()}\n</style>"
    if hasattr(st, "html"):
        st.html(html_completo)
    else:
        st.markdown(html_completo, unsafe_allow_html=True)

