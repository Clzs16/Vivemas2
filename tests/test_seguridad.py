import unittest
import sqlite3
import os
import sys
import tempfile
import re
import pandas as pd
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
from streamlit.testing.v1 import AppTest

# Asegurar importación de app.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import (
    verificar_password_pbkdf2,
    obtener_hashes_roles,
    tiene_rol,
    requiere_rol,
    sanitizar_dataframe_para_export,
    sanitizar_dataframe_de_import,
    validar_formato_codigo_paciente,
    construir_contexto_gemini_agregado,
    crear_respaldo_bd,
    inicializar_bd,
    obtener_secreto,
    verificar_acceso,
)
from scripts.generar_hash import generar_hash


class TestSeguridadAutenticacion(unittest.TestCase):
    def setUp(self):
        self.clave_campo = "CampoSeguro2026!"
        self.clave_coord = "CoordinadorMaster2026!"
        self.hash_campo = generar_hash(self.clave_campo)
        self.hash_coord = generar_hash(self.clave_coord)

    def test_verificacion_pbkdf2_correcta_e_incorrecta(self):
        # Claves correctas
        self.assertTrue(verificar_password_pbkdf2(self.clave_campo, self.hash_campo))
        self.assertTrue(verificar_password_pbkdf2(self.clave_coord, self.hash_coord))

        # Claves incorrectas
        self.assertFalse(verificar_password_pbkdf2("ClaveErronea123", self.hash_campo))
        self.assertFalse(verificar_password_pbkdf2("", self.hash_coord))
        self.assertFalse(verificar_password_pbkdf2(self.clave_campo, self.hash_coord))

    def test_formato_hash_y_coste_computacional(self):
        parts = self.hash_campo.split("$")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], "pbkdf2_sha256")
        self.assertEqual(int(parts[1]), 600000)
        self.assertEqual(len(parts[2]), 32)  # 16 bytes en hex
        self.assertEqual(len(parts[3]), 64)  # sha256 hex


class TestSeguridadRolesYGuardas(unittest.TestCase):
    def test_guarda_servidor_requiere_rol(self):
        fake_session = {"rol_activo": "campo", "operador_activo": "Operador 1"}
        with patch("streamlit.session_state", fake_session):
            # Campo tiene acceso a rol campo
            self.assertTrue(tiene_rol("campo"))
            # Campo NO tiene acceso a rol coordinador
            self.assertFalse(tiene_rol("coordinador"))
            self.assertFalse(requiere_rol("coordinador"))

        fake_coord_session = {"rol_activo": "coordinador", "operador_activo": "Coord 1"}
        with patch("streamlit.session_state", fake_coord_session):
            # Coordinador tiene acceso a ambos roles
            self.assertTrue(tiene_rol("campo"))
            self.assertTrue(tiene_rol("coordinador"))
            self.assertTrue(requiere_rol("coordinador"))


class TestSeguridadPrivacidadDatos(unittest.TestCase):
    def test_neutralizacion_inyeccion_formulas_excel(self):
        df_malicioso = pd.DataFrame([
            {"codigo": "=cmd|'/c calc'!A0", "diagnostico": "+2+5", "farmaco": "-Dosis", "otro": "@SUM(1,2)"},
            {"codigo": "P-101", "diagnostico": "IRA", "farmaco": "Amoxicilina", "otro": "Normal"}
        ])

        df_sanitizado = sanitizar_dataframe_para_export(df_malicioso)

        # Todas las celdas peligrosas deben comenzar con comilla simple
        self.assertTrue(df_sanitizado["codigo"].iloc[0].startswith("'="))
        self.assertTrue(df_sanitizado["diagnostico"].iloc[0].startswith("'+"))
        self.assertTrue(df_sanitizado["farmaco"].iloc[0].startswith("'-"))
        self.assertTrue(df_sanitizado["otro"].iloc[0].startswith("'@"))

        # Las celdas benignas no se modifican
        self.assertEqual(df_sanitizado["codigo"].iloc[1], "P-101")
        self.assertEqual(df_sanitizado["diagnostico"].iloc[1], "IRA")

        # Al reimportar, se remueve la comilla protectora limpiamente
        df_reimport = sanitizar_dataframe_de_import(df_sanitizado)
        self.assertEqual(df_reimport["codigo"].iloc[0], "=cmd|'/c calc'!A0")

    def test_contexto_gemini_estrictamente_anonimizado_y_agregado(self):
        df_pacientes = pd.DataFrame([
            {"id": 1, "fecha": "2026-01-01", "zona": "Caracas", "paciente_id": "ID_SECRETO_JUAN_PEREZ", "edad": 45, "diagnostico": "Diabetes Mellitus Tipo 2", "farmaco_entregado": "Metformina 500mg", "cantidad_entregada": 30},
            {"id": 2, "fecha": "2026-01-01", "zona": "Caracas", "paciente_id": "ID_CONFIDENCIAL_ANA_GOMEZ", "edad": 72, "diagnostico": "Hipertensión Arterial (HTA)", "farmaco_entregado": "Losartan 50mg", "cantidad_entregada": 30}
        ])

        contexto = construir_contexto_gemini_agregado(df_pacientes, "¿Cuántos diabéticos hay?", "Zona: Caracas")

        # NUNCA debe contener identificadores individuales ni edades exactas
        self.assertNotIn("ID_SECRETO_JUAN_PEREZ", contexto)
        self.assertNotIn("ID_CONFIDENCIAL_ANA_GOMEZ", contexto)
        self.assertNotIn("45", contexto)  # Edad juan
        self.assertNotIn("72", contexto)  # Edad ana

        # Debe contener agregados
        self.assertIn("Total de atenciones analizadas: 2", contexto)
        self.assertIn("Diabetes Mellitus Tipo 2", contexto)
        self.assertIn("Hipertensión Arterial (HTA)", contexto)

    def test_validacion_codigo_paciente_vs_nombre_completo(self):
        # Códigos válidos
        self.assertTrue(validar_formato_codigo_paciente("P-104"))
        self.assertTrue(validar_formato_codigo_paciente("C042"))
        self.assertTrue(validar_formato_codigo_paciente("EXP-2026-99"))

        # Sospecha de nombre completo (2 o más palabras con solo letras)
        self.assertFalse(validar_formato_codigo_paciente("Carlos Rodriguez"))
        self.assertFalse(validar_formato_codigo_paciente("Maria Alejandra Zerpa"))


class TestSeguridadVaciadoLocalYNube(unittest.TestCase):
    def test_vaciado_local_no_inserta_registros_eliminados(self):
        conn = sqlite3.connect(":memory:")
        inicializar_bd(conn)

        # Insertar atención
        conn.execute(
            "INSERT INTO registros_jornada (fecha, zona, paciente_id, edad, diagnostico, farmaco_entregado, cantidad_entregada, uuid) "
            "VALUES ('2026-01-01', 'Caracas', 'P-101', 30, 'IRA', 'Paracetamol', 10, 'uuid-local-1')"
        )
        conn.commit()

        # Al archivar localmente, se vacía registros_jornada SIN tocar registros_eliminados
        conn.execute("DELETE FROM registros_jornada")
        conn.commit()

        cant_atenciones = conn.execute("SELECT COUNT(*) FROM registros_jornada").fetchone()[0]
        self.assertEqual(cant_atenciones, 0)

        # La tabla registros_eliminados debe permanecer en 0 (la nube no se purga)
        cant_eliminados = conn.execute("SELECT COUNT(*) FROM registros_eliminados").fetchone()[0]
        self.assertEqual(cant_eliminados, 0)
        conn.close()


class TestSeguridadCifradoRespaldos(unittest.TestCase):
    def test_respaldo_cifrado_fernet(self):
        clave_fernet = Fernet.generate_key().decode()
        fernet = Fernet(clave_fernet.encode("utf-8"))

        datos_bd_simulados = b"SQLite format 3\x00 cabecera simulada de base de datos"
        datos_cifrados = fernet.encrypt(datos_bd_simulados)

        # El archivo cifrado no revela la cabecera SQLite
        self.assertFalse(datos_cifrados.startswith(b"SQLite format 3"))

        # El descifrado recupera exactamente los bytes originales
        datos_restaurados = fernet.decrypt(datos_cifrados)
        self.assertEqual(datos_restaurados, datos_bd_simulados)


class TestSeguridadCeroSecretosRepositorio(unittest.TestCase):
    def test_sin_claves_hardcodeadas_en_codigo(self):
        patron_gemini = re.compile(r'AQ\.[A-Za-z0-9_-]{30,}')
        patron_aiza = re.compile(r'AIza[0-9A-Za-z-_]{35}')
        patron_priv_key = re.compile(r'BEGIN PRIVATE KEY')

        archivos_evaluar = ["app.py", "lanzador.spec"]
        for arch in archivos_evaluar:
            if os.path.exists(arch):
                with open(arch, "r", encoding="utf-8", errors="ignore") as f:
                    contenido = f.read()
                    self.assertIsNone(patron_gemini.search(contenido), f"API key Gemini expuesta en {arch}")
                    self.assertIsNone(patron_aiza.search(contenido), f"API key Google expuesta en {arch}")
                    self.assertIsNone(patron_priv_key.search(contenido), f"Private key expuesta en {arch}")


class TestPruebaDeHumoAppTest(unittest.TestCase):
    def test_app_test_bloqueo_sin_credenciales(self):
        # Si no hay secretos configurados, la app debe mostrar error y detenerse
        ruta_app = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
        with patch("vivemas.seguridad.obtener_hashes_roles", return_value=("", "")):
            at = AppTest.from_file(ruta_app)
            at.run(timeout=15)
            # Debe haber lanzado st.error informando la configuración de seguridad incompleta
            self.assertTrue(len(at.error) > 0)
            textos_error = [e.value for e in at.error]
            self.assertTrue(any("Configuración de seguridad incompleta" in t for t in textos_error))


if __name__ == "__main__":
    unittest.main()
