# -*- coding: utf-8 -*-
"""Utilidades de texto, normalización de fármacos y validación de identificadores."""

import re
import unicodedata


def normalizar_texto(texto: str) -> str:
    """Normaliza texto eliminando acentos y convirtiendo a minúsculas para comparaciones."""
    if not texto:
        return ""
    s = unicodedata.normalize('NFD', str(texto))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.strip().lower()


def normalizar_nombre_farmaco(nombre: str) -> str:
    """Normaliza nombres de fármacos para comparación uniforme:
    minúsculas, sin acentos, espacios colapsados y sin espacio entre número y unidad ('500 mg' == '500mg')."""
    if not nombre:
        return ""
    s = str(nombre).strip().lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = re.sub(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)', r'\1\2', s)
    s = re.sub(r'\s*/\s*', '/', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _detectar_zona_en_texto(zonas_disponibles, texto_pregunta: str) -> str | None:
    """Detecta la mención exacta de una zona en una consulta mediante palabra completa e insensible a acentos/casing."""
    if not texto_pregunta or not zonas_disponibles:
        return None
    s_txt = unicodedata.normalize('NFD', str(texto_pregunta))
    t_clean = ''.join(c for c in s_txt if unicodedata.category(c) != 'Mn').lower()
    for z in zonas_disponibles:
        if not z or not str(z).strip():
            continue
        z_str = str(z).strip()
        s_z = unicodedata.normalize('NFD', z_str)
        z_clean = ''.join(c for c in s_z if unicodedata.category(c) != 'Mn').lower()
        patron = r'\b' + re.escape(z_clean) + r'\b'
        if re.search(patron, t_clean):
            return z_str
    return None


def validar_formato_codigo_paciente(codigo: str) -> bool:
    """Verifica si el código parece un nombre y apellido completo para advertir al usuario (evitar PII directa)."""
    if not codigo:
        return True
    partes = codigo.strip().split()
    if len(partes) >= 2 and all(p.isalpha() and len(p) >= 3 for p in partes):
        return False
    return True
