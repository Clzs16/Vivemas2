# -*- coding: utf-8 -*-
"""Capa de datos SQLite, migraciones versionadas (PRAGMA user_version) y respaldos cifrados."""

import os
import sqlite3
import shutil
import threading
import time
import uuid
import json
import logging
from datetime import datetime
from typing import Optional
try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None
import pandas as pd
import streamlit as st

from vivemas.config import (
    NOMBRE_BD_LOCAL,
    VERSION_ESQUEMA_ACTUAL,
    obtener_secreto,
    obtener_ruta_bd,
    LISTA_FARMACOS_COMUNES,
    ZONAS_PREDETERMINADAS,
)
from vivemas.textos import normalizar_nombre_farmaco

logger = logging.getLogger("vivemas.db")


def conectar_bd(solo_lectura: bool = False, timeout: float = 30.0) -> sqlite3.Connection:
    """Abre conexión optimizada para concurrencia local (WAL, timeout, foreign keys)."""
    ruta = obtener_ruta_bd()
    if solo_lectura:
        uri = f"file:{os.path.abspath(ruta)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(ruta, timeout=timeout)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def obtener_conexion_bd() -> sqlite3.Connection:
    """Alias compatible con app.py para obtener conexión con WAL y timeout."""
    return conectar_bd(solo_lectura=False)


def obtener_version_bd(conn: sqlite3.Connection) -> int:
    """Consulta la versión del esquema almacenada en SQLite."""
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def establecer_version_bd(conn: sqlite3.Connection, version: int):
    """Actualiza la versión del esquema en SQLite."""
    conn.execute(f"PRAGMA user_version = {int(version)};")


def _aplicar_esquema_bd(conn: sqlite3.Connection):
    """Aplica migraciones versionadas e idempotentes protegiendo bases de datos existentes."""
    cursor = conn.cursor()

    # MIGRACIÓN 1: Tablas base
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_jornada (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            zona TEXT,
            paciente_id TEXT,
            edad INTEGER,
            diagnostico TEXT,
            farmaco_entregado TEXT,
            cantidad_entregada INTEGER,
            sincronizado INTEGER DEFAULT 0,
            uuid TEXT,
            nombre_jornada TEXT,
            tipo_registro TEXT DEFAULT 'en_vivo'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_historicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            zona TEXT,
            paciente_id TEXT,
            edad INTEGER,
            diagnostico TEXT,
            farmaco_entregado TEXT,
            cantidad_entregada INTEGER,
            archivo_origen TEXT,
            nombre_jornada TEXT,
            tipo_registro TEXT DEFAULT 'historico_excel'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracion (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS control_seguridad (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registro_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            operador TEXT,
            rol TEXT,
            accion TEXT,
            detalle TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auditoria_ts ON registro_auditoria(ts)")
    
    # Eliminación de posibles claves de API residuales en BD
    try:
        cursor.execute("DELETE FROM configuracion WHERE clave = 'gemini_api_key'")
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros_eliminados (
            uuid TEXT PRIMARY KEY,
            fecha_eliminacion TEXT,
            sincronizado INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_donaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caja TEXT,
            contenido TEXT,
            cantidad INTEGER,
            tipo_item TEXT DEFAULT 'Medicamento',
            lote_vencimiento TEXT,
            fecha_registro TEXT,
            origen TEXT DEFAULT 'Manual'
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stock_caja ON stock_donaciones(caja)")

    # MIGRACIÓN 2: Columnas e índices de sincronización y uuid
    try:
        cursor.execute("ALTER TABLE registros_jornada ADD COLUMN sincronizado INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE registros_jornada ADD COLUMN uuid TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE registros_jornada ADD COLUMN nombre_jornada TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE registros_jornada ADD COLUMN tipo_registro TEXT DEFAULT 'en_vivo'")
    except sqlite3.OperationalError:
        pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reg_uuid ON registros_jornada(uuid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reg_sync ON registros_jornada(sincronizado)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jornada_nombre ON registros_jornada(nombre_jornada)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hist_nombre ON registros_historicos(nombre_jornada)")

    # Asignar UUID a registros que no lo tengan
    cursor.execute("SELECT id FROM registros_jornada WHERE uuid IS NULL")
    for (rid,) in cursor.fetchall():
        cursor.execute("UPDATE registros_jornada SET uuid = ? WHERE id = ?", (str(uuid.uuid4()), rid))

    # MIGRACIÓN 3: Tabla movimientos_stock
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movimientos_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            farmaco TEXT,
            farmaco_norm TEXT,
            tipo TEXT,
            cantidad INTEGER,
            ref_uuid TEXT UNIQUE,
            archivado INTEGER DEFAULT 0
        )
    """)
    try:
        cursor.execute("ALTER TABLE movimientos_stock ADD COLUMN archivado INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mov_stock_norm ON movimientos_stock(farmaco_norm)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mov_stock_arch ON movimientos_stock(archivado)")

    # MIGRACIÓN 4: Llenado de entregas previas desde registros_jornada
    cursor.execute("""
        INSERT OR IGNORE INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid, archivado)
        SELECT fecha, farmaco_entregado, NULL, 'entrega', cantidad_entregada, uuid, 0
        FROM registros_jornada
        WHERE uuid IS NOT NULL AND uuid != ''
    """)

    # MIGRACIÓN 5: Reparación e idempotencia de normalización de fármacos
    cursor.execute("SELECT id, farmaco, farmaco_norm FROM movimientos_stock")
    for mid, m_farm, m_norm in cursor.fetchall():
        norm_calc = normalizar_nombre_farmaco(m_farm)
        if m_norm != norm_calc:
            cursor.execute("UPDATE movimientos_stock SET farmaco_norm = ? WHERE id = ?", (norm_calc, mid))

    # Semillas de listas dinámicas
    _zonas_default = json.dumps(ZONAS_PREDETERMINADAS)
    _farmacos_default = json.dumps(LISTA_FARMACOS_COMUNES)
    cursor.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('lista_zonas', ?)", (_zonas_default,))
    cursor.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('lista_farmacos', ?)", (_farmacos_default,))

    try:
        cursor.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_unico ON registros_historicos
            (fecha, zona, paciente_id, diagnostico, farmaco_entregado, cantidad_entregada, archivo_origen)""")
    except sqlite3.IntegrityError:
        pass

    # Establecer la versión actual
    establecer_version_bd(conn, VERSION_ESQUEMA_ACTUAL)
    conn.commit()


def inicializar_bd(conn: Optional[sqlite3.Connection] = None):
    """Inicializa la base de datos local y ejecuta migraciones idempotentes."""
    if conn is not None:
        _aplicar_esquema_bd(conn)
    else:
        ruta = obtener_ruta_bd()
        try:
            _c = sqlite3.connect(ruta, timeout=30.0)
            _c.execute("PRAGMA journal_mode=WAL;")
            _c.close()
        except Exception:
            pass
        with obtener_conexion_bd() as c_local:
            _aplicar_esquema_bd(c_local)


@st.cache_resource
def asegurar_bd_inicializada() -> bool:
    """Ejecuta las migraciones y verificación estructural una única vez al iniciar el servidor."""
    inicializar_bd()
    return True


def guardar_config(clave: str, valor: str):
    """Guarda parámetros de configuración en SQLite."""
    with obtener_conexion_bd() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES (?, ?)", (clave, valor))
        conn.commit()


def obtener_config(clave: str, valor_default: str = "") -> str:
    """Recupera parámetros de configuración de SQLite de forma segura."""
    try:
        with obtener_conexion_bd() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT valor FROM configuracion WHERE clave = ?", (clave,))
            row = cursor.fetchone()
            if row and row[0] and str(row[0]).strip():
                return str(row[0]).strip()
    except Exception as e:
        logger.warning(f"Error consultando configuración para clave '{clave}': {e}")
    return valor_default


def crear_respaldo_bd(max_respaldos: int = 30, etiqueta: str = "") -> Optional[str]:
    """Copia consistente de la BD en la carpeta 'respaldos'.
    Si se ha configurado CLAVE_CIFRADO_BACKUP, cifra el archivo en reposo con AES-128-CBC (Fernet)."""
    ruta_bd = obtener_ruta_bd()
    carpeta = os.path.join(os.path.dirname(os.path.abspath(ruta_bd)), "respaldos")
    os.makedirs(carpeta, exist_ok=True)
    tag = f"_{etiqueta}" if etiqueta else ""
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    clave_cifrado = obtener_secreto("CLAVE_CIFRADO_BACKUP", "")

    extension = ".db.enc" if clave_cifrado else ".db"
    destino = os.path.join(carpeta, f"vive_mas{tag}_{ts_str}{extension}")
    temp_raw = os.path.join(carpeta, f"temp_{ts_str}.db")

    try:
        origen = sqlite3.connect(ruta_bd, timeout=20.0)
        try:
            copia = sqlite3.connect(temp_raw)
            try:
                origen.backup(copia)
            finally:
                copia.close()
        finally:
            origen.close()

        if clave_cifrado:
            if Fernet is None:
                logger.warning("Módulo cryptography no disponible. Guardando respaldo sin cifrar.")
                shutil.copy(temp_raw, destino.replace(".db.enc", ".db"))
                destino = destino.replace(".db.enc", ".db")
            else:
                try:
                    fernet = Fernet(clave_cifrado.encode("utf-8") if isinstance(clave_cifrado, str) else clave_cifrado)
                    with open(temp_raw, "rb") as f_raw:
                        datos = f_raw.read()
                    cifrados = fernet.encrypt(datos)
                    with open(destino, "wb") as f_enc:
                        f_enc.write(cifrados)
                except Exception as e_cif:
                    logger.error(f"Error al cifrar respaldo: {e_cif}. Guardando sin cifrar.")
                    shutil.copy(temp_raw, destino.replace(".db.enc", ".db"))
                    destino = destino.replace(".db.enc", ".db")
        else:
            shutil.copy(temp_raw, destino)

        if os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except Exception:
                pass

        # Conservar solo los últimos respaldos automáticos periódicos (auto10m)
        antiguos_auto = sorted(
            f for f in os.listdir(carpeta)
            if f.startswith("vive_mas_auto10m_") and (f.endswith(".db") or f.endswith(".db.enc"))
        )
        for f in antiguos_auto[:-max_respaldos]:
            try:
                os.remove(os.path.join(carpeta, f))
            except OSError as err_os:
                logger.warning(f"No se pudo eliminar respaldo antiguo '{f}': {err_os}")

        return destino
    except Exception as e:
        logger.error(f"Error generando respaldo de BD ({etiqueta}): {e}", exc_info=True)
        if os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except Exception:
                pass
        return None


def _demonio_respaldo_automatico(intervalo_segundos: int = 600):
    """Hilo en segundo plano: cada 10 minutos genera un respaldo consistente sin detener el registro en vivo."""
    while True:
        time.sleep(intervalo_segundos)
        try:
            res = crear_respaldo_bd(max_respaldos=30, etiqueta="auto10m")
            if not res:
                logger.warning("Fallo al crear el respaldo automático periódico.")
        except Exception as e:
            logger.error(f"Error en hilo de respaldo automático: {e}", exc_info=True)


@st.cache_resource
def iniciar_servicio_autorespaldo() -> bool:
    """Garantiza un único hilo en segundo plano ejecutando respaldos automáticos."""
    hilo = threading.Thread(target=_demonio_respaldo_automatico, args=(600,), daemon=True, name="AutoBackupViveMas")
    hilo.start()
    return True


def cargar_registros_local(solo_jornada_actual: bool = False, nombre_jornada: str = "") -> pd.DataFrame:
    """Carga los registros de atención médica desde la base de datos local."""
    try:
        with obtener_conexion_bd() as conn:
            if solo_jornada_actual and nombre_jornada:
                query = "SELECT * FROM registros_jornada WHERE nombre_jornada = ? ORDER BY id DESC"
                df = pd.read_sql_query(query, conn, params=(nombre_jornada,))
            else:
                query = "SELECT * FROM registros_jornada ORDER BY id DESC"
                df = pd.read_sql_query(query, conn)
            return df
    except Exception as e:
        logger.error(f"Error cargando registros locales: {e}")
        return pd.DataFrame()


def cargar_stock_donaciones() -> pd.DataFrame:
    """Carga el inventario de stock de donaciones ingresado."""
    try:
        with obtener_conexion_bd() as conn:
            return pd.read_sql_query("SELECT * FROM stock_donaciones ORDER BY id DESC", conn)
    except Exception as e:
        logger.error(f"Error cargando stock de donaciones: {e}")
        return pd.DataFrame()


def cargar_registros_historicos(nombre_jornada: Optional[str] = None) -> pd.DataFrame:
    """Carga registros de jornadas anteriores importados desde Excel/CSV."""
    try:
        with obtener_conexion_bd() as conn:
            if nombre_jornada:
                return pd.read_sql_query(
                    "SELECT * FROM registros_historicos WHERE nombre_jornada = ? ORDER BY id DESC",
                    conn,
                    params=(nombre_jornada,)
                )
            return pd.read_sql_query("SELECT * FROM registros_historicos ORDER BY id DESC", conn)
    except Exception as e:
        logger.error(f"Error cargando registros históricos: {e}")
        return pd.DataFrame()


def _dummy_clear():
    """No-op de compatibilidad si no se usa st.cache_data en lecturas directas SQLite."""
    pass


# Compatibilidad con llamadas legacy a .clear() tras inserciones o eliminaciones
cargar_registros_local.clear = _dummy_clear
cargar_stock_donaciones.clear = _dummy_clear
cargar_registros_historicos.clear = _dummy_clear

