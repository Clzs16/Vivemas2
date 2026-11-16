#!/usr/bin/env python3
"""Script interactivo seguro para generar hashes de contraseñas con sal y coste (PBKDF2-HMAC-SHA256).

Uso:
    python scripts/generar_hash.py
"""

import getpass
import hashlib
import secrets
import sys

ITERACIONES = 600000

def generar_hash(password: str) -> str:
    """Genera un hash con sal aleatoria de 16 bytes y 600.000 iteraciones."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), ITERACIONES).hex()
    return f"pbkdf2_sha256${ITERACIONES}${salt}${h}"

def main():
    print("=" * 60)
    print("  Generador de Hashes Seguros - Vive Más")
    print("=" * 60)
    print("Este script crea un hash PBKDF2-HMAC-SHA256 con 600.000 iteraciones")
    print("adecuado para configurar HASH_CAMPO o HASH_COORDINADOR.\n")

    p1 = getpass.getpass("Introduce la contraseña: ")
    if not p1 or len(p1.strip()) < 6:
        print("Error: La contraseña debe tener al menos 6 caracteres.", file=sys.stderr)
        sys.exit(1)

    p2 = getpass.getpass("Confirma la contraseña: ")
    if p1 != p2:
        print("Error: Las contraseñas no coinciden.", file=sys.stderr)
        sys.exit(1)

    h_final = generar_hash(p1.strip())
    print("\n" + "-" * 60)
    print("Hash generado con éxito. Cópialo y pégalo en tu secrets.toml:")
    print("-" * 60)
    print(h_final)
    print("-" * 60)
    print("Ejemplo en .streamlit/secrets.toml:")
    print(f'HASH_CAMPO = "{h_final}"   # o HASH_COORDINADOR')
    print("-" * 60 + "\n")

if __name__ == "__main__":
    main()
