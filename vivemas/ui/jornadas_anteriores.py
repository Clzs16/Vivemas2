# -*- coding: utf-8 -*-
"""Pestaña 4: Carga y gestión de jornadas médicas anteriores."""

import json
import hashlib
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import streamlit as st
import pandas as pd

from vivemas.db import (
    obtener_conexion_bd,
    cargar_registros_historicos,
    guardar_config,
    obtener_config,
)
from vivemas.exportes import df_a_excel_bytes, leer_archivo_tabular_unificado
from vivemas.sync_firebase import conectar_firestore
from vivemas.ui.componentes import paginar_dataframe

logger = logging.getLogger("vivemas.ui.jornadas_anteriores")


def _a_entero(val: Any, default: int = 0) -> int:
    try:
        if val is None or pd.isna(val):
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default


def uuid_fila(fila: Dict[str, Any]) -> str:
    """Genera un UUID determinista a partir de los datos clave de la fila para evitar duplicados en la nube."""
    cadena = f"{fila.get('fecha')}_{fila.get('zona')}_{fila.get('paciente_id')}_{fila.get('diagnostico')}_{fila.get('farmaco_entregado')}_{fila.get('cantidad_entregada')}_{fila.get('archivo_origen')}"
    return hashlib.md5(cadena.encode('utf-8')).hexdigest()


def limpiar_fila_historica(
    row: pd.Series,
    default_zona: str = "La Guaira",
    default_jornada: str = "Jornada Histórica",
    default_fecha: Optional[str] = None
) -> Dict[str, Any]:
    """Normaliza una fila de un DataFrame histórico importado desde Excel/CSV."""
    def safe(val, default):
        try:
            if pd.isna(val) or str(val).strip() in ("", "nan", "None", "NaT"):
                return default
        except (TypeError, ValueError):
            pass
        return val

    p_id = safe(row.get("paciente_id", row.get("Paciente", "Desconocido")), "Desconocido")
    edad = safe(row.get("edad", row.get("Edad", 0)), 0)
    diag = safe(row.get("diagnostico", row.get("Diagnostico", "N/A")), "N/A")
    farm = safe(row.get("farmaco_entregado", row.get("Farmaco", "N/A")), "N/A")
    cant = safe(row.get("cantidad_entregada", row.get("Cantidad", 1)), 1)

    fecha_def = default_fecha if default_fecha else datetime.now().strftime("%Y-%m-%d")
    fecha = safe(row.get("fecha", row.get("Fecha", fecha_def)), fecha_def)
    zona = safe(row.get("zona", row.get("Zona", default_zona)), default_zona)
    origen = str(row.get("archivo_origen", "excel"))
    nom_j = safe(row.get("nombre_jornada", row.get("Jornada", default_jornada)), default_jornada)

    try:
        fecha_txt = pd.to_datetime(fecha).strftime("%Y-%m-%d")
    except Exception:
        fecha_txt = str(fecha)

    return {
        "paciente_id": str(p_id),
        "edad": _a_entero(edad, 0),
        "diagnostico": str(diag),
        "farmaco_entregado": str(farm),
        "cantidad_entregada": _a_entero(cant, 1),
        "fecha": fecha_txt,
        "zona": str(zona),
        "archivo_origen": origen,
        "nombre_jornada": str(nom_j),
        "tipo_registro": "historico_excel"
    }


def render_tab_jornadas_anteriores():
    """Renderiza la pestaña de Gestión de Jornadas Anteriores."""
    st.subheader("📁 Carga y Gestión de Jornadas Anteriores")
    st.markdown("Sube aquí tus archivos Excel o CSV de jornadas pasadas para agregarlos al sistema, guardarlos localmente o subirlos a la nube con su respectiva clasificación.")

    archivos_historicos = st.file_uploader("Subir archivos Excel/CSV históricos", type=["xlsx", "xls", "csv"], accept_multiple_files=True, key="upload_hist_tab")

    if archivos_historicos:
        lista_dfs = []
        for arch in archivos_historicos:
            df_h, err_h = leer_archivo_tabular_unificado(arch)
            if not df_h.empty:
                df_h["archivo_origen"] = arch.name
                lista_dfs.append(df_h)
            elif err_h:
                st.warning(f"No se pudo leer {arch.name}: {err_h}")

        if lista_dfs:
            df_historico_acumulado = pd.concat(lista_dfs, ignore_index=True)
            st.session_state["df_historico"] = df_historico_acumulado
            st.success(f"¡{len(archivos_historicos)} archivo(s) cargado(s) en memoria ({len(df_historico_acumulado)} filas)!")

            st.markdown("#### 🏷️ Clasificación de la Jornada Pasada")
            col_hp1, col_hp2 = st.columns(2)
            with col_hp1:
                opciones_zona_hist = st.session_state["lista_zonas"] + ["➕ Agregar nuevo lugar..."]
                zona_hist_sel = st.selectbox("📍 Lugar / Zona de esta Jornada Pasada:", options=opciones_zona_hist, key="sel_zona_hist")
                if zona_hist_sel == "➕ Agregar nuevo lugar...":
                    nuevo_lugar_hist = st.text_input("✍️ Escribe el nombre del lugar:", key="inp_nuevo_lugar_hist")
                    lugar_hist_final = nuevo_lugar_hist.strip() if nuevo_lugar_hist.strip() else "Lugar Desconocido"
                    if nuevo_lugar_hist.strip() and nuevo_lugar_hist.strip() not in st.session_state["lista_zonas"]:
                        st.session_state["lista_zonas"].append(nuevo_lugar_hist.strip())
                        guardar_config("lista_zonas", json.dumps(st.session_state["lista_zonas"]))
                else:
                    lugar_hist_final = zona_hist_sel
            with col_hp2:
                fecha_hist_val = st.date_input("📅 Fecha de realización de la Jornada:", value=datetime.today(), key="date_jornada_hist")

            fecha_hist_str = fecha_hist_val.strftime("%Y-%m-%d")
            nombre_jornada_hist = f"Jornada - {lugar_hist_final} - {fecha_hist_str}"

            st.info(f"🏷️ **Identificador:** `{nombre_jornada_hist}` &nbsp;•&nbsp; *Lugar: **{lugar_hist_final}** | Fecha: **{fecha_hist_str}** | Tipo: **Histórico***")

            st.dataframe(df_historico_acumulado, use_container_width=True)

            col_hb1, col_hb2 = st.columns(2)
            with col_hb1:
                if st.button("💾 Guardar Registros Históricos Localmente", use_container_width=True):
                    try:
                        filas_h = [limpiar_fila_historica(r, default_zona=lugar_hist_final, default_jornada=nombre_jornada_hist, default_fecha=fecha_hist_str) for _, r in df_historico_acumulado.iterrows()]
                        with obtener_conexion_bd() as conn_l:
                            antes = conn_l.total_changes
                            conn_l.executemany("""
                                INSERT OR IGNORE INTO registros_historicos
                                (fecha, zona, paciente_id, edad, diagnostico, farmaco_entregado, cantidad_entregada, archivo_origen, nombre_jornada, tipo_registro)
                                VALUES (:fecha, :zona, :paciente_id, :edad, :diagnostico, :farmaco_entregado, :cantidad_entregada, :archivo_origen, :nombre_jornada, :tipo_registro)
                            """, filas_h)
                            nuevos = conn_l.total_changes - antes
                        cargar_registros_historicos.clear()
                        st.success(f"¡{nuevos} registros guardados localmente bajo '{nombre_jornada_hist}'! ({len(filas_h) - nuevos} duplicados omitidos)")
                    except Exception as err_loc:
                        st.error(f"Error al guardar: {err_loc}")

            with col_hb2:
                if st.button("☁️ Subir Registros Históricos a Firebase", use_container_width=True):
                    with st.spinner(f"Subiendo registros a Firebase bajo '{nombre_jornada_hist}'..."):
                        try:
                            db_cloud_h = conectar_firestore()
                            if not db_cloud_h:
                                st.error("No se encontró el archivo de credenciales de Firebase.")
                            else:
                                coleccion_ref = db_cloud_h.collection("registros_jornada")
                                batch = db_cloud_h.batch()
                                subidos_nube = 0
                                lote_pendientes = 0

                                for _, row in df_historico_acumulado.iterrows():
                                    fila = limpiar_fila_historica(row, default_zona=lugar_hist_final, default_jornada=nombre_jornada_hist, default_fecha=fecha_hist_str)
                                    data_h = {
                                        "fecha": fila["fecha"],
                                        "zona": fila["zona"],
                                        "paciente_id": fila["paciente_id"],
                                        "edad": fila["edad"],
                                        "diagnostico": fila["diagnostico"],
                                        "farmaco_entregado": fila["farmaco_entregado"],
                                        "cantidad_entregada": fila["cantidad_entregada"],
                                        "tipo_registro": "historico_excel",
                                        "nombre_jornada": fila["nombre_jornada"],
                                        "archivo_origen": fila["archivo_origen"],
                                        "sincronizado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    }
                                    nuevo_doc = coleccion_ref.document(uuid_fila(fila))
                                    batch.set(nuevo_doc, data_h)
                                    subidos_nube += 1
                                    lote_pendientes += 1

                                    if lote_pendientes >= 400:
                                        batch.commit(timeout=10)
                                        batch = db_cloud_h.batch()
                                        lote_pendientes = 0

                                if lote_pendientes > 0:
                                    batch.commit(timeout=10)

                                st.success(f"¡{subidos_nube} registros subidos a Firebase clasificados bajo '{nombre_jornada_hist}'!")
                        except Exception as err_nube:
                            st.error(f"Error en Firebase: {err_nube}")
    else:
        st.info("👆 Sube archivos en formato Excel o CSV para comenzar a gestionar jornadas anteriores.")

    # Visualizador de registros históricos guardados localmente
    df_hist_local = cargar_registros_historicos()
    if not df_hist_local.empty:
        st.divider()
        st.markdown(f"### 📚 Archivo Histórico Local ({len(df_hist_local)} registros)")
        df_hist_pag = paginar_dataframe(df_hist_local, clave_paginacion="hist_local", filas_por_defecto=15)
        st.dataframe(df_hist_pag, use_container_width=True, hide_index=True)

        excel_hist_bytes = df_a_excel_bytes(df_hist_local, nombre_hoja_defecto="Historico_Jornadas")
        st.download_button(
            label="📥 Descargar Archivo Histórico Completo en Excel",
            data=excel_hist_bytes,
            file_name=f"Historico_Jornadas_ViveMas_{datetime.now():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_dl_historico_excel",
            use_container_width=True
        )
