import unittest
import sqlite3
import unicodedata
import re
import os
import sys
import tempfile
import pandas as pd

# Asegurar que la ruta raíz esté en sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import (
    normalizar_nombre_farmaco,
    normalizar_diagnostico,
    resolver_api_key_gemini,
    _detectar_zona_en_texto,
    _es_error_modelo_inexistente,
    _es_error_reintentable_espera,
    calcular_stock_disponible_item,
    inicializar_bd,
    vaciar_stock,
    eliminar_atencion_individual,
    explotar_diagnosticos,
)


class TestNormalizacionFarmacos(unittest.TestCase):
    def test_equivalencias_espaciado_y_unidades(self):
        # 500 mg == 500mg
        self.assertEqual(
            normalizar_nombre_farmaco("Paracetamol 500 mg"),
            normalizar_nombre_farmaco("paracetamol 500mg"),
        )
        self.assertEqual(normalizar_nombre_farmaco("Paracetamol 500 mg"), "paracetamol 500mg")

    def test_acentos_y_mayusculas(self):
        # Acentos y mayúsculas removidos
        self.assertEqual(
            normalizar_nombre_farmaco("Losartán Potásico 50 mg"),
            "losartan potasico 50mg",
        )
        self.assertEqual(
            normalizar_nombre_farmaco("LOSARTAN POTASICO 50MG"),
            "losartan potasico 50mg",
        )

    def test_unidades_compuestas_y_slashes(self):
        # Manejo de unidades compuestas tipo 75mg/3ml
        self.assertEqual(
            normalizar_nombre_farmaco("Diclofenaco Sódico 75 mg / 3 ml"),
            "diclofenaco sodico 75mg/3ml",
        )
        self.assertEqual(
            normalizar_nombre_farmaco("Diclofenaco Sódico 75mg/3ml"),
            "diclofenaco sodico 75mg/3ml",
        )

    def test_espacios_multiples_y_vacios(self):
        self.assertEqual(
            normalizar_nombre_farmaco("   Amoxicilina    500   mg   "),
            "amoxicilina 500mg",
        )
        self.assertEqual(normalizar_nombre_farmaco(""), "")
        self.assertEqual(normalizar_nombre_farmaco(None), "")


class TestNormalizacionDiagnostico(unittest.TestCase):
    def test_casos_obligatorios_especificados(self):
        # 1. "Diabetes e HTA" -> ambas comorbilidades
        res_dm_hta = normalizar_diagnostico("Diabetes e HTA")
        self.assertIn("Diabetes Mellitus Tipo 2", res_dm_hta)
        self.assertIn("Hipertensión Arterial (HTA)", res_dm_hta)
        self.assertIn(" + ", res_dm_hta)

        # 2. "huesos rotos" -> conserva texto original limpio sin .title() forzado
        self.assertEqual(normalizar_diagnostico("huesos rotos"), "huesos rotos")

        # 3. "tos seca" -> IRA (coincidencia de palabra completa)
        self.assertEqual(normalizar_diagnostico("tos seca"), "Infección Respiratoria Aguda (IRA)")

        # 4. "presión alta" y "presión arterial alta" -> HTA
        self.assertEqual(normalizar_diagnostico("presión alta"), "Hipertensión Arterial (HTA)")
        self.assertEqual(normalizar_diagnostico("presión arterial alta"), "Hipertensión Arterial (HTA)")

        # 5. texto vacío -> cadena vacía limpia
        self.assertEqual(normalizar_diagnostico(""), "")
        self.assertEqual(normalizar_diagnostico("   "), "")

    def test_manejo_de_negaciones(self):
        # Diagnósticos precedidos de negación no deben reconocerse falsamente
        self.assertEqual(normalizar_diagnostico("paciente sin diabetes"), "paciente sin diabetes")
        self.assertEqual(normalizar_diagnostico("niega HTA"), "niega HTA")
        self.assertEqual(normalizar_diagnostico("descarta asma"), "descarta asma")
        self.assertEqual(normalizar_diagnostico("no presenta tos"), "no presenta tos")

    def test_no_falsos_positivos_subcadenas(self):
        # "fotos" no debe activar "tos"
        self.assertEqual(normalizar_diagnostico("fotos de control"), "fotos de control")
        # "tirar" no debe activar "ira"
        self.assertEqual(normalizar_diagnostico("tirar vendaje"), "tirar vendaje")
        # "chatarra" no debe activar "hta"
        self.assertEqual(normalizar_diagnostico("chatarra"), "chatarra")


class TestDeteccionZona(unittest.TestCase):
    def test_deteccion_palabra_completa_e_insensible(self):
        zonas = ["La Guaira", "Caracas", "Sur"]
        # Insensible a mayúsculas y acentos
        self.assertEqual(_detectar_zona_en_texto(zonas, "¿Cuántas atenciones en la guaira?"), "La Guaira")
        self.assertEqual(_detectar_zona_en_texto(zonas, "Reporte de CARACAS"), "Caracas")
        # No confundir palabra contenida ("sur" en "consulta")
        self.assertIsNone(_detectar_zona_en_texto(zonas, "¿Dónde fue la consulta general?"))
        self.assertEqual(_detectar_zona_en_texto(zonas, "Visita a zona Sur"), "Sur")


class TestManejoErroresModelos(unittest.TestCase):
    def test_clasificacion_errores(self):
        class DummyError(Exception):
            pass

        err_404 = DummyError("404 Model models/gemini-not-found is not found")
        self.assertTrue(_es_error_modelo_inexistente(err_404))
        self.assertFalse(_es_error_reintentable_espera(err_404))

        err_429 = DummyError("429 Resource exhausted: quota exceeded")
        self.assertFalse(_es_error_modelo_inexistente(err_429))
        self.assertTrue(_es_error_reintentable_espera(err_429))

        err_503 = DummyError("503 Service Unavailable: overloaded")
        self.assertFalse(_es_error_modelo_inexistente(err_503))
        self.assertTrue(_es_error_reintentable_espera(err_503))


class TestPunto1MigracionStock(unittest.TestCase):
    def test_migracion_e_idempotencia_reparacion(self):
        conn = sqlite3.connect(":memory:")
        inicializar_bd(conn)

        # Insertar donación de 100 de "paracetamol 500 mg"
        conn.execute(
            "INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('Caja 1', 'paracetamol 500 mg', 100)"
        )
        # Simular entrega con nombre no normalizado en farmaco_norm (error previo)
        conn.execute(
            "INSERT INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid) "
            "VALUES ('2026-01-01', 'Paracetamol 500mg', 'Paracetamol 500mg', 'entrega', 40, 'u1')"
        )
        conn.commit()

        # Al ejecutar inicializar_bd, la reparación idempotente recalcula farmaco_norm con normalizar_nombre_farmaco
        inicializar_bd(conn)

        info = calcular_stock_disponible_item("Paracetamol 500 mg", conn=conn)
        self.assertEqual(info["disponible"], 60)
        self.assertEqual(info["total_ingresado"], 100)
        self.assertEqual(info["total_entregado"], 40)

        # Re-ejecutar inicializar_bd (idempotencia)
        inicializar_bd(conn)
        info_post = calcular_stock_disponible_item("paracetamol 500mg", conn=conn)
        self.assertEqual(info_post["disponible"], 60)
        conn.close()


class TestPunto2BorradoIndividual(unittest.TestCase):
    def test_anulacion_compensatoria_restaura_stock(self):
        conn = sqlite3.connect(":memory:")
        inicializar_bd(conn)

        # Donación de 500
        conn.execute("INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('C1', 'Amoxicilina 500mg', 500)")
        # Insertar atención y su movimiento de stock (300 unidades entregadas por error)
        uid_atencion = "test-uuid-error-300"
        conn.execute(
            "INSERT INTO registros_jornada (fecha, zona, paciente_id, edad, diagnostico, farmaco_entregado, cantidad_entregada, sincronizado, uuid) "
            "VALUES ('2026-01-01', 'Caracas', 'P1', 30, 'IRA', 'Amoxicilina 500mg', 300, 0, ?)",
            (uid_atencion,)
        )
        conn.execute(
            "INSERT INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid) "
            "VALUES ('2026-01-01', 'Amoxicilina 500mg', 'amoxicilina 500mg', 'entrega', 300, ?)",
            (uid_atencion,)
        )
        conn.commit()
        rid_inserted = conn.execute("SELECT id FROM registros_jornada WHERE uuid = ?", (uid_atencion,)).fetchone()[0]

        # Verificar stock antes del borrado
        info_antes = calcular_stock_disponible_item("Amoxicilina 500mg", conn=conn)
        self.assertEqual(info_antes["disponible"], 200)

        # Borrar el registro individualmente
        eliminar_atencion_individual(
            conn=conn,
            rid=1,
            uid=uid_atencion,
            farmaco="Amoxicilina 500mg",
            cantidad=300
        )

        # Verificar que el stock disponible volvió a 500
        info_despues = calcular_stock_disponible_item("Amoxicilina 500mg", conn=conn)
        self.assertEqual(info_despues["disponible"], 500)
        self.assertEqual(info_despues["total_entregado"], 0)

        # Verificar que el movimiento compensatorio existe
        cursor = conn.cursor()
        cursor.execute("SELECT tipo, cantidad, ref_uuid FROM movimientos_stock WHERE ref_uuid = ?", (f"{uid_atencion}:anul",))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "anulacion")
        self.assertEqual(row[1], 300)
        conn.close()


class TestPunto3VaciarStock(unittest.TestCase):
    def test_vaciar_stock_archiva_movimientos_y_resetea_disponible(self):
        conn = sqlite3.connect(":memory:")
        inicializar_bd(conn)

        # Stock inicial y entregas
        conn.execute("INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('C1', 'Ibuprofeno 400mg', 100)")
        conn.execute(
            "INSERT INTO movimientos_stock (fecha, farmaco, farmaco_norm, tipo, cantidad, ref_uuid, archivado) "
            "VALUES ('2026-01-01', 'Ibuprofeno 400mg', 'ibuprofeno 400mg', 'entrega', 20, 'u-ib-1', 0)"
        )
        conn.commit()

        info1 = calcular_stock_disponible_item("Ibuprofeno 400mg", conn=conn)
        self.assertEqual(info1["disponible"], 80)

        # Vaciar stock (archiva movimientos y limpia donaciones)
        vaciar_stock(conn=conn)

        # Donaciones debe estar vacía y movimientos marcados como archivado=1
        c_don = conn.execute("SELECT COUNT(*) FROM stock_donaciones").fetchone()[0]
        self.assertEqual(c_don, 0)
        c_arch = conn.execute("SELECT COUNT(*) FROM movimientos_stock WHERE archivado = 1").fetchone()[0]
        self.assertEqual(c_arch, 1)

        # Ahora registrar stock nuevo de 50 unidades
        conn.execute("INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('Caja Nueva', 'Ibuprofeno 400mg', 50)")
        conn.commit()

        info2 = calcular_stock_disponible_item("Ibuprofeno 400mg", conn=conn)
        self.assertEqual(info2["total_ingresado"], 50)
        self.assertEqual(info2["total_entregado"], 0)
        self.assertEqual(info2["disponible"], 50)
        conn.close()


class TestPunto4FalloAbiertoStock(unittest.TestCase):
    def test_conexion_fallida_retorna_estado_error(self):
        conn_closed = sqlite3.connect(":memory:")
        conn_closed.close()

        res = calcular_stock_disponible_item("Paracetamol 500mg", conn=conn_closed)
        self.assertTrue(res.get("error"))
        self.assertFalse(res.get("registrado"))
        self.assertIn("detalle_error", res)


class TestPunto5ExplotarDiagnosticos(unittest.TestCase):
    def test_separacion_comorbilidades_sin_duplicar_totales(self):
        df = pd.DataFrame([
            {"id": 1, "diagnostico": "Diabetes Mellitus Tipo 2 + Hipertensión Arterial (HTA)"},
            {"id": 2, "diagnostico": "Infección Respiratoria Aguda (IRA)"},
            {"id": 3, "diagnostico": "Hipertensión Arterial (HTA)"},
        ])

        df_exp = explotar_diagnosticos(df)
        # Fila 1 se divide en 2 filas, total 4 filas
        self.assertEqual(len(df_exp), 4)

        conteo = df_exp["diagnostico"].value_counts().to_dict()
        self.assertEqual(conteo.get("Hipertensión Arterial (HTA)"), 2)
        self.assertEqual(conteo.get("Diabetes Mellitus Tipo 2"), 1)
        self.assertEqual(conteo.get("Infección Respiratoria Aguda (IRA)"), 1)


class TestConcurrenciaBeginImmediate(unittest.TestCase):
    def test_bloqueo_inmediato_entre_dos_conexiones(self):
        # Usar archivo temporal para permitir múltiples conexiones concurrentes
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            conn1 = sqlite3.connect(db_path, timeout=1.0)
            conn2 = sqlite3.connect(db_path, timeout=0.1)

            inicializar_bd(conn1)

            # Conn 1 abre transacción inmediata
            conn1.execute("BEGIN IMMEDIATE")
            conn1.execute("INSERT INTO stock_donaciones (caja, contenido, cantidad) VALUES ('C1', 'Insulina', 10)")

            # Conn 2 intenta transacción inmediata simultánea; debe ser bloqueada inmediatamente
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn2.execute("BEGIN IMMEDIATE")
            self.assertIn("locked", str(ctx.exception).lower())

            # Liberar Conn 1
            conn1.commit()
            conn1.close()
            conn2.close()
        finally:
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
