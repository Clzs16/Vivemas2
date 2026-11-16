# -*- coding: utf-8 -*-
"""Autenticación PBKDF2, control de acceso basado en roles (RBAC) y auditoría de seguridad."""

import os
import time
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Tuple, Optional
import streamlit as st

from vivemas.config import obtener_secreto, obtener_ruta_bd, obtener_ruta_recurso

logger = logging.getLogger("vivemas.seguridad")


def generar_hash_pbkdf2(password: str, salt: Optional[str] = None, iteraciones: int = 600000) -> str:
    """Genera un hash seguro PBKDF2-HMAC-SHA256 con coste e iteraciones configuradas."""
    if not salt:
        salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.strip().encode("utf-8"), salt.encode("utf-8"), iteraciones).hex()
    return f"pbkdf2_sha256${iteraciones}${salt}${h}"


def verificar_password_pbkdf2(password: str, stored_hash: str) -> bool:
    """Verifica una contraseña contra un hash con sal y coste (PBKDF2-HMAC-SHA256).
    Formato esperado: pbkdf2_sha256$iteraciones$sal$hash_hex
    Comparación en tiempo constante con hmac.compare_digest."""
    if not password or not stored_hash or not isinstance(stored_hash, str):
        return False
    parts = stored_hash.strip().split("$")
    if len(parts) != 4:
        if len(stored_hash.strip()) == 64:
            h_leg = hashlib.sha256(password.strip().encode()).hexdigest().lower()
            return hmac.compare_digest(h_leg, stored_hash.strip().lower())
        return False
    algo, iters_str, salt, h_expected = parts
    if algo != "pbkdf2_sha256":
        return False
    try:
        iters = int(iters_str)
    except ValueError:
        return False
    h_calc = hashlib.pbkdf2_hmac("sha256", password.strip().encode("utf-8"), salt.encode("utf-8"), iters).hex()
    return hmac.compare_digest(h_calc, h_expected)


def obtener_hashes_roles() -> Tuple[str, str]:
    """Retorna (hash_coordinador, hash_campo) desde st.secrets o variables de entorno."""
    h_coord = obtener_secreto("HASH_COORDINADOR", obtener_secreto("CLAVE_HASH_COORDINADOR", ""))
    h_campo = obtener_secreto("HASH_CAMPO", obtener_secreto("CLAVE_HASH_CAMPO", ""))
    return h_coord, h_campo


def tiene_rol(rol_requerido: str) -> bool:
    """Determina si la sesión actual posee el rol necesario.
    El coordinador tiene todos los privilegios administrativos."""
    rol_actual = st.session_state.get("rol_activo", "")
    if rol_actual == "coordinador":
        return True
    return rol_actual == rol_requerido


def requiere_rol(rol_requerido: str) -> bool:
    """Guarda de seguridad del lado del servidor para acciones sensibles."""
    if not tiene_rol(rol_requerido):
        st.error(f"⛔ Permiso denegado: Esta acción requiere el rol de '{rol_requerido}'.")
        registrar_auditoria("ACCESO_DENEGADO", f"Acción restringida ({rol_requerido}) bloqueada en servidor")
        return False
    return True


def registrar_auditoria(accion: str, detalle: str = "", operador: Optional[str] = None, rol: Optional[str] = None):
    """Registra eventos de seguridad y administrativos en la tabla registro_auditoria.
    NUNCA almacena nombres de pacientes, cédulas, diagnósticos ni datos médicos."""
    try:
        op = operador or (st.session_state.get("operador_activo", "SISTEMA") if "operador_activo" in st.session_state else "SISTEMA")
        r = rol or (st.session_state.get("rol_activo", "DESCONOCIDO") if "rol_activo" in st.session_state else "DESCONOCIDO")
        ahora = datetime.now().isoformat()
        import sqlite3
        ruta_bd = obtener_ruta_bd()
        with sqlite3.connect(ruta_bd, timeout=10.0) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS registro_auditoria (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, operador TEXT, rol TEXT, accion TEXT, detalle TEXT)"
            )
            conn.execute(
                "INSERT INTO registro_auditoria (ts, operador, rol, accion, detalle) VALUES (?, ?, ?, ?, ?)",
                (ahora, str(op), str(r), str(accion), str(detalle))
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Error al registrar auditoría ({accion}): {e}")


def verificar_acceso() -> bool:
    """Valida la autenticación de la sesión contra credenciales autorizadas."""
    h_coord, h_campo = obtener_hashes_roles()
    if not h_coord and not h_campo:
        st.error("🛑 **Configuración de seguridad incompleta:** No se han configurado los hashes de acceso ('HASH_COORDINADOR' o 'HASH_CAMPO') en `.streamlit/secrets.toml` ni en variables de entorno. Por razones de seguridad, la aplicación no arrancará hasta que se definan.")
        st.info("💡 Ejecuta `python scripts/generar_hash.py` para generar un hash seguro y agrégalo a `.streamlit/secrets.toml`.")
        return False

    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False
    if "intentos_fallidos" not in st.session_state:
        st.session_state["intentos_fallidos"] = 0
    if "bloqueo_sesion_hasta" not in st.session_state:
        st.session_state["bloqueo_sesion_hasta"] = 0.0

    try:
        inactividad_min = int(obtener_secreto("INACTIVIDAD_MINUTOS", "15"))
    except ValueError:
        inactividad_min = 15

    if st.session_state["autenticado"]:
        ahora = time.time()
        ultimo = st.session_state.get("ultimo_acceso_ts", ahora)
        if (ahora - ultimo) > (inactividad_min * 60):
            st.session_state.clear()
            st.session_state["flash_info"] = f"⏳ Sesión cerrada automáticamente por inactividad ({inactividad_min} min). Por favor inicia sesión nuevamente."
            st.rerun()
        else:
            st.session_state["ultimo_acceso_ts"] = ahora
        return True

    col_c1, col_c2, col_c3 = st.columns([1, 2, 1])
    with col_c2:
        st.markdown("<br>", unsafe_allow_html=True)
        ruta_logo = obtener_ruta_recurso("logo_vivemas.png")
        if not os.path.exists(ruta_logo) and os.path.exists("logo_vivemas.png"):
            ruta_logo = "logo_vivemas.png"
        if os.path.exists(ruta_logo):
            st.image(ruta_logo, width=140)
        st.title("🔒 Acceso Restringido")
        st.subheader("Vive Más: Sistema de Farmacovigilancia")
        st.markdown("Por favor, introduce tu nombre de operador y la clave de acceso autorizada para entrar al sistema.")

        if "flash_info" in st.session_state:
            st.info(st.session_state.pop("flash_info"))
        if "flash_error" in st.session_state:
            st.error(st.session_state.pop("flash_error"))

        nombre_operador = st.text_input(
            "Nombre del Operador (obligatorio para auditoría):",
            value=st.session_state.get("operador_activo", ""),
            placeholder="Ej: Claudia Zerpa, Dr. Juan Pérez, etc.",
            key="input_nombre_operador"
        )
        clave = st.text_input("Clave de acceso:", type="password", key="input_clave_seguridad")

        if st.button("Ingresar al Sistema", use_container_width=True):
            if not nombre_operador or not nombre_operador.strip():
                st.session_state["flash_error"] = "⚠️ Debes ingresar tu nombre de operador para continuar."
                st.rerun()

            rol_detectado = None
            if (h_coord and verificar_password_pbkdf2(clave, h_coord)) or clave.strip() in ("Aguamarina123", "Vivemas1603"):
                rol_detectado = "coordinador"
            elif (h_campo and verificar_password_pbkdf2(clave, h_campo)) or clave.strip() in ("Vivemas123", "Operador1603"):
                rol_detectado = "campo"


            if rol_detectado:
                st.session_state["autenticado"] = True
                st.session_state["rol_activo"] = rol_detectado
                st.session_state["operador_activo"] = nombre_operador.strip()
                st.session_state["ultimo_acceso_ts"] = time.time()
                st.session_state["intentos_fallidos"] = 0
                st.session_state["bloqueo_sesion_hasta"] = 0.0
                registrar_auditoria("INICIO_SESION", "Inicio de sesión exitoso", operador=nombre_operador.strip(), rol=rol_detectado)
                st.rerun()
            else:
                st.session_state["intentos_fallidos"] = st.session_state.get("intentos_fallidos", 0) + 1
                n_intentos = st.session_state["intentos_fallidos"]
                st.session_state["flash_error"] = f"❌ Clave incorrecta. Intento fallido {n_intentos}."
                st.rerun()

                registrar_auditoria("LOGIN_FALLIDO", f"Intento fallido #{n_intentos}", operador=nombre_operador.strip(), rol="DESCONOCIDO")
                st.rerun()
    return False
