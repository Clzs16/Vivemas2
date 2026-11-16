# -*- coding: utf-8 -*-
"""Componentes visuales reutilizables: barra superior de conectividad, paginación y avisos."""

import math
import streamlit as st
import pandas as pd

from vivemas.sync_firebase import hay_conexion_internet, contar_pendientes_sincronizacion


def render_barra_superior_estado():
    """Renderiza la barra superior de estado de conectividad, cola de sincronización y respaldo."""
    modo_offline = st.session_state.get("modo_offline", False)
    tiene_internet = not modo_offline and hay_conexion_internet()

    # Consultar pendientes de sincronización
    n_nuevos, n_dels = contar_pendientes_sincronizacion()
    total_pendientes = n_nuevos + n_dels

    operador = st.session_state.get("operador_activo", "Operador")
    rol = st.session_state.get("rol_activo", "campo")
    rol_badge = "👑 Coord." if rol == "coordinador" else "🩺 Campo"
    zona = st.session_state.get("zona_activa", "La Guaira")
    ultimo_respaldo = st.session_state.get("ultimo_respaldo", "Reciente")

    # Indicador de red
    if modo_offline:
        red_html = "<span style='color: #f59e0b;'>📴 <b>Modo Sin Internet (Local)</b></span>"
    elif tiene_internet:
        red_html = "<span style='color: #10b981;'>🌐 <b>En Línea</b></span>"
    else:
        red_html = "<span style='color: #ef4444;'>📡 <b>Sin Conexión Nube</b></span>"

    # Indicador de cola
    if total_pendientes > 0:
        sync_html = f"<span style='color: #f97316;'>⏳ <b>{total_pendientes}</b> pendientes de subir</span>"
    else:
        sync_html = "<span style='color: #10b981;'>☁️ Al día con la nube</span>"

    html = f"""
    <div class="top-status-bar">
        <div>
            {red_html} &nbsp;|&nbsp; {sync_html}
        </div>
        <div>
            💾 Respaldo: <i>{ultimo_respaldo}</i> &nbsp;|&nbsp; 📍 {zona} &nbsp;|&nbsp; <b>{operador}</b> ({rol_badge})
        </div>
    </div>
    """
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)


def paginar_dataframe(
    df: pd.DataFrame,
    clave_paginacion: str = "pag",
    filas_por_defecto: int = 15
) -> pd.DataFrame:
    """Implementa paginación compacta y navegación táctil anterior/siguiente para tablas."""
    if df is None or df.empty:
        return df

    total_filas = len(df)
    if total_filas <= filas_por_defecto:
        return df

    total_paginas = math.ceil(total_filas / filas_por_defecto)

    # Estado de la página actual
    key_page = f"pagina_{clave_paginacion}"
    if key_page not in st.session_state:
        st.session_state[key_page] = 1

    # Controles de paginación
    col_izq, col_centro, col_der = st.columns([1, 2, 1])
    with col_izq:
        if st.button("⬅️ Anterior", key=f"btn_prev_{clave_paginacion}", use_container_width=True, disabled=st.session_state[key_page] <= 1):
            st.session_state[key_page] -= 1
            st.rerun()

    with col_centro:
        st.markdown(
            f"<div style='text-align: center; padding-top: 10px; font-weight: 600;'>"
            f"Página {st.session_state[key_page]} de {total_paginas} ({total_filas} registros)"
            f"</div>",
            unsafe_allow_html=True
        )

    with col_der:
        if st.button("Siguiente ➡️", key=f"btn_next_{clave_paginacion}", use_container_width=True, disabled=st.session_state[key_page] >= total_paginas):
            st.session_state[key_page] += 1
            st.rerun()

    inicio = (st.session_state[key_page] - 1) * filas_por_defecto
    fin = inicio + filas_por_defecto
    return df.iloc[inicio:fin]
