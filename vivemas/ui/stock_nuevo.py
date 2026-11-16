# -*- coding: utf-8 -*-
"""Pestaña 2: Registro de nuevo stock (cámara QR, foto de etiquetas con IA, Excel y manual)."""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import streamlit as st
import pandas as pd

from vivemas.db import (
    obtener_conexion_bd,
    cargar_stock_donaciones,
)
from vivemas.exportes import df_a_excel_bytes, leer_archivo_tabular_unificado
from vivemas.ia_gemini import analizar_etiqueta_con_ia
from vivemas.stock import vaciar_stock
from vivemas.seguridad import (
    tiene_rol,
    requiere_rol,
    obtener_hashes_roles,
    verificar_password_pbkdf2,
    registrar_auditoria,
)

logger = logging.getLogger("vivemas.ui.stock_nuevo")


def _a_entero(val: Any, default: int = 1) -> int:
    try:
        if val is None or pd.isna(val):
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default


def decodificar_qr_imagen(archivo_imagen) -> Optional[str]:
    """Intenta decodificar código QR de una imagen usando OpenCV y pyzbar."""
    if not archivo_imagen:
        return None
    try:
        import cv2
        import numpy as np
        bytes_data = archivo_imagen.getvalue()
        arr = np.frombuffer(bytes_data, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            detector = cv2.QRCodeDetector()
            valor, _, _ = detector.detectAndDecode(img)
            if valor and valor.strip():
                return valor.strip()
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            valor, _, _ = detector.detectAndDecode(gray)
            if valor and valor.strip():
                return valor.strip()
            thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 10)
            valor, _, _ = detector.detectAndDecode(thresh)
            if valor and valor.strip():
                return valor.strip()
    except Exception:
        pass
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
        img = Image.open(archivo_imagen)
        decoded = decode(img)
        if decoded:
            return decoded[0].data.decode("utf-8").strip()
    except Exception:
        pass
    return None


def parsear_texto_qr_caja(texto_qr: str) -> Dict[str, Any]:
    """Interpreta inteligentemente el texto de un QR (JSON o delimitado)."""
    if not texto_qr:
        return {}
    txt = str(texto_qr).strip()
    if txt.startswith("{") and txt.endswith("}"):
        try:
            datos = json.loads(txt)
            caja = datos.get("caja", datos.get("Caja", datos.get("box", "")))
            contenido = datos.get("contenido", datos.get("farmaco", datos.get("medicamento", datos.get("insumo", ""))))
            cantidad = datos.get("cantidad", datos.get("cant", datos.get("qty", 1)))
            tipo = datos.get("tipo", datos.get("tipo_item", "Medicamento"))
            lote = datos.get("lote", datos.get("vencimiento", ""))
            return {
                "caja": str(caja).strip(),
                "contenido": str(contenido).strip(),
                "cantidad": _a_entero(cantidad, 1),
                "tipo_item": str(tipo).strip(),
                "lote_vencimiento": str(lote).strip()
            }
        except Exception:
            pass

    for sep in ["|", ";", ","]:
        if sep in txt:
            partes = [p.strip() for p in txt.split(sep)]
            caja = partes[0] if len(partes) > 0 else ""
            contenido = partes[1] if len(partes) > 1 else ""
            cantidad = _a_entero(partes[2], 1) if len(partes) > 2 else 1
            lote = partes[3] if len(partes) > 3 else ""
            return {
                "caja": caja,
                "contenido": contenido,
                "cantidad": cantidad,
                "tipo_item": "Medicamento",
                "lote_vencimiento": lote
            }

    return {
        "caja": txt,
        "contenido": "",
        "cantidad": 1,
        "tipo_item": "Medicamento",
        "lote_vencimiento": ""
    }


def render_tab_stock_nuevo():
    """Renderiza la pestaña de Carga de Nuevo Stock."""
    st.subheader("📦 Registro de Nuevo Stock e Inventario de Cajas")
    st.markdown(
        "Registra las nuevas cajas de donaciones y medicamentos recibidos. "
        "Puedes escanear códigos QR con la cámara, subir fotos de etiquetas, cargar archivos Excel o registrar manualmente."
    )

    subtab_cam, subtab_foto, subtab_excel, subtab_manual = st.tabs([
        "📷 Escanear QR (Cámara)",
        "🖼️ Subir Fotos de Cajas / QR",
        "📁 Subir Archivo Excel/CSV",
        "✍️ Registro Rápido Manual"
    ])

    # 1. CÁMARA
    with subtab_cam:
        col_scan_l, col_scan_r = st.columns([1, 1])
        with col_scan_l:
            st.markdown("##### 📸 Escáner en Vivo con Cámara Web")
            st.caption("Apunta la cámara al código QR de la caja para leer sus datos al instante:")
            foto_cam = st.camera_input("Apuntar al QR:", key="cam_qr_donaciones_input")
            pistola_txt = st.text_input("🔫 O escanea con lector/pistola USB (o escribe código manual):", key="inp_pistola_qr_input")

            texto_crudo = ""
            if pistola_txt and pistola_txt.strip():
                texto_crudo = pistola_txt.strip()
            elif foto_cam is not None:
                texto_crudo = decodificar_qr_imagen(foto_cam) or ""

            datos_qr = parsear_texto_qr_caja(texto_crudo) if texto_crudo else {}
            if texto_crudo:
                if datos_qr.get("caja") or datos_qr.get("contenido"):
                    st.success(f"✅ ¡QR Detectado con éxito!: `{texto_crudo[:80]}`")
                else:
                    st.info(f"ℹ️ Código leído: `{texto_crudo[:80]}`. Completa los datos a la derecha.")

        with col_scan_r:
            st.markdown("##### 📦 Datos de la Caja y Medicamento")
            val_caja_sug = datos_qr.get("caja", "")
            val_cont_sug = datos_qr.get("contenido", "")
            val_cant_sug = datos_qr.get("cantidad", 50)
            val_lote_sug = datos_qr.get("lote_vencimiento", "")

            inp_caja_qr = st.text_input("📦 N° o Nombre de la Caja:", value=val_caja_sug, placeholder="Ej: Caja 01 - Antibióticos", key="inp_caja_qr_val")
            inp_cont_qr = st.text_input("💊 Contenido (Insumo o Fármaco):", value=val_cont_sug, placeholder="Ej: Amoxicilina 500mg", key="inp_cont_qr_val")

            col_k1, col_k2 = st.columns(2)
            with col_k1:
                inp_cant_qr = st.number_input("🔢 Cantidad de Unidades:", min_value=1, value=max(1, int(val_cant_sug)), key="inp_cant_qr_val")
            with col_k2:
                inp_tipo_qr = st.selectbox("🏷️ Tipo de Ítem:", ["Medicamento", "Insumo Médico / Descartable", "Material Quirúrgico", "Nutrición / Suplemento", "Otro"], key="inp_tipo_qr_val")

            inp_lote_qr = st.text_input("📅 Lote o Fecha de Vencimiento (Opcional):", value=val_lote_sug, placeholder="Ej: Lote 2408 - Venc 12/2026", key="inp_lote_qr_val")

            if st.button("💾 Registrar Caja en Nuevo Stock", type="primary", key="btn_guardar_qr_stock", use_container_width=True):
                if not inp_caja_qr.strip():
                    st.error("⚠️ Por favor escribe el N° o Nombre de la Caja.")
                elif not inp_cont_qr.strip():
                    st.error("⚠️ Por favor escribe el Contenido (fármaco o insumo) de la caja.")
                else:
                    with obtener_conexion_bd() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO stock_donaciones (caja, contenido, cantidad, tipo_item, lote_vencimiento, fecha_registro, origen)
                            VALUES (?, ?, ?, ?, ?, ?, 'Cámara QR')
                        """, (
                            inp_caja_qr.strip(),
                            inp_cont_qr.strip(),
                            int(inp_cant_qr),
                            inp_tipo_qr,
                            inp_lote_qr.strip(),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ))
                    cargar_stock_donaciones.clear()
                    st.success(f"¡{inp_cant_qr} unidades de '{inp_cont_qr}' registradas en '{inp_caja_qr}'!")
                    st.rerun()

    # 2. FOTO CON IA
    with subtab_foto:
        col_foto_l, col_foto_r = st.columns([1, 1])
        with col_foto_l:
            st.markdown("##### 🖼️ Cargar Foto de Caja o Etiqueta")
            subir_foto = st.file_uploader("Seleccionar imagen (PNG, JPG, JPEG):", type=["png", "jpg", "jpeg"], key="up_qr_foto_input_tab")
            datos_foto = {}
            if subir_foto is not None:
                st.image(subir_foto, caption="Foto adjunta", use_container_width=True)
                texto_qr_foto = decodificar_qr_imagen(subir_foto)
                if texto_qr_foto:
                    datos_foto = parsear_texto_qr_caja(texto_qr_foto)
                    st.success(f"✅ ¡Código QR detectado en la foto!: `{texto_qr_foto[:80]}`")
                else:
                    st.info("ℹ️ No se detectó un QR estándar. Puedes usar Gemini IA para analizar la foto:")
                    if st.button("🤖 Analizar Etiqueta con Gemini IA", key="btn_ia_analizar_foto_stock", use_container_width=True):
                        with st.spinner("Gemini IA analizando etiqueta y caja médica..."):
                            ia_res = analizar_etiqueta_con_ia(subir_foto)
                            if ia_res:
                                st.session_state["datos_foto_ia"] = ia_res
                                st.success("¡Datos extraídos por la IA exitosamente!")
                                st.rerun()
                            else:
                                st.warning("No se pudieron extraer datos automáticamente. Puedes completarlos a la derecha.")

            if "datos_foto_ia" in st.session_state and not datos_foto:
                datos_foto = st.session_state["datos_foto_ia"]

        with col_foto_r:
            st.markdown("##### 📦 Registro de la Foto Analizada")
            es_de_ia = ("datos_foto_ia" in st.session_state and bool(st.session_state["datos_foto_ia"]))
            if es_de_ia:
                st.info("ℹ️ **Datos sugeridos por IA:** Confirma su validez antes de ingresarlos.")

            f_caja = datos_foto.get("caja", "")
            f_cont = datos_foto.get("contenido", "")
            f_cant = datos_foto.get("cantidad", 50)
            f_tipo = datos_foto.get("tipo_item", "Medicamento")
            f_lote = datos_foto.get("lote_vencimiento", "")

            foto_caja_txt = st.text_input("📦 N° o Nombre de la Caja:", value=f_caja, placeholder="Ej: Caja 02", key="foto_caja_inp")
            foto_cont_txt = st.text_input("💊 Contenido (Insumo o Medicamento):", value=f_cont, placeholder="Ej: Omeprazol 20mg", key="foto_cont_inp")

            col_fk1, col_fk2 = st.columns(2)
            with col_fk1:
                foto_cant_num = st.number_input("🔢 Cantidad de Unidades:", min_value=1, value=max(1, int(f_cant)), key="foto_cant_inp")
            with col_fk2:
                tipos_disp = ["Medicamento", "Insumo Médico / Descartable", "Material Quirúrgico", "Nutrición / Suplemento", "Otro"]
                t_idx = tipos_disp.index(f_tipo) if f_tipo in tipos_disp else 0
                foto_tipo_sel = st.selectbox("🏷️ Tipo de Ítem:", tipos_disp, index=t_idx, key="foto_tipo_inp")

            foto_lote_txt = st.text_input("📅 Lote o Fecha de Vencimiento (Opcional):", value=f_lote, key="foto_lote_inp")

            confirmado_ia = True
            if es_de_ia:
                confirmado_ia = st.checkbox("✅ Confirmo que revisé y los datos extraídos son correctos", key="chk_confirmar_ia_don")

            if st.button("💾 Guardar Caja en Nuevo Stock", type="primary", disabled=(es_de_ia and not confirmado_ia), key="btn_guardar_foto_stock", use_container_width=True):
                if es_de_ia and not confirmado_ia:
                    st.error("⚠️ Debes marcar la casilla de confirmación para guardar los datos leídos por la IA.")
                elif not foto_caja_txt.strip():
                    st.error("⚠️ Por favor escribe el N° o Nombre de la Caja.")
                elif not foto_cont_txt.strip():
                    st.error("⚠️ Por favor escribe el Contenido de la caja.")
                else:
                    with obtener_conexion_bd() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO stock_donaciones (caja, contenido, cantidad, tipo_item, lote_vencimiento, fecha_registro, origen)
                            VALUES (?, ?, ?, ?, ?, ?, 'Foto / Etiqueta')
                        """, (
                            foto_caja_txt.strip(),
                            foto_cont_txt.strip(),
                            int(foto_cant_num),
                            foto_tipo_sel,
                            foto_lote_txt.strip(),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ))
                    cargar_stock_donaciones.clear()
                    if "datos_foto_ia" in st.session_state:
                        del st.session_state["datos_foto_ia"]
                    st.success(f"¡Caja '{foto_caja_txt}' registrada ({foto_cant_num} uds de {foto_cont_txt})!")
                    st.rerun()

    # 3. EXCEL
    with subtab_excel:
        st.markdown("##### 📁 Importación Masiva desde Archivo Excel o CSV")
        arch_don_up = st.file_uploader("Adjuntar archivo de donaciones:", type=["xlsx", "xls", "csv"], key="up_excel_donaciones_tab")

        if arch_don_up is not None:
            df_up_don, err_lec = leer_archivo_tabular_unificado(arch_don_up)
            if err_lec:
                st.error(err_lec)
            elif not df_up_don.empty:
                st.dataframe(df_up_don.head(10), use_container_width=True)
                cols_disp = df_up_don.columns.tolist()
                c_caja_auto = next((c for c in cols_disp if any(k in c.lower() for k in ["caja", "box", "bulto", "contenedor", "lote", "paquete"])), cols_disp[0])
                c_cont_auto = next((c for c in cols_disp if any(k in c.lower() for k in ["contenido", "farmaco", "fármaco", "medicamento", "producto", "insumo", "nombre", "item", "descrip"])), cols_disp[1] if len(cols_disp) > 1 else cols_disp[0])
                c_cant_auto = next((c for c in cols_disp if any(k in c.lower() for k in ["cantidad", "cant", "unidades", "stock", "total", "qty", "uds"])), cols_disp[2] if len(cols_disp) > 2 else cols_disp[0])

                col_mc1, col_mc2, col_mc3 = st.columns(3)
                with col_mc1:
                    col_sel_caja = st.selectbox("Columna de Caja:", options=cols_disp, index=cols_disp.index(c_caja_auto), key="sel_col_caja_excel")
                with col_mc2:
                    col_sel_cont = st.selectbox("Columna de Contenido / Medicamento:", options=cols_disp, index=cols_disp.index(c_cont_auto), key="sel_col_cont_excel")
                with col_mc3:
                    col_sel_cant = st.selectbox("Columna de Cantidad:", options=cols_disp, index=cols_disp.index(c_cant_auto), key="sel_col_cant_excel")

                if st.button("💾 Importar Donaciones al Nuevo Stock", type="primary", key="btn_importar_excel_don"):
                    with st.spinner("Importando cajas y medicamentos desde archivo..."):
                        filas_insertar = []
                        ahora_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for _, row in df_up_don.iterrows():
                            nom_caja = str(row.get(col_sel_caja, "Caja Donación")).strip()
                            nom_cont = str(row.get(col_sel_cont, "Insumo")).strip()
                            cant_val = _a_entero(row.get(col_sel_cant, 1), 1)
                            if nom_cont and nom_cont != "nan":
                                filas_insertar.append((nom_caja, nom_cont, cant_val, "Medicamento", "", ahora_txt, f"Archivo: {arch_don_up.name}"))

                        if filas_insertar:
                            with obtener_conexion_bd() as conn:
                                cursor = conn.cursor()
                                cursor.executemany("""
                                    INSERT INTO stock_donaciones (caja, contenido, cantidad, tipo_item, lote_vencimiento, fecha_registro, origen)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                """, filas_insertar)
                            cargar_stock_donaciones.clear()
                            st.success(f"¡Éxito! Se importaron {len(filas_insertar)} registros de donaciones correctamente.")
                            st.rerun()

    # 4. MANUAL
    with subtab_manual:
        st.markdown("##### ✍️ Registro Rápido de Caja")
        with st.form("form_registro_manual_stock", clear_on_submit=True):
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                man_caja = st.text_input("📦 N° o Nombre de la Caja:", placeholder="Ej: Caja 05 - Soluciones", key="man_caja_txt")
                man_cont = st.text_input("💊 Contenido (Insumo o Medicamento):", placeholder="Ej: Omeprazol 20mg", key="man_cont_txt")
            with col_m2:
                man_cant = st.number_input("🔢 Cantidad de Unidades:", min_value=1, value=50, key="man_cant_num")
                man_tipo = st.selectbox("🏷️ Tipo de Ítem:", ["Medicamento", "Insumo Médico / Descartable", "Material Quirúrgico", "Nutrición / Suplemento", "Otro"], key="man_tipo_sel")

            man_lote = st.text_input("📅 Lote / Vencimiento (Opcional):", placeholder="Ej: Lote A-102", key="man_lote_txt")

            if st.form_submit_button("💾 Guardar Caja en Nuevo Stock", use_container_width=True):
                if not man_caja.strip():
                    st.error("⚠️ Por favor escribe el N° o Nombre de la Caja.")
                elif not man_cont.strip():
                    st.error("⚠️ Por favor escribe el Contenido de la caja.")
                else:
                    with obtener_conexion_bd() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO stock_donaciones (caja, contenido, cantidad, tipo_item, lote_vencimiento, fecha_registro, origen)
                            VALUES (?, ?, ?, ?, ?, ?, 'Manual')
                        """, (
                            man_caja.strip(),
                            man_cont.strip(),
                            int(man_cant),
                            man_tipo,
                            man_lote.strip(),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ))
                    cargar_stock_donaciones.clear()
                    st.success(f"¡Caja '{man_caja}' registrada con éxito ({man_cant} uds de {man_cont})!")
                    st.rerun()

    # 5. TABLA Y EXCEL
    st.divider()
    df_stock_don = cargar_stock_donaciones()

    col_t_don, col_btn_ref = st.columns([3, 1])
    with col_t_don:
        st.markdown("### 📋 Cuadro de Nuevo Stock Registrado (Cajas y Donaciones)")
    with col_btn_ref:
        if st.button("🔄 Actualizar Cuadro", key="btn_refrescar_tabla_don", use_container_width=True):
            cargar_stock_donaciones.clear()
            st.rerun()

    if not df_stock_don.empty:
        total_cajas_unicas = df_stock_don["caja"].nunique()
        total_items_unicos = df_stock_don["contenido"].nunique()
        total_unidades_sum = df_stock_don["cantidad"].sum()

        col_d1, col_d2, col_d3 = st.columns(3)
        col_d1.metric("📦 Cajas Identificadas", total_cajas_unicas)
        col_d2.metric("💊 Variedad de Insumos/Fármacos", total_items_unicos)
        col_d3.metric("🔢 Total Unidades en Stock", f"{total_unidades_sum:,}")

        col_filtro_c, col_filtro_t = st.columns([2, 1])
        with col_filtro_c:
            buscar_texto = st.text_input("🔍 Buscar por caja o medicamento:", placeholder="Escribe para filtrar...", key="inp_buscar_donaciones")
        with col_filtro_t:
            filtro_tipo_don = st.selectbox("Filtrar por tipo:", ["Todos"] + df_stock_don["tipo_item"].dropna().unique().tolist(), key="sel_filtro_tipo_don")

        df_filtrado_don = df_stock_don.copy()
        if buscar_texto.strip():
            b_low = buscar_texto.strip().lower()
            df_filtrado_don = df_filtrado_don[
                df_filtrado_don["caja"].str.lower().str.contains(b_low) |
                df_filtrado_don["contenido"].str.lower().str.contains(b_low)
            ]
        if filtro_tipo_don != "Todos":
            df_filtrado_don = df_filtrado_don[df_filtrado_don["tipo_item"] == filtro_tipo_don]

        cols_mostrar_don = ["id", "caja", "contenido", "cantidad", "tipo_item", "lote_vencimiento", "fecha_registro", "origen"]
        st.dataframe(df_filtrado_don[cols_mostrar_don], use_container_width=True, hide_index=True)

        resumen_cajas = df_stock_don.groupby(["caja", "contenido", "tipo_item"])["cantidad"].sum().reset_index()
        resumen_insumos = df_stock_don.groupby("contenido")["cantidad"].sum().sort_values(ascending=False).reset_index()

        excel_stock_bytes = df_a_excel_bytes({
            "Detalle_Cajas": df_stock_don[cols_mostrar_don],
            "Resumen_Por_Caja": resumen_cajas,
            "Total_Por_Insumo": resumen_insumos
        })

        col_dl_don, col_del_don = st.columns([2, 1])
        with col_dl_don:
            st.download_button(
                label="📥 Descargar Excel de Nuevo Stock (Multi-hoja)",
                data=excel_stock_bytes,
                file_name=f"Nuevo_Stock_Cajas_{datetime.now():%Y%m%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="btn_dl_excel_donaciones",
                use_container_width=True
            )

        with col_del_don:
            with st.expander("⚙️ Opciones de vaciado"):
                clave_v_stk = st.text_input("Clave de acceso autorizada:", type="password", key="inp_clave_vaciar_stk")
                conf_v_stk = st.text_input("Escribe 'BORRAR' para confirmar:", key="inp_conf_palabra_vstk")
                if st.button("🗑️ Vaciar Stock", key="btn_vaciar_donaciones_total", use_container_width=True):
                    h_coord, h_campo = obtener_hashes_roles()
                    valida = (h_coord and verificar_password_pbkdf2(clave_v_stk, h_coord)) or \
                             (h_campo and verificar_password_pbkdf2(clave_v_stk, h_campo))
                    if (conf_v_stk or "").strip().upper() != "BORRAR":
                        st.error("⚠️ Debes escribir exactamente la palabra 'BORRAR'.")
                    elif not valida:
                        st.error("❌ Clave de acceso incorrecta.")
                        registrar_auditoria("REAUTENTICACION_FALLIDA", "Fallo al autorizar vaciado de stock")
                    else:
                        try:
                            vaciar_stock()
                            cargar_stock_donaciones.clear()
                            registrar_auditoria("VACIAR_STOCK", "Vaciado total de inventario y archivado de movimientos")
                            st.success("Inventario de nuevo stock vaciado y movimientos previos archivados.")
                            st.rerun()
                        except Exception as e_vstk:
                            logger.error(f"Error al vaciar stock: {e_vstk}", exc_info=True)
                            st.error("Ocurrió un error seguro al vaciar el inventario. Consulta los logs.")
    else:
        st.info("📦 Aún no hay nuevo stock registrado. Escanea un código QR con la cámara, sube una foto, carga un Excel o usa el registro rápido arriba.")
