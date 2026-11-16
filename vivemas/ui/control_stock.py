# -*- coding: utf-8 -*-
"""Pestaña 3: Control, auditoría y conciliación de stock de medicamentos."""

import logging
from datetime import datetime
import streamlit as st
import pandas as pd

from vivemas.db import (
    obtener_conexion_bd,
    cargar_stock_donaciones,
    cargar_registros_local,
)
from vivemas.textos import normalizar_nombre_farmaco
from vivemas.exportes import df_a_excel_bytes, leer_archivo_tabular_unificado
from vivemas.ui.componentes import paginar_dataframe

logger = logging.getLogger("vivemas.ui.control_stock")


def render_tab_control_stock():
    """Renderiza la pestaña de Control y Conciliación de Stock."""
    st.subheader("📦 Control, Auditoría y Conciliación de Stock")
    st.markdown(
        "Este módulo concilia automáticamente el **Stock Inicial (Cajas/Donaciones)** con los **Medicamentos Entregados a Pacientes** "
        "y el **Stock Final Físico**, detectando mermas, discrepancias y alertas de escasez."
    )

    df_don_base = cargar_stock_donaciones()
    df_atenciones_locales = cargar_registros_local()

    col_chk1, col_chk2 = st.columns(2)
    with col_chk1:
        usar_don_como_ini = st.checkbox(
            "📦 Cargar Cajas de Donaciones registradas como Stock Inicial",
            value=not df_don_base.empty,
            key="chk_usar_don_ini"
        )
    with col_chk2:
        conciliar_con_atenciones = st.checkbox(
            "📋 Restar automáticamente las Atenciones registradas a Pacientes",
            value=not df_atenciones_locales.empty,
            key="chk_conciliar_atenc"
        )

    col_st1, col_st2 = st.columns(2)
    with col_st1:
        archivos_iniciales = st.file_uploader(
            "📁 Adjuntar Stock Inicial externo (si no usas las Cajas del sistema)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="up_inicial"
        )
    with col_st2:
        archivos_finales = st.file_uploader(
            "📁 Adjuntar Conteo Físico / Stock Remanente Final (Opcional)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="up_final"
        )

    # 1. Determinar Stock Inicial
    df_inicial_total = pd.DataFrame()
    if usar_don_como_ini and not df_don_base.empty:
        df_don_calc = df_don_base.copy()
        df_don_calc["farmaco_norm"] = df_don_calc["contenido"].apply(normalizar_nombre_farmaco)
        res_ini = df_don_calc.groupby("farmaco_norm")["cantidad"].sum().reset_index()
        res_ini.columns = ["Fármaco", "Stock_Inicial"]
        df_inicial_total = res_ini
        st.caption(f"✅ Se cargaron **{len(df_inicial_total)}** medicamentos desde las Cajas de Donación registradas.")
    elif archivos_iniciales:
        lista_i = []
        for arch in archivos_iniciales:
            df_i, _ = leer_archivo_tabular_unificado(arch)
            if not df_i.empty:
                lista_i.append(df_i)
        if lista_i:
            df_up_ini = pd.concat(lista_i, ignore_index=True)
            col_f_i = next((c for c in df_up_ini.columns if any(k in c.lower() for k in ["farmaco", "fármaco", "medicamento", "producto", "item", "contenido"])), df_up_ini.columns[0])
            col_c_i = next((c for c in df_up_ini.columns if any(k in c.lower() for k in ["cantidad", "stock", "total", "inicial", "uds"])), df_up_ini.columns[1] if len(df_up_ini.columns) > 1 else df_up_ini.columns[0])
            df_up_ini[col_c_i] = pd.to_numeric(df_up_ini[col_c_i], errors="coerce").fillna(0)
            df_up_ini["farmaco_norm"] = df_up_ini[col_f_i].apply(normalizar_nombre_farmaco)
            df_inicial_total = df_up_ini.groupby("farmaco_norm")[col_c_i].sum().reset_index()
            df_inicial_total.columns = ["Fármaco", "Stock_Inicial"]

    # 2. Entregas a pacientes (netas)
    df_entregas_pacientes = pd.DataFrame(columns=["Fármaco", "Entregado_Pacientes"])
    if conciliar_con_atenciones:
        try:
            with obtener_conexion_bd() as conn:
                df_movs = pd.read_sql("""
                    SELECT 
                        farmaco_norm,
                        COALESCE(SUM(CASE WHEN tipo = 'entrega' THEN cantidad ELSE 0 END), 0) -
                        COALESCE(SUM(CASE WHEN tipo = 'anulacion' THEN cantidad ELSE 0 END), 0) as cantidad
                    FROM movimientos_stock
                    WHERE COALESCE(archivado, 0) = 0
                    GROUP BY farmaco_norm
                """, conn)
            if not df_movs.empty:
                df_movs["cantidad"] = pd.to_numeric(df_movs["cantidad"], errors="coerce").fillna(0)
                df_entregas_pacientes = df_movs[df_movs["cantidad"] > 0].copy()
                df_entregas_pacientes.columns = ["Fármaco", "Entregado_Pacientes"]
            elif not df_atenciones_locales.empty:
                df_atenc_c = df_atenciones_locales.copy()
                df_atenc_c["farmaco_norm"] = df_atenc_c["farmaco_entregado"].apply(normalizar_nombre_farmaco)
                df_atenc_c["cantidad_entregada"] = pd.to_numeric(df_atenc_c["cantidad_entregada"], errors="coerce").fillna(0)
                df_entregas_pacientes = df_atenc_c.groupby("farmaco_norm")["cantidad_entregada"].sum().reset_index()
                df_entregas_pacientes.columns = ["Fármaco", "Entregado_Pacientes"]
        except Exception as e_mov:
            logger.error(f"Error consultando entregas para conciliación de stock: {e_mov}", exc_info=True)
            st.warning("⚠️ No se pudieron conciliar todas las entregas de medicamentos.")

    # 3. Stock Final Físico
    df_final_total = pd.DataFrame()
    if archivos_finales:
        lista_f = []
        for arch in archivos_finales:
            df_f, _ = leer_archivo_tabular_unificado(arch)
            if not df_f.empty:
                lista_f.append(df_f)
        if lista_f:
            df_up_fin = pd.concat(lista_f, ignore_index=True)
            col_f_f = next((c for c in df_up_fin.columns if any(k in c.lower() for k in ["farmaco", "fármaco", "medicamento", "producto", "item", "contenido"])), df_up_fin.columns[0])
            col_c_f = next((c for c in df_up_fin.columns if any(k in c.lower() for k in ["cantidad", "stock", "total", "final", "remanente", "uds"])), df_up_fin.columns[1] if len(df_up_fin.columns) > 1 else df_up_fin.columns[0])
            df_up_fin[col_c_f] = pd.to_numeric(df_up_fin[col_c_f], errors="coerce").fillna(0)
            df_up_fin["farmaco_norm"] = df_up_fin[col_f_f].apply(normalizar_nombre_farmaco)
            df_final_total = df_up_fin.groupby("farmaco_norm")[col_c_f].sum().reset_index()
            df_final_total.columns = ["Fármaco", "Stock_Final_Fisico"]

    # 4. Matriz de conciliación
    if not df_inicial_total.empty or not df_entregas_pacientes.empty:
        st.divider()
        col_t_res, col_sld = st.columns([3, 2])
        with col_t_res:
            st.markdown("### 📊 Balance Conciliado de Medicamentos")
        with col_sld:
            umbral_critico = st.slider("⚠️ Umbral de Alerta de Stock Crítico (unidades):", min_value=1, max_value=30, value=10, key="slider_umbral_critico")

        if not df_inicial_total.empty:
            df_conciliado = pd.merge(df_inicial_total, df_entregas_pacientes, on="Fármaco", how="outer").fillna(0)
        else:
            df_conciliado = df_entregas_pacientes.copy()
            df_conciliado["Stock_Inicial"] = 0

        df_conciliado["Stock_Teorico_Restante"] = df_conciliado["Stock_Inicial"] - df_conciliado["Entregado_Pacientes"]

        if not df_final_total.empty:
            df_conciliado = pd.merge(df_conciliado, df_final_total, on="Fármaco", how="outer").fillna(0)
            df_conciliado["Discrepancia_Fisica"] = df_conciliado["Stock_Final_Fisico"] - df_conciliado["Stock_Teorico_Restante"]

        st.caption(
            "📌 **Interpretación:**\n"
            "- **Stock Teórico Restante:** Cantidad esperada que debe quedar tras las atenciones médicas.\n"
            "- **Discrepancia Física:** Diferencia entre el conteo físico en mano y lo registrado en el sistema."
        )

        def estilo_conciliacion(val):
            try:
                v = float(val)
                if v <= 0:
                    return 'background-color: #7f1d1d; color: #fecaca; font-weight: bold;'
                elif v <= umbral_critico:
                    return 'background-color: #78350f; color: #fef3c7; font-weight: bold;'
            except Exception:
                pass
            return ''

        df_mostrar = df_conciliado.sort_values(by="Stock_Teorico_Restante", ascending=True)
        st.dataframe(
            df_mostrar.style.map(estilo_conciliacion, subset=["Stock_Teorico_Restante"]),
            use_container_width=True
        )

        c_m1, c_m2, c_m3 = st.columns(3)
        c_m1.metric("📦 Fármacos en Control", len(df_mostrar))
        c_m2.metric("💊 Total Unidades Entregadas", int(df_mostrar["Entregado_Pacientes"].sum()))
        criticos_cnt = len(df_mostrar[df_mostrar["Stock_Teorico_Restante"] <= umbral_critico])
        c_m3.metric("⚠️ Medicamentos en Alerta Crítica", criticos_cnt)

        excel_concil_bytes = df_a_excel_bytes(df_conciliado, nombre_hoja_defecto="Conciliacion_Stock")
        st.download_button(
            label="📥 Descargar Reporte Completo de Conciliación en Excel",
            data=excel_concil_bytes,
            file_name=f"Reporte_Conciliacion_Stock_{datetime.now():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_descarga_conciliacion_stock",
            use_container_width=True
        )
    else:
        st.info("ℹ️ Para ver el balance, registra Cajas de Stock en 'Registro de Nuevo Stock' o adjunta archivos de Stock Inicial arriba.")
