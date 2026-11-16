# -*- coding: utf-8 -*-
"""Sincronización con Firebase Firestore, detección de conectividad y gestión de cola en nube."""

import os
import time
import socket
import logging
import uuid
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
import pandas as pd
import streamlit as st

from vivemas.config import obtener_ruta_firebase
from vivemas.db import obtener_conexion_bd, cargar_registros_local

logger = logging.getLogger("vivemas.sync_firebase")
_ultimo_error_firestore = None
_client_cache = None


def hay_conexion_internet(timeout: float = 1.2) -> bool:
    """Comprobación ultra rápida y resiliente de conectividad exterior (DNS y HTTPS fallback)."""
    endpoints = [
        ("1.1.1.1", 53),
        ("8.8.8.8", 53),
        ("www.google.com", 443),
    ]
    for host, port in endpoints:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            continue
    return False


def _a_entero(val: Any, default: int = 0) -> int:
    """Convierte un valor a entero de forma segura."""
    try:
        if val is None or pd.isna(val):
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default


def conectar_firestore(forzar_reconeccion: bool = False):
    """Conecta con Firebase Firestore reutilizando la sesión activa optimizada."""
    global _ultimo_error_firestore, _client_cache
    if _client_cache is not None and not forzar_reconeccion:
        return _client_cache

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        ruta_json = obtener_ruta_firebase()
        if not ruta_json or not os.path.exists(ruta_json):
            _ultimo_error_firestore = "Archivo 'firebase_key.json' no encontrado en el sistema."
            _client_cache = None
            return None

        if not firebase_admin._apps:
            cred = credentials.Certificate(ruta_json)
            firebase_admin.initialize_app(cred)

        db = firestore.client()
        _ultimo_error_firestore = None
        _client_cache = db
        return db
    except Exception as e:
        _ultimo_error_firestore = str(e)
        _client_cache = None
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
            ruta_json = obtener_ruta_firebase()
            if ruta_json and os.path.exists(ruta_json):
                for app_name in list(firebase_admin._apps.keys()):
                    try:
                        firebase_admin.delete_app(firebase_admin._apps[app_name])
                    except Exception:
                        pass
                cred = credentials.Certificate(ruta_json)
                firebase_admin.initialize_app(cred)
                db = firestore.client()
                _ultimo_error_firestore = None
                _client_cache = db
                return db
        except Exception as e2:
            _ultimo_error_firestore = str(e2)
            _client_cache = None
        return None



def contar_pendientes_sincronizacion() -> Tuple[int, int]:
    """Retorna (nuevos_pendientes, eliminados_pendientes) en la base local."""
    try:
        with obtener_conexion_bd() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM registros_jornada WHERE sincronizado = 0")
            n_nuevos = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM registros_eliminados WHERE sincronizado = 0")
            n_dels = cur.fetchone()[0]
            return int(n_nuevos), int(n_dels)
    except Exception:
        return 0, 0


def ejecutar_sincronizacion_pendientes() -> Tuple[bool, str]:
    """Sincroniza registros nuevos y propaga eliminaciones a Firebase Firestore."""
    db_cloud = conectar_firestore()
    if not db_cloud:
        detalle = _ultimo_error_firestore or "No se pudo conectar a Firebase Firestore."
        return False, f"No se pudo conectar a Firebase: {detalle}"

    try:
        coleccion_ref = db_cloud.collection("registros_jornada")
        total_borrados_sync = 0
        total_nuevos_sync = 0

        # 1. PROPAGACIÓN DE ELIMINACIONES PENDIENTES A LA NUBE
        error_propagacion_del = None
        try:
            with obtener_conexion_bd() as conn:
                cur = conn.cursor()
                cur.execute("SELECT uuid FROM registros_eliminados WHERE sincronizado = 0")
                dels_pendientes = [str(r[0]).strip() for r in cur.fetchall() if r[0]]

            if dels_pendientes:
                for i in range(0, len(dels_pendientes), 350):
                    lote_del = dels_pendientes[i:i + 350]
                    batch_del = db_cloud.batch()
                    for uid in lote_del:
                        batch_del.delete(coleccion_ref.document(uid))
                    for intento_d in range(3):
                        try:
                            batch_del.commit(timeout=25)
                            break
                        except Exception as eb_del:
                            if intento_d == 2:
                                raise eb_del
                            time.sleep(1.0 * (intento_d + 1))

                    with obtener_conexion_bd() as conn:
                        conn.executemany(
                            "UPDATE registros_eliminados SET sincronizado = 1 WHERE uuid = ?",
                            [(uid,) for uid in lote_del]
                        )
                    total_borrados_sync += len(lote_del)
        except Exception as e_del:
            error_propagacion_del = str(e_del)
            logger.error(f"Error propagando eliminaciones pendientes a Firebase: {e_del}", exc_info=True)

        # 2. SUBIDA DE NUEVOS REGISTROS PENDIENTES A LA NUBE
        with obtener_conexion_bd() as conn:
            df_pendientes = pd.read_sql(
                "SELECT id, uuid, fecha, zona, paciente_id, edad, diagnostico, farmaco_entregado, cantidad_entregada, nombre_jornada, tipo_registro "
                "FROM registros_jornada WHERE sincronizado = 0",
                conn
            )

            if df_pendientes.empty and total_borrados_sync == 0:
                return True, "No hay registros pendientes por sincronizar. Todos los datos están al día en la nube."

            if not df_pendientes.empty:
                ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                registros = df_pendientes.to_dict("records")
                for i in range(0, len(registros), 350):
                    lote = registros[i:i + 350]
                    batch = db_cloud.batch()
                    for r in lote:
                        nom_j = str(r.get("nombre_jornada") or st.session_state.get("nombre_jornada_activa", "Jornada en Vivo")).strip()
                        doc_id = str(r["uuid"]).strip() if r.get("uuid") else str(uuid.uuid4())
                        batch.set(coleccion_ref.document(doc_id), {
                            "fecha": str(r.get("fecha") or ""),
                            "zona": str(r.get("zona") or ""),
                            "paciente_id": str(r.get("paciente_id") or ""),
                            "edad": _a_entero(r.get("edad"), 0),
                            "diagnostico": str(r.get("diagnostico") or ""),
                            "farmaco_entregado": str(r.get("farmaco_entregado") or ""),
                            "cantidad_entregada": _a_entero(r.get("cantidad_entregada"), 1),
                            "tipo_registro": str(r.get("tipo_registro") or "en_vivo"),
                            "nombre_jornada": nom_j,
                            "sincronizado_en": ahora,
                        })

                    for intento in range(3):
                        try:
                            batch.commit(timeout=25)
                            break
                        except Exception as eb:
                            if intento == 2:
                                raise eb
                            time.sleep(1.0 * (intento + 1))

                    conn.executemany("UPDATE registros_jornada SET sincronizado = 1 WHERE id = ?", [(r["id"],) for r in lote])
                    total_nuevos_sync += len(lote)

            msg_res = []
            if total_nuevos_sync > 0:
                msg_res.append(f"{total_nuevos_sync} atención(es) subida(s)")
            if total_borrados_sync > 0:
                msg_res.append(f"{total_borrados_sync} eliminación(es) propagada(s)")
            if error_propagacion_del:
                msg_res.append(f"Aviso: falló propagación de eliminaciones a la nube ({error_propagacion_del})")
            texto_res = ", ".join(msg_res) if msg_res else "Sin cambios pendientes"
            return True, f"¡Sincronización finalizada! {texto_res}."
    except Exception as e:
        err_msg = str(e)
        if "invalid_grant" in err_msg or "Invalid JWT Signature" in err_msg:
            return False, "La clave de Firebase (firebase_key.json) fue revocada o cambiada en la consola de Firebase."
        elif "Timeout" in err_msg or "timed out" in err_msg.lower():
            return False, "Tiempo de espera agotado al conectar con Firebase. Verifica tu conexión a internet."
        else:
            return False, f"Error durante la sincronización: {err_msg}"


def obtener_resumen_jornadas_firebase() -> List[Dict[str, Any]]:
    """Consulta Firebase y devuelve una lista optimizada con las distintas jornadas almacenadas."""
    db = conectar_firestore()
    if not db:
        return []
    try:
        coleccion = db.collection("registros_jornada")
        try:
            docs = coleccion.select(["nombre_jornada", "tipo_registro", "fecha", "zona", "archivo_origen"]).stream(timeout=20)
        except Exception:
            docs = coleccion.stream(timeout=20)

        jornadas = {}
        for d in docs:
            data = d.to_dict()
            nom = str(data.get("nombre_jornada") or data.get("archivo_origen") or "Jornada en Vivo").strip()
            tipo = str(data.get("tipo_registro") or ("historico_excel" if data.get("archivo_origen") else "en_vivo")).strip()
            fecha = str(data.get("fecha") or "S/F")[:10]
            zona_d = str(data.get("zona") or "").strip()

            if nom not in jornadas:
                jornadas[nom] = {
                    "nombre": nom,
                    "tipo": tipo,
                    "fechas": set(),
                    "zonas": set(),
                    "total": 0
                }
            jornadas[nom]["total"] += 1
            if fecha and fecha != "S/F":
                jornadas[nom]["fechas"].add(fecha)
            if zona_d:
                jornadas[nom]["zonas"].add(zona_d)

        resultado = []
        for nom, info in sorted(jornadas.items(), key=lambda x: x[0]):
            fechas_str = ", ".join(sorted(info["fechas"])) if info["fechas"] else "S/F"
            zonas_str = ", ".join(sorted(info["zonas"])) if info["zonas"] else ""
            icono = "🔴 [En Vivo]" if info["tipo"] == "en_vivo" else "📁 [Histórico]"
            sub_info = f"{zonas_str} | {fechas_str}" if zonas_str else fechas_str
            resultado.append({
                "nombre": nom,
                "tipo": info["tipo"],
                "fechas": fechas_str,
                "zonas": zonas_str,
                "total": info["total"],
                "etiqueta": f"{icono} {nom} — ({sub_info}) [{info['total']} registros]"
            })
        return resultado
    except Exception as e_res:
        logger.error(f"Error consultando resumen de jornadas en Firebase: {e_res}", exc_info=True)
        return []


def consultar_registros_firebase(nombre_jornada: Optional[str] = None, tipo_registro: Optional[str] = None) -> List[Dict[str, Any]]:
    """Descarga los registros de Firebase aplicando filtros por jornada y/o tipo con timeout optimizado."""
    db = conectar_firestore()
    if not db:
        return []
    try:
        eliminados_locales = set()
        try:
            with obtener_conexion_bd() as conn:
                cur = conn.cursor()
                cur.execute("SELECT uuid FROM registros_eliminados")
                eliminados_locales = {str(r[0]).strip() for r in cur.fetchall() if r[0]}
        except Exception as e_del:
            logger.warning(f"Error consultando eliminados locales: {e_del}")

        coleccion = db.collection("registros_jornada")
        if nombre_jornada and nombre_jornada not in ("todas", "Todas las Jornadas", "🌐 Todas las Jornadas en la Nube", ""):
            docs = coleccion.where("nombre_jornada", "==", nombre_jornada).stream(timeout=25)
        else:
            docs = coleccion.stream(timeout=25)

        registros = []
        for d in docs:
            item = d.to_dict()
            doc_id = str(d.id).strip()
            item_uuid = str(item.get("uuid") or doc_id).strip()
            item["uuid"] = item_uuid

            if item_uuid in eliminados_locales or item.get("anulado") or item.get("eliminado"):
                continue

            item_nom = str(item.get("nombre_jornada") or item.get("archivo_origen") or "Jornada en Vivo").strip()
            item_tipo = str(item.get("tipo_registro") or ("historico_excel" if item.get("archivo_origen") else "en_vivo")).strip()
            item["nombre_jornada"] = item_nom
            item["tipo_registro"] = item_tipo

            if tipo_registro and tipo_registro not in ("todos", "Todas", "") and item_tipo != tipo_registro:
                continue
            if nombre_jornada and nombre_jornada not in ("todas", "Todas las Jornadas", "🌐 Todas las Jornadas en la Nube", "") and item_nom != nombre_jornada:
                continue
            registros.append(item)
        return registros
    except Exception as e_cons:
        logger.error(f"Error consultando registros en Firebase Firestore: {e_cons}", exc_info=True)
        return []

