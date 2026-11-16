# -*- coding: utf-8 -*-
"""Barra lateral: red local, jornada activa, tema de pantalla, credenciales y sincronización."""

import os
import json
import logging
from datetime import datetime
import streamlit as st

from vivemas.config import (
    SS,
    obtener_ip_local,
    obtener_secreto,
    obtener_ruta_firebase,
    obtener_ruta_bd,
    obtener_ruta_recurso,
)
from vivemas.db import (
    obtener_config,
    guardar_config,
    crear_respaldo_bd,
    obtener_conexion_bd,
)
from vivemas.seguridad import (
    tiene_rol,
    requiere_rol,
    registrar_auditoria,
)
from vivemas.sync_firebase import ejecutar_sincronizacion_pendientes
from vivemas.ia_gemini import (
    resolver_api_key_gemini,
    obtener_origen_api_key_gemini,
    guardar_api_key_gemini_permanente,
)

logger = logging.getLogger("vivemas.ui.sidebar")


def render_sidebar():
    """Renderiza la barra lateral con todas las opciones operativas y de configuración."""
    with st.sidebar:
        ruta_logo_sb = obtener_ruta_recurso("logo_vivemas.png")
        if not os.path.exists(ruta_logo_sb):
            for cand in ["logo_vivemas.png", os.path.join("dist", "logo_vivemas.png")]:
                if os.path.exists(cand):
                    ruta_logo_sb = os.path.abspath(cand)
                    break
        if os.path.exists(ruta_logo_sb):
            st.image(ruta_logo_sb, use_container_width=True)

        st.header("📡 Servidor Red Local (Offline)")
        ip_servidor = obtener_ip_local()
        st.info(
            f"**Modo Hotspot / LAN Activo** 🟢\n\n"
            f"Comparte esta dirección con las demás laptops y teléfonos:\n"
            f"👉 **`http://{ip_servidor}:8501`**\n\n"
            f"*(En esta laptop anfitriona:* `http://localhost:8501`*)*"
        )
        st.caption("🛡️ **Autoguardado local activo:** Respaldo automático cada 10 min en segundo plano.")

        # Selector de Tema / Visualización (U1)
        tema_actual = st.session_state.get(SS.TEMA, "oscuro")
        opciones_tema = {
            "oscuro": "🌙 Modo Oscuro",
            "luz_baja": "🔴 Luz Baja (Nocturno Campo)",
            "alto_contraste": "☀️ Alto Contraste (Sol)"
        }
        nuevo_tema = st.selectbox(
            "🎨 Modo de Pantalla:",
            options=list(opciones_tema.keys()),
            format_func=lambda k: opciones_tema[k],
            index=list(opciones_tema.keys()).index(tema_actual) if tema_actual in opciones_tema else 0,
            key="sb_selector_tema",
            help="Selecciona 'Luz Baja' para trabajar de noche en campo sin deslumbrar o 'Alto Contraste' bajo el sol."
        )
        if nuevo_tema != tema_actual:
            st.session_state[SS.TEMA] = nuevo_tema
            st.rerun()

        # Toggle de Modo Offline (U5)
        modo_off = st.checkbox(
            "📴 Forzar Modo Offline",
            value=st.session_state.get(SS.MODO_OFFLINE, False),
            key="sb_modo_offline",
            help="Desactiva consultas a la nube para evitar retrasos si la señal celular es inestable."
        )
        st.session_state[SS.MODO_OFFLINE] = modo_off

        st.divider()
        st.header("🏷️ Jornada en Vivo Actual")
        st.caption("Configura el **Lugar** y la **Fecha** para clasificar automáticamente todos los registros:")

        if "lista_zonas" not in st.session_state:
            try:
                st.session_state["lista_zonas"] = json.loads(obtener_config("lista_zonas", "[]"))
            except Exception:
                st.session_state["lista_zonas"] = ["La Guaira"]

        if "lugar_jornada_activa" not in st.session_state:
            lugar_ini_global = obtener_config("lugar_jornada_activa", st.session_state["lista_zonas"][0] if st.session_state["lista_zonas"] else "La Guaira")
            st.session_state["lugar_jornada_activa"] = lugar_ini_global
            st.session_state["zona_activa"] = lugar_ini_global

        if "fecha_jornada_activa" not in st.session_state:
            st.session_state["fecha_jornada_activa"] = datetime.today().strftime("%Y-%m-%d")

        def _al_cambiar_lugar_jornada():
            nuevo_sel = st.session_state.get("sb_lugar_jornada")
            if nuevo_sel and nuevo_sel != "➕ Agregar nuevo lugar...":
                st.session_state["lugar_jornada_activa"] = nuevo_sel
                st.session_state["zona_activa"] = nuevo_sel
                guardar_config("lugar_jornada_activa", nuevo_sel)

        def _al_cambiar_fecha_jornada():
            nueva_f = st.session_state.get("sb_fecha_jornada")
            if nueva_f:
                st.session_state["fecha_jornada_activa"] = nueva_f.strftime("%Y-%m-%d")

        opciones_zona_sb = st.session_state["lista_zonas"] + ["➕ Agregar nuevo lugar..."]
        lugar_sesion = st.session_state["lugar_jornada_activa"]
        lugar_idx = opciones_zona_sb.index(lugar_sesion) if lugar_sesion in opciones_zona_sb else 0

        lugar_jornada_sel = st.selectbox(
            "📍 Lugar de la Jornada:",
            options=opciones_zona_sb,
            index=lugar_idx,
            key="sb_lugar_jornada",
            on_change=_al_cambiar_lugar_jornada
        )
        if lugar_jornada_sel == "➕ Agregar nuevo lugar...":
            nuevo_lugar_sb = st.text_input("✍️ Escribe el nuevo lugar:", key="sb_nuevo_lugar_txt")
            if nuevo_lugar_sb.strip():
                lugar_activo_final = nuevo_lugar_sb.strip()
                st.session_state["lugar_jornada_activa"] = lugar_activo_final
                st.session_state["zona_activa"] = lugar_activo_final
                if lugar_activo_final not in st.session_state["lista_zonas"]:
                    st.session_state["lista_zonas"].append(lugar_activo_final)
                    guardar_config("lista_zonas", json.dumps(st.session_state["lista_zonas"]))
                    guardar_config("lugar_jornada_activa", lugar_activo_final)
            else:
                lugar_activo_final = st.session_state["lugar_jornada_activa"]
        else:
            lugar_activo_final = st.session_state["lugar_jornada_activa"]

        try:
            val_fecha_activa = datetime.strptime(st.session_state["fecha_jornada_activa"], "%Y-%m-%d").date()
        except Exception:
            val_fecha_activa = datetime.today().date()

        fecha_jornada_activa = st.date_input(
            "📅 Fecha de la Jornada:",
            value=val_fecha_activa,
            key="sb_fecha_jornada",
            on_change=_al_cambiar_fecha_jornada
        )
        fecha_jornada_activa_str = st.session_state["fecha_jornada_activa"]

        nombre_jornada_auto = f"Jornada - {lugar_activo_final} - {fecha_jornada_activa_str}"
        st.session_state["nombre_jornada_activa"] = nombre_jornada_auto
        st.session_state["zona_activa"] = lugar_activo_final

        st.success(f"🏷️ **Identificador:**\n`{nombre_jornada_auto}`")

        st.divider()
        st.markdown(f"👤 **Operador:** `{st.session_state.get('operador_activo', 'Personal')}` &nbsp;•&nbsp; 🏷️ **Rol:** `{st.session_state.get('rol_activo', 'campo').upper()}`")
        if st.button("🚪 Cerrar Sesión", use_container_width=True, key="btn_cerrar_sesion_sidebar"):
            op_sal = st.session_state.get("operador_activo", "DESCONOCIDO")
            rol_sal = st.session_state.get("rol_activo", "DESCONOCIDO")
            st.session_state.clear()
            registrar_auditoria("CIERRE_SESION", "Cierre manual de sesión", operador=op_sal, rol=rol_sal)
            st.rerun()

        # Respaldo manual rápido
        if st.button("💾 Crear Respaldo Inmediato", use_container_width=True, key="btn_respaldo_manual_sb"):
            res = crear_respaldo_bd(max_respaldos=30, etiqueta="manual")
            if res:
                ahora_fmt = datetime.now().strftime("%H:%M:%S")
                st.session_state[SS.ULTIMO_RESPALDO] = ahora_fmt
                registrar_auditoria("RESPALDO_MANUAL", f"Respaldo generado en {res}")
                st.success(f"¡Respaldo seguro generado con éxito! ({ahora_fmt})")
            else:
                st.error("No se pudo generar el respaldo manual.")

        st.header("⚙️ Configuración")
        api_key_almacenada = resolver_api_key_gemini()
        es_coordinador = tiene_rol("coordinador")

        if api_key_almacenada:
            st.caption("🤖 **Gemini IA:** 🟢 Activa permanentemente")
        else:
            st.caption("🤖 **Gemini IA:** 🔴 No configurada")

        with st.expander("🛠️ Ajustes del Sistema", expanded=False):
            if es_coordinador:
                st.markdown("##### 🔑 Configuración Gemini IA")
                st.caption("Modificar la clave de API es una función exclusiva del Coordinador.")
                nuevo_api_key = st.text_input(
                    "Nueva Gemini API Key:",
                    type="password",
                    placeholder="Escribe o pega la clave para actualizarla...",
                    key="input_gemini_api_key_coord"
                )
                if st.button("💾 Guardar API Key Permanente", key="btn_guardar_gemini_coord", use_container_width=True):
                    if nuevo_api_key.strip():
                        if guardar_api_key_gemini_permanente(nuevo_api_key.strip()):
                            st.session_state["gemini_api_key_sesion"] = nuevo_api_key.strip()
                            registrar_auditoria("CONFIG_GEMINI_KEY", "Coordinador actualizó la API Key de Gemini permanente")
                            st.success("¡Clave de Gemini guardada de forma permanente y activa en el sistema!")
                            st.rerun()
                        else:
                            st.error("No se pudo guardar la clave permanentemente.")
                    else:
                        st.warning("Por favor escribe una clave válida.")
                st.markdown("---")
                st.markdown("##### ☁️ Credenciales de Firebase Firestore")
                ruta_fb_actual = obtener_ruta_firebase()
                if ruta_fb_actual and os.path.exists(ruta_fb_actual):
                    st.caption("🟢 **Credenciales de Firebase activas permanentemente.** No es necesario volver a subirlas.")
                else:
                    st.caption("⚠️ No se detectó 'firebase_key.json'. Sube el archivo para dejarlo activo permanentemente.")

                with st.expander("🔄 Reemplazar archivo 'firebase_key.json' (Solo si cambias de proyecto)", expanded=False):
                    subir_fb_json = st.file_uploader("Seleccionar nuevo JSON de Firebase:", type=["json"], key="up_fb_json_sidebar")
                    if subir_fb_json is not None:
                        if st.button("💾 Aplicar Nueva Clave Permanentemente", key="btn_guardar_fb_json"):
                            try:
                                contenido_json = subir_fb_json.getvalue()
                                if len(contenido_json) > 10 * 1024 * 1024:
                                    st.error("❌ El archivo supera el tamaño máximo permitido (10 MB).")
                                    st.stop()
                                datos_val = json.loads(contenido_json.decode("utf-8"))
                                campos_req = ["type", "project_id", "private_key_id", "private_key", "client_email"]
                                if not all(k in datos_val for k in campos_req) or datos_val.get("type") != "service_account":
                                    st.error("❌ Archivo de credenciales inválido: no coincide con el esquema oficial de Google Service Account.")
                                else:
                                    for r_dest in [
                                        os.path.join(os.getcwd(), "firebase_key.json"),
                                        os.path.join(os.getcwd(), "dist", "firebase_key.json")
                                    ]:
                                        try:
                                            with open(r_dest, "wb") as f_fb:
                                                f_fb.write(contenido_json)
                                            os.chmod(r_dest, 0o600)
                                        except Exception:
                                            pass
                                    import firebase_admin
                                    for a_name in list(firebase_admin._apps.keys()):
                                        try:
                                            firebase_admin.delete_app(firebase_admin._apps[a_name])
                                        except Exception:
                                            pass
                                    registrar_auditoria("ACTUALIZAR_FIREBASE_KEY", f"Credenciales actualizadas para proyecto {datos_val.get('project_id')}")
                                    st.success(f"¡Credenciales de Firebase actualizadas permanentemente para `{datos_val.get('project_id')}`!")
                                    st.rerun()
                            except json.JSONDecodeError:
                                st.error("❌ El archivo proporcionado no es un JSON válido.")
                            except Exception as err_k:
                                logger.error(f"Error procesando credencial de Firebase: {err_k}")
                                st.error("❌ Ocurrió un error al procesar el archivo. Consulta los logs del sistema.")
            else:
                st.info("🔒 *La configuración de claves de Gemini y Firebase está reservada exclusivamente al Coordinador.*")
                ruta_fb_actual = obtener_ruta_firebase()
                if ruta_fb_actual and os.path.exists(ruta_fb_actual):
                    st.caption("🟢 **Firebase Firestore:** Conectado y permanente")

        col_st_ia, col_st_fb = st.columns(2)
        with col_st_ia:
            if api_key_almacenada:
                st.caption("🟢 **IA Activa**")
            else:
                st.caption("🔴 **Falta Gemini Key**")
        with col_st_fb:
            ruta_fb_actual = obtener_ruta_firebase()
            if os.path.exists(ruta_fb_actual):
                st.caption("🟢 **Firebase Listo**")
            else:
                st.caption("🔴 **Falta Firebase Key**")

        st.divider()
        st.header("🌐 Sincronización Cloud")
        if st.button("🔄 Sincronizar Pendientes a Firebase", key="btn_sync_sidebar", use_container_width=True):
            with st.spinner("Sincronizando con Firebase Firestore..."):
                ok_sync, msg_sync = ejecutar_sincronizacion_pendientes()
                if ok_sync:
                    registrar_auditoria("SINCRONIZACION_NUBE", "Sincronización manual de pendientes exitosa")
                    st.success(msg_sync)
                    st.rerun()
                else:
                    st.error(msg_sync)
                    fb_pid = obtener_secreto("FIREBASE_PROJECT_ID", "tu-proyecto-firebase")
                    if "invalid_grant" in msg_sync or "revocada" in msg_sync.lower():
                        st.info(
                            "💡 **Cómo generar una nueva clave:**\n"
                            f"1. Entra a [Firebase Console](https://console.firebase.google.com/) > Proyecto **`{fb_pid}`**.\n"
                            "2. Ve a ⚙️ Configuración del proyecto > **Cuentas de servicio**.\n"
                            "3. Clic en **'Generar nueva clave privada'**.\n"
                            "4. Sube el `.json` en **🛠️ Ajustes del Sistema** arriba."
                        )
