#!/usr/bin/env python3
"""Script para restaurar un respaldo cifrado (.db.enc) a una base de datos SQLite (.db).

Uso:
    python scripts/restaurar_backup.py <archivo_cifrado.db.enc> <destino.db>
"""

import sys
import os
import getpass
from cryptography.fernet import Fernet

def main():
    if len(sys.argv) < 3:
        print("Uso: python scripts/restaurar_backup.py <archivo_respaldo.db.enc> <destino.db>")
        sys.exit(1)

    origen = sys.argv[1]
    destino = sys.argv[2]

    if not os.path.exists(origen):
        print(f"Error: El archivo de origen '{origen}' no existe.", file=sys.stderr)
        sys.exit(1)

    clave = os.environ.get("CLAVE_CIFRADO_BACKUP")
    if not clave:
        clave = getpass.getpass("Introduce la clave Fernet de cifrado (CLAVE_CIFRADO_BACKUP): ").strip()

    try:
        fernet = Fernet(clave.encode("utf-8") if isinstance(clave, str) else clave)
    except Exception as e:
        print(f"Error al inicializar la clave de descifrado: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(origen, "rb") as f_in:
            datos_cifrados = f_in.read()

        datos_descifrados = fernet.decrypt(datos_cifrados)

        with open(destino, "wb") as f_out:
            f_out.write(datos_descifrados)

        print(f"Éxito: Respaldo descifrado y restaurado correctamente en '{destino}'.")
    except Exception as e:
        print(f"Error al descifrar el archivo de respaldo: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
