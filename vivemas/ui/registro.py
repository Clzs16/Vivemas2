# -*- coding: utf-8 -*-
"""Pestaña 1: Registro rápido de atenciones médicas en campo con soporte multi-fármaco."""

import uuid
import json
import logging
from datetime import datetime
import streamlit as st
import pandas as pd

from vivemas.config import SS, LISTA_FARMACOS_COMUNES
from vivemas.db import (
    obtener_conexion_bd,
    cargar_registros_local,
    guardar_config,
    obtener_config,
    crear_respaldo_bd,
)
from vivemas.textos import (
    normalizar_nombre_farmaco,
    validar_formato_codigo_paciente,
)
from vivemas.diagnosticos import normalizar_diagnostico
from vivemas.stock import (
    calcular_stock_disponible_item,
    eliminar_atencion_individual,
)
from vivemas.exportes import df_a_excel_bytes
from vivemas.seguridad import (
    tiene_rol,
    requiere_rol,
    obtener_hashes_roles,
    verificar_password_pbkdf2,
    registrar_auditoria,
)
from vivemas.sync_firebase import conectar_firestore, ejecutar_sincronizacion_pendientes
from vivemas.ui.componentes import paginar_dataframe

logger = logging.getLogger("vivemas.ui.registro")


def render_tab_registro():
    """Renderiza la pestaña principal de Registro de Atenciones Médicas."""
    lugar_def_tab1 = st.session_state.get("zona_activa", "La Guaira")
    fecha_def_tab1 = st.session_state.get("fecha_jornada_activa", datetime.now().strftime("%Y-%m-%d"))
    jornada_activa_tag = st.session_state.get("nombre_jornada_activa", f"Jornada - {lugar_def_tab1} - {fecha_def_tab1}")

    st.caption(f"🏷️ **Jornada Activa:** `{jornada_activa_tag}` &nbsp;•&nbsp; *Lugar: **{lugar_def_tab1}** | Fecha: **{fecha_def_tab1}***")
    st.info("📋 **Aviso de Consentimiento y Uso de Datos de Salud:** Todas las atenciones registradas son tratadas bajo estricto secreto profesional y confidencialidad médica para asistencia humanitaria y farmacovigilancia comunitaria.")

    # Inicializar listas dinámicas en sesión si no existen
    if "lista_farmacos" not in st.session_state:
        try:
            st.session_state["lista_farmacos"] = json.loads(obtener_config("lista_farmacos", "[]")) or LISTA_FARMACOS_COMUNES
        except Exception:
            st.session_state["lista_farmacos"] = LISTA_FARMACOS_COMUNES

    if "lista_diagnosticos" not in st.session_state:
        from vivemas.diagnosticos import CATEGORIAS_DIAGNOSTICAS
        diags_base = [cat[0] for cat in CATEGORIAS_DIAGNOSTICAS]
        try:
            st.session_state["lista_diagnosticos"] = json.loads(obtener_config("lista_diagnosticos", "[]")) or diags_base
        except Exception:
            st.session_state["lista_diagnosticos"] = diags_base

    if "form_reg_id" not in st.session_state:
        st.session_state["form_reg_id"] = 0
    f_id = st.session_state["form_reg_id"]

    for clave_b, val_b in [
        ("borrador_paciente", ""),
        ("borrador_diag", ""),
        ("borrador_cant", 1),
        ("borrador_nzona", ""),
        ("borrador_nfarm", ""),
        ("borrador_cant2", 1),
        ("borrador_cant3", 1),
        ("borrador_cant4", 1),
        ("borrador_cant5", 1),
    ]:
        if clave_b not in st.session_state:
            st.session_state[clave_b] = val_b

    with st.form(f"form_atencion_{f_id}", clear_on_submit=False):
        col_z1, col_z2 = st.columns(2)
        with col_z1:
            opciones_zona = st.session_state["lista_zonas"] + ["➕ Agregar nuevo lugar..."]
            zona_idx = 0
            zona_previa = st.session_state.get("zona_activa", st.session_state["lista_zonas"][0] if st.session_state["lista_zonas"] else "La Guaira")
            if zona_previa in opciones_zona:
                zona_idx = opciones_zona.index(zona_previa)
            zona_elegida = st.selectbox("📍 Zona / Lugar de Atención", options=opciones_zona, index=zona_idx, key=f"sel_zona_{f_id}")
        with col_z2:
            nuevo_lugar_input = st.text_input("✍️ Escribe el nuevo lugar (solo si elegiste 'Agregar nuevo'):", value=st.session_state.get("borrador_nzona", ""), key=f"inp_nzona_{f_id}")

        st.markdown("---")

        col_a, col_b = st.columns(2)
        with col_a:
            paciente_id = st.text_input(
                "Nombre o Cédula:",
                value=st.session_state.get("borrador_paciente", ""),
                placeholder="Ej: María Pérez o V-12345678",
                key=f"inp_paciente_{f_id}"
            )

            diag_input = st.text_input(
                "Diagnóstico (Opcional):",
                value=st.session_state.get("borrador_diag", ""),
                placeholder="Escribe el diagnóstico manualmente o deja en blanco",
                key=f"inp_diag_{f_id}"
            )

        with col_b:
            # FÁRMACO 1 (Principal)
            opciones_farmacos_form = st.session_state["lista_farmacos"] + ["➕ Agregar nuevo medicamento..."]
            farmaco_elegido_form = st.selectbox("Fármaco 1 (Principal)", options=opciones_farmacos_form, key=f"select_farmaco_{f_id}")
            nuevo_farmaco_form = st.text_input("✍️ Escribe nuevo fármaco (si elegiste 'Agregar nuevo'):", value=st.session_state.get("borrador_nfarm", ""), key=f"inp_nfarm_{f_id}")

            f_eval = nuevo_farmaco_form.strip() if farmaco_elegido_form == "➕ Agregar nuevo medicamento..." else farmaco_elegido_form
            info_stk = calcular_stock_disponible_item(f_eval)
            chk_falla_stock = False
            if info_stk.get("error"):
                st.warning(f"⚠️ No se pudo verificar inventario en tiempo real ({info_stk.get('detalle_error')}).")
                chk_falla_stock = st.checkbox("⚠️ Confirmo guardar sin validación de stock", key=f"chk_falla_stk_{f_id}")
            elif info_stk["registrado"]:
                disp_cant = info_stk["disponible"]
                if disp_cant <= 0:
                    st.error(f"🔴 **Sin stock disponible:** {info_stk['total_entregado']}/{info_stk['total_ingresado']} entregados.")
                elif disp_cant <= 10:
                    st.warning(f"⚠️ **Stock crítico:** Quedan solo **{disp_cant}** uds.")
                else:
                    st.caption(f"📦 Stock disponible: **{disp_cant}** uds.")

            cantidad = st.number_input("Cantidad Entregada (Fármaco 1)", min_value=1, step=1, value=st.session_state.get("borrador_cant", 1), key=f"inp_cant_{f_id}")

        # MULTI-FÁRMACO: Medicamentos 2º, 3º, 4º y 5º para la misma consulta
        with st.expander("➕ Agregar más medicamentos a esta misma consulta (Multi-fármaco: 2º, 3º, 4º y 5º fármaco)", expanded=False):
            st.caption("Permite entregar hasta 5 fármacos al mismo paciente sin tener que volver a ingresar sus datos.")
            opciones_extras = ["-- Ninguno adicional --"] + st.session_state["lista_farmacos"]

            col_m2_f, col_m2_c = st.columns([3, 1])
            with col_m2_f:
                f2_sel = st.selectbox("Fármaco 2 (opcional)", options=opciones_extras, key=f"sel_f2_{f_id}")
            with col_m2_c:
                cant2 = st.number_input("Cantidad 2", min_value=1, step=1, value=st.session_state.get("borrador_cant2", 1), key=f"cant2_{f_id}")

            col_m3_f, col_m3_c = st.columns([3, 1])
            with col_m3_f:
                f3_sel = st.selectbox("Fármaco 3 (opcional)", options=opciones_extras, key=f"sel_f3_{f_id}")
            with col_m3_c:
                cant3 = st.number_input("Cantidad 3", min_value=1, step=1, value=st.session_state.get("borrador_cant3", 1), key=f"cant3_{f_id}")

            col_m4_f, col_m4_c = st.columns([3, 1])
            with col_m4_f:
                f4_sel = st.selectbox("Fármaco 4 (opcional)", options=opciones_extras, key=f"sel_f4_{f_id}")
            with col_m4_c:
                cant4 = st.number_input("Cantidad 4", min_value=1, step=1, value=st.session_state.get("borrador_cant4", 1), key=f"cant4_{f_id}")

            col_m5_f, col_m5_c = st.columns([3, 1])
            with col_m5_f:
                f5_sel = st.selectbox("Fármaco 5 (opcional)", options=opciones_extras, key=f"sel_f5_{f_id}")
            with col_m5_c:
                cant5 = st.number_input("Cantidad 5", min_value=1, step=1, value=st.session_state.get("borrador_cant5", 1), key=f"cant5_{f_id}")

        submitted = st.form_submit_button("💾 Guardar Atención Médica", use_container_width=True)

        if submitted:
            st.session_state["borrador_paciente"] = paciente_id
            st.session_state["borrador_cant"] = int(cantidad)
            st.session_state["borrador_nzona"] = nuevo_lugar_input
            st.session_state["borrador_nfarm"] = nuevo_farmaco_form

            # 1. Resolver zona
            zona_final_registro = nuevo_lugar_input.strip() if zona_elegida == "➕ Agregar nuevo lugar..." else zona_elegida.strip()

            # 2. Resolver fármaco 1
            farmaco_final = nuevo_farmaco_form.strip() if farmaco_elegido_form == "➕ Agregar nuevo medicamento..." else farmaco_elegido_form.strip()

            # 3. Resolver diagnóstico (campo manual, libre y 100% opcional)
            if diag_input and diag_input.strip():
                diag_final = diag_input.strip()
                st.session_state["borrador_diag"] = diag_input.strip()
            else:
                diag_final = ""
                st.session_state["borrador_diag"] = ""


            # Lista de fármacos a registrar en esta consulta
            farmacos_a_entregar = [(farmaco_final, int(cantidad))]
            for f_extra_val, c_extra_val in [
                (f2_sel, cant2),
                (f3_sel, cant3),
                (f4_sel, cant4),
                (f5_sel, cant5),
            ]:
                if f_extra_val and f_extra_val != "-- Ninguno adicional --":
                    farmacos_a_entregar.append((f_extra_val.strip(), int(c_extra_val)))

            # Validaciones: no se bloquea por diagnóstico ni por edad
            if not zona_final_registro:
                st.error("⚠️ Falta el Lugar: Por favor escribe el nombre del nuevo lugar.")
            elif farmaco_elegido_form == "➕ Agregar nuevo medicamento..." and not farmaco_final:
                st.error("⚠️ Falta el Medicamento: Por favor escribe el nombre del nuevo fármaco.")
            elif not paciente_id.strip():
                st.error("⚠️ Falta el Paciente: Por favor escribe el nombre o cédula del paciente.")
            else:
                if zona_final_registro not in st.session_state["lista_zonas"]:
                    st.session_state["lista_zonas"].append(zona_final_registro)
                    guardar_config("lista_zonas", json.dumps(st.session_state["lista_zonas"]))
                st.session_state["zona_activa"] = zona_final_registro

                if farmaco_final not in st.session_state["lista_farmacos"]:
                    st.session_state["lista_farmacos"].append(farmaco_final)
                    guardar_config("lista_farmacos", json.dumps(st.session_state["lista_farmacos"]))

                diags_indivs = [d.strip() for d in diag_final.split(" + ") if d.strip()]
                hubo_nuevo_diag = False
                for d_ind in diags_indivs:
                    if d_ind not in st.session_state["lista_diagnosticos"]:
                        st.session_state["lista_diagnosticos"].append(d_ind)
                        hubo_nuevo_diag = True
                if hubo_nuevo_diag:
                    guardar_config("lista_diagnosticos", json.dumps(st.session_state["lista_diagnosticos"]))

                fecha_guardar = st.session_state.get("fecha_jornada_activa", datetime.now().strftime("%Y-%m-%d"))
                nom_jornada_guardar = f"Jornada - {zona_final_registro} - {fecha_guardar}"

                guardado_exitoso = False
                sin_stock_avisos = []

                # Transacción BEGIN IMMEDIATE para atenciones multi-fármaco atómicas
                with obtener_conexion_bd() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    bloqueo_stock = False

                    # Validar stock para cada medicamento de la consulta
                    for f_nom, f_cant in farmacos_a_entregar:
                        stk_chk = calcular_stock_disponible_item(f_nom, conn=conn)
                        if stk_chk.get("error"):
                            if not chk_falla_stock:
                                bloqueo_stock = True
                                conn.rollback()
                                st.warning(f"⚠️ No se pudo verificar el stock de '{f_nom}' ({stk_chk.get('detalle_error')}).")
                                break
                        elif stk_chk["registrado"] and f_cant > stk_chk["disponible"]:
                            bloqueo_stock = True
                            conn.rollback()
                            st.error(
                                f"🔴 **Stock insuficiente de '{f_nom}':** Solicitadas {f_cant} uds, pero solo quedan {stk_chk['disponible']} disponibles."
                            )
                            break
                        elif not stk_chk["registrado"]:
                            sin_stock_avisos.append(f_nom)

                    if not bloqueo_stock:
                        cursor = conn.cursor()
                        for f_nom, f_cant in farmacos_a_entregar:
                            item_uuid = str(uuid.uuid4())
                            f_norm = normalizar_nombre_farmaco(f_nom)
                            cursor.execute("""
                                INSERT INTO registros_jornada (fecha, zona, paciente_id, edad, diagnostico, farmaco_entregado, cantidad_entregada, sincronizado, uuid, nombre_jornada, tipo_registro)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'en_vivo')
                            """, (
                                fecha_guardar,
                                zona_final_registro,
                                paciente_id.strip(),
                                0,  # Edad no solicitada
                                diag_final,
                                f_nom,
                                int(f_cant),
                                item_uuid,
                                nom_jornada_guardar
                            ))
                            cursor.execute("""
                                INSERT INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid, archivado)
                                VALUES (?, ?, ?, 'entrega', ?, ?, 0)
                            """, (
                                fecha_guardar,
                                f_nom,
                                f_norm,
                                int(f_cant),
                                item_uuid
                            ))
                        conn.commit()
                        guardado_exitoso = True

                if guardado_exitoso:
                    cargar_registros_local.clear()
                    try:
                        st.toast("✅ ¡Atención registrada correctamente!", icon="🩺")
                    except Exception:
                        pass

                    if sin_stock_avisos:
                        st.warning(f"ℹ️ Medicamento(s) sin inventario inicial previo: {', '.join(sin_stock_avisos)}.")

                    st.success(f"¡Atención de {paciente_id} guardada con éxito ({len(farmacos_a_entregar)} fármaco(s))!")

                    st.session_state["borrador_paciente"] = ""
                    st.session_state["borrador_diag"] = ""
                    st.session_state["borrador_cant"] = 1
                    st.session_state["borrador_nzona"] = ""
                    st.session_state["borrador_nfarm"] = ""
                    for k_c in ["borrador_cant2", "borrador_cant3", "borrador_cant4", "borrador_cant5"]:
                        st.session_state[k_c] = 1
                    st.session_state["form_reg_id"] += 1
                    st.rerun()

    # Tabla de registros en vivo
    st.divider()
    df_local = cargar_registros_local()

    col_t_reg, col_btn_sync = st.columns([4, 1])
    with col_t_reg:
        st.markdown("### 📋 Registros de Atención en Vivo")
    with col_btn_sync:
        if st.button("🔄 Actualizar", key="btn_refrescar_tabla_reg", use_container_width=True):
            cargar_registros_local.clear()
            st.rerun()

    if not df_local.empty:
        col_filtro1, col_filtro2, col_filtro3 = st.columns([2, 2, 2])
        with col_filtro1:
            zonas_opc = ["Todas las Zonas"] + sorted([z for z in df_local["zona"].dropna().unique().tolist() if str(z).strip()])
            filtro_z = st.selectbox("Filtrar por Zona:", zonas_opc, key="filtro_tabla_zona")
        with col_filtro2:
            diags_opc = ["Todos los Diagnósticos"] + sorted([d for d in df_local["diagnostico"].dropna().unique().tolist() if str(d).strip()])
            filtro_d = st.selectbox("Filtrar por Diagnóstico:", diags_opc, key="filtro_tabla_diag")
        with col_filtro3:
            filtro_txt = st.text_input("Buscar Paciente o Fármaco:", key="filtro_tabla_txt")

        df_filtrado = df_local.copy()
        if filtro_z != "Todas las Zonas":
            df_filtrado = df_filtrado[df_filtrado["zona"] == filtro_z]
        if filtro_d != "Todos los Diagnósticos":
            df_filtrado = df_filtrado[df_filtrado["diagnostico"] == filtro_d]
        if filtro_txt.strip():
            txt_low = filtro_txt.strip().lower()
            df_filtrado = df_filtrado[
                df_filtrado["paciente_id"].astype(str).str.lower().str.contains(txt_low, na=False) |
                df_filtrado["farmaco_entregado"].astype(str).str.lower().str.contains(txt_low, na=False)
            ]

        # Paginación (U9)
        df_paginado = paginar_dataframe(df_filtrado, clave_paginacion="reg_local", filas_por_defecto=15)

        st.caption("💡 *Para corregir o anular un registro, marca la casilla en **'🗑️ Borrar'** (restaura el stock automáticamente).*")

        df_con_check = df_paginado.copy()
        df_con_check.insert(0, "🗑️ Borrar", False)
        columnas_bloqueadas = [c for c in df_paginado.columns]

        df_interactivo = st.data_editor(
            df_con_check,
            use_container_width=True,
            disabled=columnas_bloqueadas,
            hide_index=True,
            key="editor_tabla_local"
        )

        filas_a_eliminar = df_interactivo[df_interactivo["🗑️ Borrar"] == True]
        if not filas_a_eliminar.empty:
            cant_eliminar = len(filas_a_eliminar)
            nombres_eliminar = ", ".join(map(str, filas_a_eliminar["paciente_id"].tolist()[:3]))
            if cant_eliminar > 3:
                nombres_eliminar += f" y {cant_eliminar - 3} más..."

            col_warn, col_del_btn = st.columns([3, 1])
            with col_warn:
                st.warning(f"⚠️ Has seleccionado **{cant_eliminar}** registro(s) para eliminar: **{nombres_eliminar}**")
            with col_del_btn:
                if st.button(f"🗑️ Eliminar ({cant_eliminar})", type="primary", key="btn_eliminar_filas_seleccionadas", use_container_width=True):
                    uuids_a_borrar = [str(u).strip() for u in filas_a_eliminar["uuid"].dropna().tolist() if str(u).strip()]
                    with obtener_conexion_bd() as conn:
                        for _, row_del in filas_a_eliminar.iterrows():
                            eliminar_atencion_individual(
                                conn=conn,
                                rid=row_del["id"],
                                uid=row_del.get("uuid"),
                                farmaco=row_del.get("farmaco_entregado"),
                                cantidad=row_del.get("cantidad_entregada"),
                                fecha=row_del.get("fecha")
                            )

                    try:
                        db_cloud_del = conectar_firestore()
                        if db_cloud_del and uuids_a_borrar:
                            col_fb_del = db_cloud_del.collection("registros_jornada")
                            b_del = db_cloud_del.batch()
                            for uid in uuids_a_borrar:
                                b_del.delete(col_fb_del.document(uid))
                            b_del.commit(timeout=5)
                            with obtener_conexion_bd() as conn:
                                conn.executemany("UPDATE registros_eliminados SET sincronizado = 1 WHERE uuid = ?", [(uid,) for uid in uuids_a_borrar])
                    except Exception as e_del_rt:
                        logger.info(f"Propagación a Firebase pospuesta ({e_del_rt})")

                    cargar_registros_local.clear()
                    registrar_auditoria("BORRADO_INDIVIDUAL", f"Eliminados {cant_eliminar} registros (UUIDs: {uuids_a_borrar})")
                    st.success(f"¡{cant_eliminar} registro(s) eliminado(s) y stock recuperado!")
                    st.rerun()

        # Banner de sincronización
        pendientes_sync = int((df_local["sincronizado"] == 0).sum()) if "sincronizado" in df_local.columns else 0
        total_reg = len(df_local)

        if pendientes_sync > 0:
            col_warn_b, col_btn_sync_b = st.columns([3, 1])
            with col_warn_b:
                st.warning(f"⚠️ **{pendientes_sync}** registro(s) pendientes de subir a la nube. *(Total guardados: {total_reg})*")
            with col_btn_sync_b:
                if st.button("☁️ Sincronizar Ahora", key="btn_sync_banner_directo", use_container_width=True):
                    with st.spinner("Sincronizando con Firebase Firestore..."):
                        ok_b, msg_b = ejecutar_sincronizacion_pendientes()
                        if ok_b:
                            registrar_auditoria("SINCRONIZACION_NUBE", "Sincronización directa exitosa")
                            st.success(msg_b)
                            st.rerun()
                        else:
                            st.error(msg_b)
        else:
            st.success(f"☁️ Todos los **{total_reg}** registros locales están al día con Firebase.")

        # Exportación unificada a Excel
        excel_bytes = df_a_excel_bytes(df_local, nombre_hoja_defecto="Registros_Locales")
        st.download_button(
            label="📥 Descargar Registros en Excel (.xlsx)",
            data=excel_bytes,
            file_name="Registros_Locales_ViveMas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_descarga_excel_inferior",
            use_container_width=True
        )

        with st.expander("📦 Archivar jornada (solo local)"):
            st.markdown(
                "Esta acción archiva y limpia los datos de atención de este dispositivo. "
                "**La base de datos en la nube (Firebase) NO se modifica ni se borra.**"
            )
            clave_coord_arch = st.text_input("Clave de acceso autorizada:", type="password", key="inp_clave_arch_local")
            confirmar_palabra = st.text_input("Escribe 'BORRAR' para confirmar el archivado local:", key="inp_confirmar_palabra_arch")
            if st.button("📦 Ejecutar Archivado Local", type="primary", key="btn_archivar_local_ejecutar"):
                h_coord, h_campo = obtener_hashes_roles()
                valida = (h_coord and verificar_password_pbkdf2(clave_coord_arch, h_coord)) or \
                         (h_campo and verificar_password_pbkdf2(clave_coord_arch, h_campo))
                if (confirmar_palabra or "").strip().upper() != "BORRAR":
                    st.error("⚠️ Debes escribir exactamente la palabra 'BORRAR' en mayúsculas.")
                elif not valida:
                    st.error("❌ Clave de acceso incorrecta.")
                    registrar_auditoria("REAUTENTICACION_FALLIDA", "Fallo al autorizar archivado local de jornada")
                else:
                    respaldo_ok = crear_respaldo_bd(etiqueta="pre_archivo_local")
                    if not respaldo_ok:
                        logger.warning("No se pudo generar el respaldo automático antes del archivado local.")

                    with obtener_conexion_bd() as conn:
                        conn.execute("DELETE FROM registros_jornada")
                    cargar_registros_local.clear()
                    registrar_auditoria("ARCHIVAR_JORNADA_LOCAL", f"Archivados y limpiados {len(df_local)} registros locales")
                    st.success("✅ Base de datos local archivada y limpiada con éxito. La nube permanece intacta.")
                    st.rerun()
    else:
        st.info("Aún no hay registros de atención guardados hoy. Utiliza el formulario superior para comenzar.")
