# -*- coding: utf-8 -*-
"""Pruebas unitarias para la arquitectura modular M1-M5 y mejoras de experiencia de campo U1-U10."""

import io
import unittest
import sqlite3
import pandas as pd

from vivemas.config import SS, init_estado
from vivemas.textos import normalizar_texto, normalizar_nombre_farmaco, _detectar_zona_en_texto, validar_formato_codigo_paciente
from vivemas.diagnosticos import normalizar_diagnostico, explotar_diagnosticos
from vivemas.exportes import sanitizar_dataframe_para_export, sanitizar_dataframe_de_import, df_a_excel_bytes, leer_archivo_tabular_unificado
from vivemas.db import obtener_version_bd, establecer_version_bd, _aplicar_esquema_bd
from vivemas.stock import calcular_stock_disponible_item, eliminar_atencion_individual
from vivemas.ui.estilos import aplicar_estilos_tema


class TestModularM1M5(unittest.TestCase):
    """Verificación de módulos desacoplados, versiones de base de datos y exportaciones."""

    def test_versionado_bd_user_version(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(obtener_version_bd(conn), 0)
        establecer_version_bd(conn, 5)
        self.assertEqual(obtener_version_bd(conn), 5)
        _aplicar_esquema_bd(conn)
        self.assertEqual(obtener_version_bd(conn), 5)
        conn.close()

    def test_exportes_unificados_excel(self):
        # 1. Un DataFrame
        df1 = pd.DataFrame({"medicamento": ["=SUMA(A1:A2)", "Paracetamol 500mg"], "cant": [10, 20]})
        b1 = df_a_excel_bytes(df1, "Test")
        self.assertTrue(len(b1) > 0)
        self.assertTrue(b1.startswith(b"PK"))  # Cabecera ZIP de archivo XLSX

        # 2. Diccionario multi-hoja
        df2 = pd.DataFrame({"diagnostico": ["Hipertensión", "Diabetes"]})
        b2 = df_a_excel_bytes({"Fármacos": df1, "Diagnósticos": df2})
        self.assertTrue(len(b2) > 0)
        self.assertTrue(b2.startswith(b"PK"))

        # 3. Diccionario con DataFrames vacíos no debe fallar
        df_vacio = pd.DataFrame()
        b3 = df_a_excel_bytes({"Vacia": df_vacio})
        self.assertTrue(len(b3) > 0)

    def test_lector_tabular_unificado(self):
        # CSV válido con codificación UTF-8
        csv_bytes = "Caja,Contenido,Cantidad\nCaja 1,Ibuprofeno 400mg,50\n".encode("utf-8")
        buf = io.BytesIO(csv_bytes)
        buf.name = "donaciones.csv"
        df, err = leer_archivo_tabular_unificado(buf, columnas_requeridas=["caja", "contenido"])
        self.assertIsNone(err)
        self.assertEqual(len(df), 1)
        self.assertIn("caja", df.columns)

        # CSV con columnas faltantes
        buf.seek(0)
        df_err, err_msg = leer_archivo_tabular_unificado(buf, columnas_requeridas=["caja", "lote_inexistente"])
        self.assertIsNotNone(err_msg)
        self.assertIn("lote_inexistente", err_msg)

    def test_centralizacion_session_state(self):
        self.assertEqual(SS.ROL, "rol_usuario")
        self.assertEqual(SS.TEMA, "app_tema")
        self.assertEqual(SS.MODO_OFFLINE, "modo_offline")

    def test_temas_css_inyeccion_sin_excepciones(self):
        # Verifica que la función que inyecta los estilos se ejecute limpiamente
        # en sus 3 modalidades de campo: oscuro, luz_baja y alto_contraste
        for tema in ["oscuro", "luz_baja", "alto_contraste"]:
            try:
                aplicar_estilos_tema(tema)
            except Exception as e:
                self.fail(f"aplicar_estilos_tema falló con tema '{tema}': {e}")


    def test_compatibilidad_clear_funciones_datos(self):
        """Verifica que las funciones de carga de datos admitan .clear() sin lanzar AttributeError."""
        from vivemas.db import cargar_stock_donaciones, cargar_registros_local, cargar_registros_historicos
        for fn in [cargar_stock_donaciones, cargar_registros_local, cargar_registros_historicos]:
            self.assertTrue(hasattr(fn, "clear"), f"{fn.__name__} debe tener el método .clear()")
            try:
                fn.clear()
            except Exception as e:
                self.fail(f"Llamar a {fn.__name__}.clear() falló: {e}")


if __name__ == "__main__":
    unittest.main()

