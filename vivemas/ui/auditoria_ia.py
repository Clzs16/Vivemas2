# -*- coding: utf-8 -*-
"""Pestaña 5: Auditoría clínica y razonamiento epidemiológico con Google Gemini."""

import uuid
import logging
from typing import Tuple
import streamlit as st
import pandas as pd

from vivemas.config import SS
from vivemas.db import obtener_conexion_bd
from vivemas.textos import _detectar_zona_en_texto, normalizar_texto
from vivemas.diagnosticos import explotar_diagnosticos
from vivemas.ia_gemini import (
    resolver_api_key_gemini,
    obtener_cliente_gemini,
    obtener_modelos_gemini_disponibles,
    abrir_stream_gemini,
    construir_contexto_gemini_agregado,
)
from vivemas.sync_firebase import (
    consultar_registros_firebase,
    obtener_resumen_jornadas_firebase,
    hay_conexion_internet,
)

logger = logging.getLogger("vivemas.ui.auditoria_ia")


def uuid_fila_simple(r) -> str:
    cadena = f"{r.get('fecha')}_{r.get('zona')}_{r.get('paciente_id')}_{r.get('diagnostico')}_{r.get('farmaco_entregado')}_{r.get('cantidad_entregada')}_{r.get('archivo_origen')}"
    import hashlib
    return hashlib.md5(cadena.encode('utf-8')).hexdigest()


def cargar_datos_auditoria_completa(fuente: str = "🌐 Ambos Datos (Nube + Local)", sel_jornada: str = None) -> Tuple[pd.DataFrame, str]:
    """Carga y consolida registros para el auditor IA garantizando integridad y deduplicación."""
    df_local_total = pd.DataFrame()
    try:
        with obtener_conexion_bd() as conn:
            df_jornadas = pd.read_sql("SELECT * FROM registros_jornada", conn)
            df_hist = pd.read_sql("SELECT * FROM registros_historicos", conn)
            partes = []
            if not df_jornadas.empty:
                df_j = df_jornadas.copy()
                df_j["origen_dato"] = "Local (En Vivo)"
                df_j["uuid"] = df_j.apply(
                    lambda r: str(r.get("uuid") or "").strip() or f"vivo_{r.get('id', uuid.uuid4())}",
                    axis=1
                )
                partes.append(df_j)
            if not df_hist.empty:
                df_h = df_hist.copy()
                df_h["origen_dato"] = "Local (Histórico)"
                df_h["uuid"] = df_h.apply(
                    lambda r: str(r.get("uuid") or "").strip() or uuid_fila_simple(r),
                    axis=1
                )
                partes.append(df_h)
            if partes:
                df_local_total = pd.concat(partes, ignore_index=True)
    except Exception as e_loc:
        logger.error(f"Error cargando datos locales para auditoría: {e_loc}", exc_info=True)

    df_nube_total = pd.DataFrame()
    if fuente in ("🌐 Ambos Datos (Nube + Local)", "☁️ Nube de Firebase"):
        try:
            nombre_filtro = None
            if sel_jornada and sel_jornada not in ("🌐 Todas las Jornadas en la Nube", "Todas", "Todas las Jornadas"):
                nombre_filtro = sel_jornada
            lista_docs = consultar_registros_firebase(nombre_jornada=nombre_filtro)
            if lista_docs:
                df_nube_total = pd.DataFrame(lista_docs)
                df_nube_total["origen_dato"] = "Nube (Firebase)"
                if "uuid" in df_nube_total.columns:
                    df_nube_total["uuid"] = df_nube_total["uuid"].fillna("").astype(str)
        except Exception as e_cloud:
            logger.error(f"Error descargando datos de Firebase para auditoría: {e_cloud}", exc_info=True)

    if fuente == "🌐 Ambos Datos (Nube + Local)":
        if not df_nube_total.empty and not df_local_total.empty:
            df_combinado = pd.concat([df_nube_total, df_local_total], ignore_index=True)
            for idx, r in df_combinado.iterrows():
                u = str(r.get("uuid") or "").strip()
                if not u or u.lower() in ("nan", "none"):
                    df_combinado.at[idx, "uuid"] = f"auto_{idx}_{uuid.uuid4()}"
            df_resultado = df_combinado.drop_duplicates(subset=["uuid"], keep="first").copy()
            cant_nube = len(df_nube_total)
            cant_local = len(df_local_total)
            desc_info = f"Ambos Datos (Nube: {cant_nube} | Local: {cant_local} — Consolidado: {len(df_resultado)})"
        elif not df_nube_total.empty:
            df_resultado = df_nube_total
            desc_info = f"Ambos Datos (Solo Nube activa: {len(df_nube_total)} registros)"
        elif not df_local_total.empty:
            df_resultado = df_local_total
            desc_info = f"Ambos Datos (Solo Local activa: {len(df_local_total)} registros)"
        else:
            df_resultado = pd.DataFrame()
            desc_info = "Sin registros disponibles"

    elif fuente == "☁️ Nube de Firebase":
        if not df_nube_total.empty:
            df_resultado = df_nube_total
            etiq = sel_jornada if sel_jornada else "Todas las jornadas"
            desc_info = f"Nube de Firebase ({etiq}: {len(df_nube_total)} registros)"
        else:
            if not df_local_total.empty and "sincronizado" in df_local_total.columns:
                df_sync = df_local_total[df_local_total["sincronizado"] == 1]
                if not df_sync.empty:
                    df_resultado = df_sync
                    desc_info = f"Nube (Caché local sincronizada: {len(df_sync)} registros)"
                else:
                    df_resultado = pd.DataFrame()
                    desc_info = "Nube de Firebase (0 registros o sin conexión)"
            else:
                df_resultado = pd.DataFrame()
                desc_info = "Nube de Firebase (0 registros o sin conexión)"

    elif fuente == "💻 Base de Datos Local":
        df_resultado = df_local_total
        desc_info = f"Base de Datos Local ({len(df_local_total)} registros)"

    return df_resultado, desc_info


def render_tab_auditoria_ia():
    """Renderiza la pestaña del Asistente Epidemiológico Gemini."""
    st.subheader("🧠 Auditoría Inteligente y Razonamiento Clínico con Gemini")
    st.markdown(
        "Asistente clínico impulsado por **Google Gemini** para auditar medicamentos, frecuencias de enfermedades, stock y tendencias. "
        "Permite analizar datos unificados (Nube de Firebase + Local), solo nube o solo base local."
    )

    with st.expander("🎯 Origen de Datos y Filtro de Jornada para la IA", expanded=True):
        col_src1, col_src2 = st.columns(2)
        with col_src1:
            opciones_fuente = [
                "🌐 Ambos Datos (Nube + Local)",
                "☁️ Nube de Firebase",
                "💻 Base de Datos Local"
            ]
            fuente_audit = st.radio("Fuente de Datos a examinar con IA:", opciones_fuente, index=0, horizontal=True, key="rad_fuente_audit")
        with col_src2:
            sel_jornada_audit = None
            if fuente_audit == "☁️ Nube de Firebase":
                lista_jornadas_fb = obtener_resumen_jornadas_firebase()
                opciones_fb = ["🌐 Todas las Jornadas en la Nube"] + [j["etiqueta"] for j in lista_jornadas_fb]
                sel_jornada_audit = st.selectbox("Selecciona la Jornada en la Nube a auditar:", options=opciones_fb, key="sel_jornada_fb_audit")
            elif fuente_audit == "🌐 Ambos Datos (Nube + Local)":
                st.info("🌐 **Modo Unificado Activo:** Combina los registros de la nube de Firebase con las atenciones en vivo e históricas de tu computadora (sin duplicar).")
            else:
                st.caption("💻 *Examinará exclusivamente los registros locales de SQLite (jornadas en vivo e históricas).*")

    clave_estado_audit = f"{fuente_audit}_{sel_jornada_audit}"
    if ("ultima_clave_audit" not in st.session_state or
        st.session_state["ultima_clave_audit"] != clave_estado_audit or
        "df_nube_audit" not in st.session_state or
        st.session_state.get("df_nube_audit") is None):
        df_cargado_auto, origen_auto = cargar_datos_auditoria_completa(fuente_audit, sel_jornada_audit)
        st.session_state["df_nube_audit"] = df_cargado_auto
        st.session_state["auditoria_origen_info"] = origen_auto
        st.session_state["ultima_clave_audit"] = clave_estado_audit

    col_btn1, col_btn2 = st.columns([3, 1])
    with col_btn1:
        if st.button("🔄 Refrescar y Recargar Datos para Gemini", key="btn_recargar_datos_ia"):
            with st.spinner("Actualizando datos..."):
                df_cargado_man, origen_man = cargar_datos_auditoria_completa(fuente_audit, sel_jornada_audit)
                st.session_state["df_nube_audit"] = df_cargado_man
                st.session_state["auditoria_origen_info"] = origen_man
                st.session_state["ultima_clave_audit"] = clave_estado_audit
                st.success(f"¡Datos actualizados! ({origen_man})")
                st.rerun()

    with col_btn2:
        if st.button("🗑️ Limpiar Conversación"):
            st.session_state["messages_nube"] = []
            st.rerun()

    df_actual_audit = st.session_state.get("df_nube_audit", pd.DataFrame())
    origen_actual_txt = st.session_state.get("auditoria_origen_info", "Ambos Datos (Nube + Local)")

    if not df_actual_audit.empty:
        total_p_badge = len(df_actual_audit)
        zonas_disp_badge = len(df_actual_audit["zona"].dropna().unique()) if "zona" in df_actual_audit.columns else 0
        st.success(f"✅ **IA Lista para Auditar:** `{origen_actual_txt}` &nbsp;•&nbsp; **{total_p_badge}** atenciones en **{zonas_disp_badge}** zonas geográficas.")
    else:
        st.warning(f"⚠️ *{origen_actual_txt}*. No hay atenciones registradas aún en esta fuente.")

    if "messages_nube" not in st.session_state:
        st.session_state.messages_nube = []

    for message in st.session_state.messages_nube:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt_usuario := st.chat_input("Escribe tu pregunta clínica o analítica (Ej: ¿Qué patología predomina en La Guaira?)..."):
        st.session_state.messages_nube.append({"role": "user", "content": prompt_usuario})
        with st.chat_message("user"):
            st.markdown(prompt_usuario)

        with st.chat_message("assistant"):
            df_nube_data = st.session_state.get("df_nube_audit", pd.DataFrame())
            if df_nube_data.empty:
                df_nube_data, origen_actual_txt = cargar_datos_auditoria_completa(fuente_audit, sel_jornada_audit)
                st.session_state["df_nube_audit"] = df_nube_data
                st.session_state["auditoria_origen_info"] = origen_actual_txt

            if df_nube_data.empty:
                respuesta = "⚠️ No hay registros médicos disponibles para auditar en la fuente seleccionada."
                st.markdown(respuesta)
                st.session_state.messages_nube.append({"role": "assistant", "content": respuesta})
            else:
                zonas_disp = [str(z).strip() for z in df_nube_data["zona"].dropna().unique().tolist() if str(z).strip()] if "zona" in df_nube_data.columns else []
                zona_m = _detectar_zona_en_texto(zonas_disp, prompt_usuario)

                if zona_m and "zona" in df_nube_data.columns:
                    z_clean_m = normalizar_texto(zona_m)
                    df_analisis = df_nube_data[df_nube_data["zona"].apply(lambda v: normalizar_texto(v) == z_clean_m)]
                else:
                    df_analisis = df_nube_data

                origen_desc = st.session_state.get("auditoria_origen_info", "Consolidado")
                filtro_txt = f"Origen: {origen_desc} | Zona: {zona_m if zona_m else f'Todas ({len(zonas_disp)} zonas)'}"

                contexto_clinico = construir_contexto_gemini_agregado(df_analisis, prompt_usuario, filtro_txt)

                modo_offline = st.session_state.get(SS.MODO_OFFLINE, False)
                api_key_activa = resolver_api_key_gemini()

                # U5: Fallback matemático inmediato sin retrasos si offline o sin clave
                if modo_offline or not hay_conexion_internet() or not api_key_activa:
                    motivo = "Modo Offline activo" if modo_offline else ("Sin conexión a internet" if not hay_conexion_internet() else "Falta clave API de Gemini")
                    st.info(f"📊 **Análisis Matemático Local ({motivo}):**")
                    resp_directa = f"📊 **Resultado del Análisis ({filtro_txt}):**\n\n"
                    if "farmaco_entregado" in df_analisis.columns and "cantidad_entregada" in df_analisis.columns:
                        tf = df_analisis.groupby("farmaco_entregado")["cantidad_entregada"].sum().sort_values(ascending=False).head(8).to_string()
                        resp_directa += f"💊 **Fármacos más entregados:**\n```\n{tf}\n```\n\n"
                    if "diagnostico" in df_analisis.columns:
                        tp = explotar_diagnosticos(df_analisis)["diagnostico"].value_counts().head(8).to_string()
                        resp_directa += f"🩺 **Diagnósticos más frecuentes:**\n```\n{tp}\n```\n"
                    st.markdown(resp_directa)
                    st.session_state.messages_nube.append({"role": "assistant", "content": resp_directa})
                else:
                    try:
                        client_ai = obtener_cliente_gemini(api_key_activa)
                        modelos_audit = obtener_modelos_gemini_disponibles(api_key_activa)
                        response_stream = abrir_stream_gemini(client_ai, contexto_clinico, modelos_audit)

                        if response_stream:
                            def flujo_texto():
                                for chunk in response_stream:
                                    if chunk.text:
                                        yield chunk.text

                            respuesta_final = st.write_stream(flujo_texto())
                            st.session_state.messages_nube.append({"role": "assistant", "content": respuesta_final})
                        else:
                            st.warning("⚠️ *Alta demanda en Google Gemini. Mostrando análisis matemático directo:*")
                            resp_directa = f"📊 **Resultado del Análisis ({filtro_txt}):**\n\n"
                            if "farmaco_entregado" in df_analisis.columns and "cantidad_entregada" in df_analisis.columns:
                                tf = df_analisis.groupby("farmaco_entregado")["cantidad_entregada"].sum().sort_values(ascending=False).head(8).to_string()
                                resp_directa += f"💊 **Fármacos más entregados:**\n```\n{tf}\n```\n\n"
                            if "diagnostico" in df_analisis.columns:
                                tp = explotar_diagnosticos(df_analisis)["diagnostico"].value_counts().head(8).to_string()
                                resp_directa += f"🩺 **Diagnósticos más frecuentes:**\n```\n{tp}\n```\n"
                            st.markdown(resp_directa)
                            st.session_state.messages_nube.append({"role": "assistant", "content": resp_directa})
                    except Exception as err_gemini:
                        st.error(f"❌ Error al consultar a Gemini: {err_gemini}")
