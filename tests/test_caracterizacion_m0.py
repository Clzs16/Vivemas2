import unittest
import sqlite3
import os
import sys
import tempfile
import pandas as pd
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

# Asegurar importación de app.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import (
    normalizar_nombre_farmaco,
    normalizar_diagnostico,
    explotar_diagnosticos,
    calcular_stock_disponible_item,
    vaciar_stock,
    eliminar_atencion_individual,
    inicializar_bd,
    verificar_password_pbkdf2,
    tiene_rol,
    requiere_rol,
    construir_contexto_gemini_agregado,
    sanitizar_dataframe_para_export,
)
from scripts.generar_hash import generar_hash


class TestCaracterizacionM0(unittest.TestCase):
    def test_normalizacion_farmacos_y_diagnosticos(self):
        # Fármacos: número y unidad colapsados, sin acentos, minúsculas
        self.assertEqual(normalizar_nombre_farmaco("Paracetamol 500 mg"), "paracetamol 500mg")
        self.assertEqual(normalizar_nombre_farmaco("Losartán Potásico 50 mg"), "losartan potasico 50mg")

        # Diagnósticos: comorbilidades unidas con " + ", detección por palabra completa, sin negaciones falsas
        res_diag = normalizar_diagnostico("Diabetes e HTA")
        self.assertIn("Diabetes Mellitus Tipo 2", res_diag)
        self.assertIn("Hipertensión Arterial (HTA)", res_diag)
        self.assertEqual(normalizar_diagnostico("paciente sin diabetes"), "paciente sin diabetes")
        self.assertEqual(normalizar_diagnostico("huesos rotos"), "huesos rotos")

    def test_explotar_diagnosticos(self):
        df = pd.DataFrame([
            {"id": 1, "diagnostico": "Diabetes Mellitus Tipo 2 + Hipertensión Arterial (HTA)"},
            {"id": 2, "diagnostico": "Infección Respiratoria Aguda (IRA)"},
        ])
        df_exp = explotar_diagnosticos(df)
        self.assertEqual(len(df_exp), 3)
        conteo = df_exp["diagnostico"].value_counts().to_dict()
        self.assertEqual(conteo.get("Diabetes Mellitus Tipo 2"), 1)
        self.assertEqual(conteo.get("Hipertensión Arterial (HTA)"), 1)

    def test_calculo_stock_entrega_anulacion_y_archivado(self):
        conn = sqlite3.connect(":memory:")
        inicializar_bd(conn)

        # 1. Ingreso de donación
        conn.execute("INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('C1', 'Amoxicilina 500mg', 100)")
        conn.commit()

        stk1 = calcular_stock_disponible_item("Amoxicilina 500mg", conn=conn)
        self.assertEqual(stk1["disponible"], 100)

        # 2. Entrega de 40
        conn.execute(
            "INSERT INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid, archivado) "
            "VALUES ('2026-01-01', 'Amoxicilina 500mg', 'amoxicilina 500mg', 'entrega', 40, 'u-test-1', 0)"
        )
        conn.commit()
        stk2 = calcular_stock_disponible_item("amoxicilina 500 mg", conn=conn)
        self.assertEqual(stk2["disponible"], 60)

        # 3. Anulación compensatoria de 10
        conn.execute(
            "INSERT INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid, archivado) "
            "VALUES ('2026-01-01', 'Amoxicilina 500mg', 'amoxicilina 500mg', 'anulacion', 10, 'u-test-1:anul', 0)"
        )
        conn.commit()
        stk3 = calcular_stock_disponible_item("Amoxicilina 500mg", conn=conn)
        self.assertEqual(stk3["disponible"], 70)

        # 4. Vaciar stock archiva y no resta del stock nuevo
        vaciar_stock(conn=conn)
        conn.execute("INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('C2', 'Amoxicilina 500mg', 50)")
        conn.commit()
        stk4 = calcular_stock_disponible_item("Amoxicilina 500mg", conn=conn)
        self.assertEqual(stk4["disponible"], 50)
        conn.close()

    def test_migracion_sobre_base_antigua(self):
        conn = sqlite3.connect(":memory:")
        # Simular base antigua preexistente (sin uuid, sin movimientos_stock, sin archivado)
        conn.execute("""
            CREATE TABLE registros_jornada (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT,
                zona TEXT,
                paciente_id TEXT,
                edad INTEGER,
                diagnostico TEXT,
                farmaco_entregado TEXT,
                cantidad_entregada INTEGER
            )
        """)
        conn.execute("INSERT INTO registros_jornada (fecha, paciente_id, edad, diagnostico, farmaco_entregado, cantidad_entregada) VALUES ('2025-01-01', 'P-OLD', 40, 'Gripe', 'Paracetamol', 10)")
        conn.commit()

        # Ejecutar inicialización / migración
        inicializar_bd(conn)

        # Verificar que el registro antiguo se preservó
        row = conn.execute("SELECT paciente_id, edad, farmaco_entregado FROM registros_jornada WHERE id = 1").fetchone()
        self.assertEqual(row[0], "P-OLD")
        self.assertEqual(row[1], 40)

        # Verificar que se crearon las columnas y tablas nuevas
        cols = [c[1] for c in conn.execute("PRAGMA table_info(registros_jornada)").fetchall()]
        self.assertIn("uuid", cols)
        self.assertIn("sincronizado", cols)

        tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        self.assertIn("movimientos_stock", tables)
        self.assertIn("registro_auditoria", tables)
        conn.close()

    def test_verificar_password_pbkdf2_y_roles(self):
        pw = "ClaveSecreta2026!"
        h = generar_hash(pw)
        self.assertTrue(verificar_password_pbkdf2(pw, h))
        self.assertFalse(verificar_password_pbkdf2("erronea", h))

        # Guardia de servidor: campo no puede ejecutar coordinador
        fake_campo = {"rol_activo": "campo", "operador_activo": "Operador 1"}
        with patch("streamlit.session_state", fake_campo):
            self.assertFalse(tiene_rol("coordinador"))
            self.assertFalse(requiere_rol("coordinador"))

    def test_contexto_gemini_agregado_sin_paciente_id_ni_edad(self):
        df = pd.DataFrame([
            {"paciente_id": "CONFIDENCIAL_123", "edad": 55, "zona": "La Guaira", "diagnostico": "Asma", "farmaco_entregado": "Salbutamol", "cantidad_entregada": 1}
        ])
        ctx = construir_contexto_gemini_agregado(df, "¿Qué diagnósticos hay?", "Zona: Todas")
        self.assertNotIn("CONFIDENCIAL_123", ctx)
        self.assertNotIn("55", ctx)
        self.assertIn("Total de atenciones analizadas: 1", ctx)

    def test_sanitizar_dataframe_para_export(self):
        df = pd.DataFrame([{"formula": "=SUM(A1:A10)", "texto": "Normal"}])
        df_san = sanitizar_dataframe_para_export(df)
        self.assertEqual(df_san["formula"].iloc[0], "'=SUM(A1:A10)")
        self.assertEqual(df_san["texto"].iloc[0], "Normal")


class TestAppTestSmokeM0(unittest.TestCase):
    def test_apptest_login_campo_y_coordinador_sin_excepciones(self):
        ruta_app = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
        h_campo = generar_hash("Campo2026!")
        h_coord = generar_hash("Coordinador2026!")

        with patch("vivemas.seguridad.obtener_hashes_roles", return_value=(h_coord, h_campo)):
            # 1. Sesión Campo
            at_campo = AppTest.from_file(ruta_app)
            at_campo.run(timeout=15)
            self.assertEqual(len(at_campo.exception), 0)

            # Iniciar sesión campo
            if at_campo.text_input(key="input_nombre_operador"):
                at_campo.text_input(key="input_nombre_operador").input("Dra. Campo")
                at_campo.text_input(key="input_clave_seguridad").input("Campo2026!")
                at_campo.button[0].click()
                at_campo.run(timeout=15)
                self.assertTrue(at_campo.session_state.get("autenticado"))
                self.assertEqual(at_campo.session_state.get("rol_activo"), "campo")
                self.assertEqual(len(at_campo.exception), 0)

            # 2. Sesión Coordinador
            at_coord = AppTest.from_file(ruta_app)
            at_coord.run(timeout=15)
            if at_coord.text_input(key="input_nombre_operador"):
                at_coord.text_input(key="input_nombre_operador").input("Dr. Coordinador")
                at_coord.text_input(key="input_clave_seguridad").input("Coordinador2026!")
                at_coord.button[0].click()
                at_coord.run(timeout=15)
                self.assertTrue(at_coord.session_state.get("autenticado"))
                self.assertEqual(at_coord.session_state.get("rol_activo"), "coordinador")
                self.assertEqual(len(at_coord.exception), 0)


if __name__ == "__main__":
    unittest.main()
