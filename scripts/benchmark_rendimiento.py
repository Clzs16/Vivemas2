# -*- coding: utf-8 -*-
"""Benchmark de rendimiento de arranque y ejecución (M6)."""

import time
import os

print("--- INICIANDO BENCHMARK M6 ---")

# 1. Medir tiempo de importación del paquete modular
t0 = time.perf_counter()
import vivemas
t_import_vivemas = (time.perf_counter() - t0) * 1000
print(f"1. Tiempo de importación de 'vivemas': {t_import_vivemas:.2f} ms")

# 2. Medir tiempo de importación de app.py
t0 = time.perf_counter()
import app
t_import_app = (time.perf_counter() - t0) * 1000
print(f"2. Tiempo de importación de 'app.py': {t_import_app:.2f} ms")

# 3. Medir tiempo de inicialización de BD SQLite (migraciones idempotentes)
t0 = time.perf_counter()
import sqlite3
conn = sqlite3.connect(":memory:")
vivemas.db.inicializar_bd(conn)
t_init_bd = (time.perf_counter() - t0) * 1000
print(f"3. Tiempo de inicialización y migración SQLite: {t_init_bd:.2f} ms")

# 4. Medir tiempo de cálculo de stock
t0 = time.perf_counter()
res_stk = vivemas.stock.calcular_stock_disponible_item("Paracetamol 500mg", conn=conn)
t_stock = (time.perf_counter() - t0) * 1000
print(f"4. Tiempo de consulta transaccional de stock: {t_stock:.2f} ms")

# 5. Medir tiempo de generación de Excel
t0 = time.perf_counter()
import pandas as pd
df_bench = pd.DataFrame({"med": ["Paracetamol", "Ibuprofeno"] * 50, "cant": [10, 20] * 50})
b_excel = vivemas.exportes.df_a_excel_bytes(df_bench)
t_excel = (time.perf_counter() - t0) * 1000
print(f"5. Tiempo de generación Excel (100 filas): {t_excel:.2f} ms")

# 6. Medir tiempo de AppTest headless
t0 = time.perf_counter()
from streamlit.testing.v1 import AppTest
ruta_app = os.path.abspath("app.py")
at = AppTest.from_file(ruta_app)
at.run(timeout=10)
t_apptest = (time.perf_counter() - t0) * 1000
print(f"6. Tiempo de renderizado inicial Streamlit AppTest: {t_apptest:.2f} ms")

print("--- BENCHMARK COMPLETADO EXITOSAMENTE ---")
