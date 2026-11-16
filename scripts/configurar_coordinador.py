# -*- coding: utf-8 -*-
"""Script para generar clave y hash del rol coordinador de forma segura y local."""

import os
import sys
import secrets
import string

directorio_raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, directorio_raiz)

from vivemas.seguridad import generar_hash_pbkdf2

def main():
    # Caracteres legibles y seguros sin caracteres ambiguos (como 0/O, 1/l)
    chars = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!#$%"
    pwd = "".join(secrets.choice(chars) for _ in range(12))

    h = generar_hash_pbkdf2(pwd)

    # 1. Guardar clave en archivo local privado fuera de Git
    ruta_clave = os.path.join(directorio_raiz, ".streamlit", "clave_acceso_coordinador.txt")
    os.makedirs(os.path.dirname(ruta_clave), exist_ok=True)
    with open(ruta_clave, "w", encoding="utf-8") as f:
        f.write("CLAVE DE ACCESO - ROL COORDINADOR\n")
        f.write("===================================\n")
        f.write(f"Contrasena: {pwd}\n\n")
        f.write("Instrucciones:\n")
        f.write("1. Usa esta contrasena en la pantalla de inicio de la aplicacion para entrar como Coordinador.\n")
        f.write("2. Una vez anotada o memorizada, puedes borrar este archivo de texto.\n")

    # 2. Agregar HASH_COORDINADOR a .streamlit/secrets.toml
    ruta_sec = os.path.join(directorio_raiz, ".streamlit", "secrets.toml")
    lineas = []
    if os.path.exists(ruta_sec):
        with open(ruta_sec, "r", encoding="utf-8") as f:
            lineas = [l for l in f.readlines() if not l.startswith("HASH_COORDINADOR")]

    lineas.append(f'HASH_COORDINADOR = "{h}"\n')
    with open(ruta_sec, "w", encoding="utf-8") as f:
        f.writelines(lineas)

    # 3. Sincronizar a dist/.streamlit/secrets.toml
    ruta_dist_sec = os.path.join(directorio_raiz, "dist", ".streamlit", "secrets.toml")
    os.makedirs(os.path.dirname(ruta_dist_sec), exist_ok=True)
    with open(ruta_dist_sec, "w", encoding="utf-8") as f:
        f.writelines(lineas)

    print("CONFIGURACION_EXITOSA")

if __name__ == "__main__":
    main()
