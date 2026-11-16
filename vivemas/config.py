# -*- coding: utf-8 -*-
"""Configuraciones, constantes y estado de sesión de Vive Más."""

import os
import sys
import socket
from pathlib import Path
import streamlit as st


class SS:
    """Constantes para claves de st.session_state (centralización de estado)."""
    AUTENTICADO = "autenticado"
    ROL = "rol_usuario"
    USUARIO_ACTUAL = "usuario_actual"
    ZONA_ACTIVA = "zona_activa"
    FECHA_JORNADA_ACTIVA = "fecha_jornada_activa"
    NOMBRE_JORNADA_ACTIVA = "nombre_jornada_activa"
    JORNADA_CONFIRMADA = "jornada_confirmada"
    TEMA = "app_tema"  # 'oscuro', 'luz_baja', 'alto_contraste'
    MODO_OFFLINE = "modo_offline"
    ULTIMO_RESPALDO = "ultimo_respaldo"
    COLA_SYNC = "cola_sync_pendientes"
    FILAS_POR_PAGINA = "filas_por_pagina"
    REGISTRO_MULTIPLE = "registro_multiple_farmacos"
    FORM_RESET_KEY = "form_reset_key"
    MENSAJE_EXITO = "mensaje_exito_temporal"
    ALERTAS_UI = "alertas_ui"


NOMBRE_BD_LOCAL = "jornada_salud.db"
CARPETA_RESPALDOS = "respaldos_bd"
MAX_COPIAS_SEGURIDAD = 30
VERSION_ESQUEMA_ACTUAL = 5

DEFAULT_MODELOS_GEMINI = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

LISTA_FARMACOS_COMUNES = [
    "Paracetamol 500mg",
    "Ibuprofeno 400mg",
    "Amoxicilina 500mg",
    "Losartán 50mg",
    "Enalapril 20mg",
    "Amlodipino 10mg",
    "Metformina 850mg",
    "Glibenclamida 5mg",
    "Omeprazol 20mg",
    "Loratadina 10mg",
    "Cetirizina 10mg",
    "Salbutamol Inhalador",
    "Diclofenac 50mg",
    "Ácido Fólico 5mg",
    "Complejo B Comprimidos",
    "Suero Oral Sobres",
    "Azitromicina 500mg",
    "Ciprofloxacina 500mg",
    "Atorvastatina 20mg",
    "Multivitamínico Pediátrico",
]

ZONAS_PREDETERMINADAS = [
    "La Guaira",
    "Caraballeda",
    "Catia La Mar",
    "Maiquetía",
    "Macuto",
    "Naiguatá",
    "Carayaca",
    "El Junko",
    "Urimare",
    "Carlos Soublette",
    "Caruao",
]


def obtener_secreto(nombre: str, default: str = "") -> str:
    """Busca un secreto en secrets.toml en disco, st.secrets, BD SQLite o entorno."""
    # 1. Leer directamente del archivo secrets.toml en disco (refresco inmediato sin reiniciar Streamlit)
    for ruta_s in [
        os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
        os.path.join(os.getcwd(), "dist", ".streamlit", "secrets.toml"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".streamlit", "secrets.toml"),
        os.path.join(os.path.expanduser("~"), ".streamlit", "secrets.toml"),
    ]:
        if os.path.isfile(ruta_s):
            try:
                with open(ruta_s, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(nombre) and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() == nombre:
                                val = v.strip().strip('"').strip("'")
                                if val:
                                    return val
            except Exception:
                pass

    # 2. st.secrets en memoria
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            val = str(st.secrets[nombre]).strip()
            if val:
                return val
    except Exception:
        pass

    # 3. Base de datos SQLite
    try:
        from vivemas.db import obtener_config
        val_db = obtener_config(nombre.lower(), "")
        if val_db and str(val_db).strip():
            return str(val_db).strip()
    except Exception:
        pass

    return os.environ.get(nombre, default).strip()



def obtener_ruta_recurso(nombre_archivo: str) -> str:
    """Obtiene la ruta de un recurso empaquetado o en desarrollo."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, nombre_archivo)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), nombre_archivo)


def obtener_ruta_bd() -> str:
    """Retorna la ruta absoluta a la base de datos SQLite."""
    return os.path.join(os.getcwd(), NOMBRE_BD_LOCAL)


def obtener_ruta_firebase() -> str:
    """Busca el archivo de credenciales de Firebase en ubicaciones seguras."""
    # 1. Variable de entorno FIREBASE_KEY_PATH
    env_path = os.environ.get("FIREBASE_KEY_PATH", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    
    # 2. En st.secrets
    try:
        if hasattr(st, "secrets") and "FIREBASE_KEY_PATH" in st.secrets:
            sec_path = str(st.secrets["FIREBASE_KEY_PATH"]).strip()
            if os.path.isfile(sec_path):
                return sec_path
    except Exception:
        pass

    # 3. Ubicaciones locales relativas, empaquetadas y del proyecto
    candidatos = [
        "firebase_key.json",
        os.path.join("..", "firebase_key.json"),
        os.path.join("dist", "firebase_key.json"),
        obtener_ruta_recurso("firebase_key.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firebase_key.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebase_key.json"),
        "secrets/firebase_key.json",
        ".streamlit/firebase_key.json",
        os.path.join(os.path.expanduser("~"), ".vivemas", "firebase_key.json"),
    ]
    for p in candidatos:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return ""


def obtener_ip_local() -> str:
    """Detecta la IP local de la laptop para compartir en red (LAN/Hotspot)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # No realiza conexión real hacia afuera, solo consulta la interfaz de enrutamiento
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def init_estado():
    """Inicializa de forma centralizada las variables de st.session_state."""
    valores_defecto = {
        SS.AUTENTICADO: False,
        SS.ROL: None,
        SS.USUARIO_ACTUAL: "Operador",
        SS.ZONA_ACTIVA: "La Guaira",
        SS.FECHA_JORNADA_ACTIVA: None,
        SS.NOMBRE_JORNADA_ACTIVA: "",
        SS.JORNADA_CONFIRMADA: False,
        SS.TEMA: "oscuro",
        SS.MODO_OFFLINE: False,
        SS.ULTIMO_RESPALDO: "Sin respaldos recientes",
        SS.COLA_SYNC: 0,
        SS.FILAS_POR_PAGINA: 15,
        SS.FORM_RESET_KEY: 0,
        SS.REGISTRO_MULTIPLE: [],
    }
    for k, v in valores_defecto.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Limpieza preventiva de claves de widgets obsoletas
    if "api_key_input" in st.session_state:
        try:
            del st.session_state["api_key_input"]
        except Exception:
            pass
