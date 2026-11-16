# -*- coding: utf-8 -*-
"""Pestaña 6: Estadísticas, reportes consolidados y Asistente Guiado de Cierre de Jornada."""

import os
import re
import logging
from datetime import datetime
import streamlit as st
import pandas as pd

from vivemas.db import (
    cargar_registros_local,
    cargar_registros_historicos,
    cargar_stock_donaciones,
    crear_respaldo_bd,
)
from vivemas.diagnosticos import explotar_diagnosticos
from vivemas.exportes import df_a_excel_bytes
from vivemas.seguridad import (
    tiene_rol,
    requiere_rol,
    obtener_hashes_roles,
    verificar_password_pbkdf2,
    registrar_auditoria,
)
from vivemas.sync_firebase import (
    conectar_firestore,
    obtener_resumen_jornadas_firebase,
    consultar_registros_firebase,
    ejecutar_sincronizacion_pendientes,
    contar_pendientes_sincronizacion,
    hay_conexion_internet,
)
from vivemas.ui.componentes import paginar_dataframe

logger = logging.getLogger("vivemas.ui.reportes")


def render_tab_reportes():
    """Renderiza la pestaña de Reportes, Explorador de Nube y Asistente de Cierre de Jornada."""
    col_rep_t, col_rep_b = st.columns([3, 1])
    with col_rep_t:
        st.subheader("📊 Reportes, Cierre y Gestión de Datos")
    with col_rep_b:
        if st.button("🔄 Actualizar Datos", key="btn_refrescar_tabla_rep", use_container_width=True):
            cargar_registros_local.clear()
            cargar_registros_historicos.clear()
            st.rerun()

    subtab_loc, subtab_cierre, subtab_fb = st.tabs([
        "📊 Estadísticas Locales y Epidemiología",
        "🏁 Asistente de Cierre de Jornada (U8)",
        "☁️ Explorador y Búsqueda en Firebase (Nube)"
    ])

    # 1. ESTADÍSTICAS LOCALES
    with subtab_loc:
        st.markdown("Visualiza el consolidado de datos locales, registros históricos y análisis de distribución de fármacos.")
        df_rep_local = cargar_registros_local()
        df_rep_hist = cargar_registros_historicos()

        total_local = len(df_rep_local) if not df_rep_local.empty else 0
        total_hist = len(df_rep_hist) if not df_rep_hist.empty else 0
        total_general = total_local + total_hist

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("📋 Atenciones Locales", total_local)
        col_m2.metric("📁 Registros Históricos", total_hist)
        col_m3.metric("🌐 Total Consolidado", total_general)

        st.divider()

        df_consolidado = pd.concat([df_rep_local, df_rep_hist], ignore_index=True) if not df_rep_hist.empty else df_rep_local

        if not df_consolidado.empty:
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                st.markdown("##### 🩺 Top Diagnósticos Frecuentes")
                if "diagnostico" in df_consolidado.columns:
                    df_exp_diag = explotar_diagnosticos(df_consolidado)
                    diag_counts = df_exp_diag["diagnostico"].value_counts().head(6)
                    if not diag_counts.empty:
                        st.bar_chart(diag_counts)
                    else:
                        st.caption("Sin diagnósticos suficientes para graficar.")

            with col_g2:
                st.markdown("##### 💊 Top Medicamentos Distribuidos (Unidades)")
                if "farmaco_entregado" in df_consolidado.columns and "cantidad_entregada" in df_consolidado.columns:
                    farm_counts = df_consolidado.groupby("farmaco_entregado")["cantidad_entregada"].sum().sort_values(ascending=False).head(6)
                    if not farm_counts.empty:
                        st.bar_chart(farm_counts)
                    else:
                        st.caption("Sin datos de fármacos para graficar.")

            st.divider()
            st.markdown("##### 📋 Tabla Detallada de Atenciones")
            tipo_vista = st.radio("Selecciona qué registros visualizar:", ["Registros Locales (Jornada Actual)", "Consolidado Completo (Local + Histórico)"], horizontal=True)

            df_mostrar = df_rep_local if tipo_vista == "Registros Locales (Jornada Actual)" else df_consolidado
            df_mostrar_pag = paginar_dataframe(df_mostrar, clave_paginacion="rep_tabla", filas_por_defecto=15)
            st.dataframe(df_mostrar_pag, use_container_width=True)

            excel_rep_bytes = df_a_excel_bytes(df_mostrar, nombre_hoja_defecto="Reporte_Consolidado")
            col_exp1, col_exp2 = st.columns([1, 1])
            with col_exp1:
                st.download_button(
                    label="📥 Descargar Reporte en Excel",
                    data=excel_rep_bytes,
                    file_name="Reporte_Consolidado_ViveMas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            with col_exp2:
                if st.button("🛡️ Crear Respaldo Manual Ahora", use_container_width=True):
                    res = crear_respaldo_bd(etiqueta="manual")
                    if res:
                        st.success("¡Copia de seguridad creada con éxito en la carpeta 'respaldos'!")
                    else:
                        st.error("No se pudo generar la copia.")
        else:
            st.info("No hay registros almacenados actualmente para generar estadísticas.")

    # 2. ASISTENTE GUIADO DE CIERRE DE JORNADA (U8)
    with subtab_cierre:
        st.markdown("### 🏁 Asistente Guiado de Cierre de Jornada")
        st.markdown(
            "Flujo guiado para el equipo de salud al finalizar la jornada médica en campo: "
            "verifica atenciones, concilia inventario, genera el respaldo y exporta el acta oficial."
        )

        df_local_cierre = cargar_registros_local()
        df_stock_cierre = cargar_stock_donaciones()
        n_pend_sync, n_del_sync = contar_pendientes_sincronizacion()
        hay_net = hay_conexion_internet()

        st.markdown("#### 📝 Checklist de Cierre del Día:")

        # Paso 1: Revisión de atenciones
        c_p1, c_p2 = st.columns([3, 1])
        with c_p1:
            st.markdown(f"1. **Atenciones Realizadas:** Se registraron **{len(df_local_cierre)}** atenciones médicas hoy.")
        with c_p2:
            st.caption("✅ Verificado" if len(df_local_cierre) > 0 else "⚠️ Sin atenciones")

        # Paso 2: Estado de stock
        c_s1, c_s2 = st.columns([3, 1])
        with c_s1:
            st.markdown(f"2. **Cajas en Inventario:** **{len(df_stock_cierre)}** entradas de stock activas.")
        with c_s2:
            st.caption("✅ Verificado" if len(df_stock_cierre) > 0 else "ℹ️ Sin stock")

        # Paso 3: Sincronización con la nube
        c_sc1, c_sc2 = st.columns([3, 1])
        with c_sc1:
            st.markdown(f"3. **Cola de Sincronización:** **{n_pend_sync}** atenciones y **{n_del_sync}** eliminaciones pendientes.")
        with c_sc2:
            if n_pend_sync + n_del_sync == 0:
                st.caption("✅ Al día")
            else:
                st.caption("⏳ Pendiente")

        st.markdown("---")
        st.markdown("#### 🚀 Acciones de Cierre:")

        col_cact1, col_cact2 = st.columns(2)
        with col_cact1:
            if st.button("☁️ Sincronizar Cola Final a Firebase", key="btn_cierre_sync", use_container_width=True, disabled=not hay_net):
                with st.spinner("Subiendo pendientes a Firebase..."):
                    ok_s, msg_s = ejecutar_sincronizacion_pendientes()
                    if ok_s:
                        registrar_auditoria("CIERRE_SYNC", "Sincronización de cierre exitosa")
                        st.success(msg_s)
                        st.rerun()
                    else:
                        st.error(msg_s)
            if not hay_net:
                st.caption("*(Sin conexión a internet: los datos permanecen seguros en la base de datos local y se sincronizarán al conectar)*")

        with col_cact2:
            if st.button("🛡️ Generar Respaldo Cifrado de Cierre", key="btn_cierre_backup", type="primary", use_container_width=True):
                res_cierre = crear_respaldo_bd(etiqueta="cierre_oficial")
                if res_cierre:
                    st.session_state["cierre_backup_generado"] = res_cierre
                    registrar_auditoria("CIERRE_RESPALDO", f"Respaldo oficial de cierre generado en {res_cierre}")
                    st.success(f"¡Respaldo oficial generado con éxito!\n`{os.path.basename(res_cierre)}`")
                else:
                    st.error("No se pudo generar el respaldo de cierre.")

        st.markdown("---")
        st.markdown("#### 📄 Acta Oficial de Cierre en Excel:")

        # Generar Acta completa multi-hoja
        resumen_diags = explotar_diagnosticos(df_local_cierre)["diagnostico"].value_counts().reset_index() if not df_local_cierre.empty and "diagnostico" in df_local_cierre.columns else pd.DataFrame()
        if not resumen_diags.empty:
            resumen_diags.columns = ["Diagnóstico", "Casos"]

        resumen_farm = df_local_cierre.groupby("farmaco_entregado")["cantidad_entregada"].sum().reset_index() if not df_local_cierre.empty and "farmaco_entregado" in df_local_cierre.columns else pd.DataFrame()
        if not resumen_farm.empty:
            resumen_farm.columns = ["Medicamento", "Total_Entregado"]

        acta_bytes = df_a_excel_bytes({
            "Atenciones_Jornada": df_local_cierre,
            "Resumen_Epidemiologico": resumen_diags,
            "Consumo_Farmacos": resumen_farm,
            "Inventario_Stock": df_stock_cierre,
        })

        nom_j_act = st.session_state.get("nombre_jornada_activa", "Jornada")
        nom_arch_acta = f"Acta_Cierre_{re.sub(r'[^a-zA-Z0-9_]', '_', nom_j_act)}_{datetime.now():%Y%m%d}.xlsx"

        st.download_button(
            label=f"📥 Descargar Acta de Cierre Completa ({nom_arch_acta})",
            data=acta_bytes,
            file_name=nom_arch_acta,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_dl_acta_cierre",
            use_container_width=True
        )

    # 3. NUBE FIREBASE
    with subtab_fb:
        st.markdown("### ☁️ Consulta Directa en la Nube de Firebase")
        st.markdown("Filtra y busca exclusivamente los datos de una jornada en específico almacenada en Firebase.")

        col_fb_act1, col_fb_act2 = st.columns([2, 1])
        with col_fb_act1:
            if st.button("🔍 Explorar / Actualizar Jornadas en Firebase", key="btn_explorar_fb"):
                st.session_state["resumen_fb_cache"] = obtener_resumen_jornadas_firebase()
                st.rerun()

        resumen_jornadas_fb = st.session_state.get("resumen_fb_cache")
        if resumen_jornadas_fb is None:
            resumen_jornadas_fb = obtener_resumen_jornadas_firebase()
            st.session_state["resumen_fb_cache"] = resumen_jornadas_fb

        if not resumen_jornadas_fb:
            st.warning("⚠️ No se pudieron obtener jornadas de Firebase. Verifica si 'firebase_key.json' está presente o si hay conexión a internet.")
        else:
            total_jornadas_nube = len(resumen_jornadas_fb)
            total_docs_nube = sum(j["total"] for j in resumen_jornadas_fb)

            col_mfb1, col_mfb2 = st.columns(2)
            col_mfb1.metric("🏷️ Jornadas en Nube", total_jornadas_nube)
            col_mfb2.metric("📋 Total Registros en Firebase", total_docs_nube)

            st.divider()

            col_sel_t, col_sel_j = st.columns([1, 2])
            with col_sel_t:
                tipo_filtro_fb = st.selectbox("Filtrar por origen:", ["Todos los tipos", "🔴 Solo Jornadas en Vivo", "📁 Solo Históricos Excel"], key="sel_tipo_filtro_fb")

            jornadas_filtradas = resumen_jornadas_fb
            if tipo_filtro_fb == "🔴 Solo Jornadas en Vivo":
                jornadas_filtradas = [j for j in resumen_jornadas_fb if j["tipo"] == "en_vivo"]
            elif tipo_filtro_fb == "📁 Solo Históricos Excel":
                jornadas_filtradas = [j for j in resumen_jornadas_fb if j["tipo"] == "historico_excel"]

            opciones_jornadas_disp = ["🌐 Todas las jornadas listadas"] + [j["etiqueta"] for j in jornadas_filtradas]
            with col_sel_j:
                jornada_elegida_fb = st.selectbox("🎯 Selecciona la Jornada específica a consultar:", options=opciones_jornadas_disp, key="sel_jornada_especifica_fb")

            if st.button("📥 Consultar Registros de esta Jornada", type="primary", key="btn_consultar_jornada_fb"):
                with st.spinner("Consultando Firebase..."):
                    nombre_buscado = None
                    if jornada_elegida_fb != "🌐 Todas las jornadas listadas":
                        target_j = next((j["nombre"] for j in jornadas_filtradas if j["etiqueta"] == jornada_elegida_fb), None)
                        nombre_buscado = target_j

                    tipo_buscado = None
                    if tipo_filtro_fb == "🔴 Solo Jornadas en Vivo":
                        tipo_buscado = "en_vivo"
                    elif tipo_filtro_fb == "📁 Solo Históricos Excel":
                        tipo_buscado = "historico_excel"

                    regs_fb = consultar_registros_firebase(nombre_jornada=nombre_buscado, tipo_registro=tipo_buscado)
                    if regs_fb:
                        st.session_state["df_resultado_fb"] = pd.DataFrame(regs_fb)
                        st.session_state["nombre_resultado_fb"] = nombre_buscado or "Todas_las_jornadas"
                        st.success(f"¡Se recuperaron {len(regs_fb)} registros de Firebase!")
                    else:
                        st.warning("No se encontraron registros para la selección.")

            if "df_resultado_fb" in st.session_state and not st.session_state["df_resultado_fb"].empty:
                df_res_fb = st.session_state["df_resultado_fb"]
                nom_res = st.session_state.get("nombre_resultado_fb", "Jornada")

                st.markdown(f"#### 📋 Registros de: **{nom_res}** ({len(df_res_fb)} atenciones)")
                cols_fb_ver = [c for c in ["fecha", "zona", "paciente_id", "edad", "diagnostico", "farmaco_entregado", "cantidad_entregada", "tipo_registro", "nombre_jornada", "sincronizado_en"] if c in df_res_fb.columns]
                st.dataframe(df_res_fb[cols_fb_ver] if cols_fb_ver else df_res_fb, use_container_width=True)

                buf_fb = df_a_excel_bytes(df_res_fb, nombre_hoja_defecto="Registros_Firebase")
                nombre_limpio_arch = re.sub(r'[^a-zA-Z0-9_\-]', '_', nom_res)
                st.download_button(
                    label=f"📥 Descargar Excel de '{nom_res}'",
                    data=buf_fb,
                    file_name=f"Firebase_{nombre_limpio_arch}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="btn_dl_excel_fb",
                    use_container_width=True
                )

                st.markdown("---")
                with st.expander("🗑️ Purgar Registros en la Nube"):
                    cant_fb_del = len(df_res_fb)
                    st.warning(f"⚠️ Se eliminarán **{cant_fb_del}** registros de Firebase Firestore correspondientes a '{nom_res}'.")
                    clave_reauth_fb = st.text_input("Clave de acceso autorizada:", type="password", key="pwd_reauth_cloud_del")
                    txt_conf_fb = st.text_input("Escribe 'BORRAR' para confirmar la eliminación en la nube:", key="txt_conf_cloud_del")
                    if st.button("🗑️ Eliminar Registros de la Nube", type="primary", key="btn_exec_cloud_del"):
                        h_coord, h_campo = obtener_hashes_roles()
                        valida = (h_coord and verificar_password_pbkdf2(clave_reauth_fb, h_coord)) or \
                                 (h_campo and verificar_password_pbkdf2(clave_reauth_fb, h_campo))
                        if (txt_conf_fb or "").strip().upper() != "BORRAR":
                            st.error("⚠️ Debes escribir exactamente la palabra 'BORRAR'.")
                        elif not valida:
                            st.error("❌ Clave de acceso incorrecta.")
                            registrar_auditoria("REAUTENTICACION_FALLIDA", "Fallo al autorizar borrado en la nube")
                        else:
                                with st.spinner("Generando respaldo previo y eliminando de Firebase..."):
                                    try:
                                        os.makedirs("respaldos", exist_ok=True)
                                        f_resp_nube = os.path.join("respaldos", f"backup_nube_pre_borrado_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
                                        with open(f_resp_nube, "wb") as w_bak:
                                            w_bak.write(df_a_excel_bytes(df_res_fb, nombre_hoja_defecto="Nube_Pre_Borrado"))

                                        db_cl = conectar_firestore()
                                        if db_cl:
                                            col_ref_del = db_cl.collection("registros_jornada")
                                            b_purgar = db_cl.batch()
                                            count_purgados = 0
                                            for doc_id in df_res_fb["uuid"].dropna().tolist():
                                                b_purgar.delete(col_ref_del.document(str(doc_id).strip()))
                                                count_purgados += 1
                                                if count_purgados % 400 == 0:
                                                    b_purgar.commit()
                                                    b_purgar = db_cl.batch()
                                            b_purgar.commit()

                                            registrar_auditoria("BORRADO_NUBE", f"Eliminados {count_purgados} registros de Firestore ('{nom_res}'). Respaldo en {f_resp_nube}")
                                            st.success(f"✅ Se eliminaron {count_purgados} registros de Firebase. Respaldo guardado en 'respaldos'.")
                                            st.session_state["df_resultado_fb"] = pd.DataFrame()
                                            st.rerun()
                                    except Exception as e_purgar:
                                        logger.error(f"Error al purgar registros de Firebase: {e_purgar}", exc_info=True)
                                        st.error(f"Ocurrió un error al eliminar en Firebase: {e_purgar}")
