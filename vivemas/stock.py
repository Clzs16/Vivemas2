# -*- coding: utf-8 -*-
"""Cálculo transaccional de stock, anulaciones compensatorias y vaciado/archivado."""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
import sqlite3

from vivemas.db import obtener_conexion_bd, crear_respaldo_bd
from vivemas.textos import normalizar_nombre_farmaco

logger = logging.getLogger("vivemas.stock")


def calcular_stock_disponible_item(farmaco_nombre: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Calcula el balance en tiempo real: stock ingresado en donaciones menos entregas netas (entregas - anulaciones).
    Ignora movimientos archivados, no usa caché y propaga estado de error en fallos de conexión/consulta."""
    if not farmaco_nombre or farmaco_nombre == "➕ Agregar nuevo medicamento...":
        return {
            "error": False,
            "detalle_error": "",
            "registrado": False,
            "total_ingresado": 0,
            "total_entregado": 0,
            "disponible": 0
        }

    f_norm = normalizar_nombre_farmaco(farmaco_nombre)
    if not f_norm:
        return {
            "error": False,
            "detalle_error": "",
            "registrado": False,
            "total_ingresado": 0,
            "total_entregado": 0,
            "disponible": 0
        }

    def _ejecutar_calculo(c):
        cur = c.cursor()
        # 1. Total ingresado desde stock_donaciones
        cur.execute("SELECT contenido, cantidad FROM stock_donaciones")
        rows_don = cur.fetchall()
        total_ingresado = 0
        for c_nom, c_cant in rows_don:
            if normalizar_nombre_farmaco(c_nom) == f_norm:
                try:
                    total_ingresado += int(c_cant or 0)
                except (ValueError, TypeError):
                    pass

        # 2. Total neto entregado: entregas - anulaciones, ignorando archivados
        cur.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN tipo = 'entrega' THEN cantidad ELSE 0 END), 0) -
                COALESCE(SUM(CASE WHEN tipo = 'anulacion' THEN cantidad ELSE 0 END), 0)
            FROM movimientos_stock
            WHERE farmaco_norm = ? AND COALESCE(archivado, 0) = 0
        """, (f_norm,))
        res_ent = cur.fetchone()
        total_entregado = max(0, int(res_ent[0])) if (res_ent and res_ent[0] is not None) else 0

        disponible = max(0, total_ingresado - total_entregado)
        return {
            "error": False,
            "detalle_error": "",
            "registrado": total_ingresado > 0,
            "total_ingresado": total_ingresado,
            "total_entregado": total_entregado,
            "disponible": disponible
        }

    try:
        if conn is not None:
            return _ejecutar_calculo(conn)
        else:
            with obtener_conexion_bd() as c_local:
                return _ejecutar_calculo(c_local)
    except Exception as e:
        logger.error(f"Error calculando stock para fármaco '{farmaco_nombre}': {e}", exc_info=True)
        return {
            "error": True,
            "detalle_error": str(e),
            "registrado": False,
            "total_ingresado": 0,
            "total_entregado": 0,
            "disponible": 0
        }


def eliminar_atencion_individual(
    conn: sqlite3.Connection,
    rid: int,
    uid: str,
    farmaco: str,
    cantidad: int,
    fecha: Optional[str] = None
):
    """Elimina una atención médica e inserta un movimiento compensatorio tipo='anulacion' en movimientos_stock."""
    ahora_del = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    f_fecha = fecha or ahora_del
    f_norm = normalizar_nombre_farmaco(farmaco)
    cur = conn.cursor()
    if uid:
        cur.execute(
            "INSERT OR REPLACE INTO registros_eliminados (uuid, fecha_eliminacion, sincronizado) VALUES (?, ?, 0)",
            (str(uid).strip(), ahora_del)
        )
        cur.execute("""
            INSERT OR IGNORE INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid, archivado)
            VALUES (?, ?, ?, 'anulacion', ?, ?, 0)
        """, (f_fecha, farmaco, f_norm, int(cantidad or 0), f"{str(uid).strip()}:anul"))
    cur.execute("DELETE FROM registros_jornada WHERE id = ?", (rid,))


def vaciar_stock(conn: Optional[sqlite3.Connection] = None):
    """Vacía las cajas de donación y archiva los movimientos de stock en una transacción atómica protegida."""
    crear_respaldo_bd(etiqueta="pre_vaciar_stock")

    def _ejecutar(c):
        c.execute("UPDATE movimientos_stock SET archivado = 1 WHERE COALESCE(archivado, 0) = 0")
        c.execute("DELETE FROM stock_donaciones")

    if conn is not None:
        _ejecutar(conn)
    else:
        with obtener_conexion_bd() as c_local:
            _ejecutar(c_local)
