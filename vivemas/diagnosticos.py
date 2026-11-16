# -*- coding: utf-8 -*-
"""Normalización clínica de diagnósticos, extracción de comorbilidades y patrones epidemiológicos."""

import re
import unicodedata
import pandas as pd


CATEGORIAS_DIAGNOSTICAS = [
    ("Hipertensión Arterial (HTA)", [
        r'\bpresion\s+arterial\s+alta\b',
        r'\bhta\b',
        r'\bhipertens\w*',
        r'\bpresion\s+alta\b'
    ]),
    ("Diabetes Mellitus Tipo 2", [
        r'\bdiabetes\w*',
        r'\bdm2\b',
        r'\bdm\b',
        r'\bazucar\b',
        r'\bglicemia\s+alta\b'
    ]),
    ("Infección Respiratoria Aguda (IRA)", [
        r'\brespiratori\w*',
        r'\bgripe\b',
        r'\btos\b',
        r'\bira\b',
        r'\bresfriad\w*',
        r'\bfaringitis\b',
        r'\bbronquitis\b'
    ]),
    ("Gastroenteritis / Diarrea Aguda", [
        r'\bgastro\w*',
        r'\bdiarrea\w*',
        r'\bvomit\w*',
        r'\bestomac\w*'
    ]),
    ("Cefalea / Síndrome Migrañoso", [
        r'\bcefalea\w*',
        r'\bmigran\w*',
        r'\bdolor\s+de\s+cabeza\b'
    ]),
    ("Lumbalgia / Dolores Osteomusculares", [
        r'\blumb\w*',
        r'\bespalda\b',
        r'\bmuscul\w*',
        r'\barticular\w*'
    ]),
    ("Control Prenatal / Embarazo", [
        r'\bprenatal\w*',
        r'\bembaraz\w*',
        r'\bgestac\w*'
    ]),
    ("Control Pediátrico / Niño Sano", [
        r'\bpediatr\w*',
        r'\bnino\s+sano\b',
        r'\bcontrol\s+pediatric\w*'
    ]),
    ("Dermatitis / Alergia Cutánea", [
        r'\bdermat\w*',
        r'\balerg\w*',
        r'\bpiel\b',
        r'\brash\b'
    ]),
    ("Asma Bronquial / Broncoespasmo", [
        r'\basma\b',
        r'\bbroncoespas\w*'
    ]),
    ("Parasitosis Intestinal", [
        r'\bparasit\w*',
        r'\blombriz\w*',
        r'\bamiba\w*'
    ]),
    ("Anemia / Déficit Nutricional", [
        r'\banemia\b',
        r'\bhemoglobina\s+baja\b'
    ])
]

PALABRAS_NEGACION = {'sin', 'no', 'niega', 'descarta', 'descarto', 'descartar', 'descartada', 'descartado'}


def normalizar_diagnostico(diag_txt: str) -> str:
    """Estandariza sinónimos y formatos clínicos con límites de palabra (regex \\b) y sin acentos.
    Soporta comorbilidades múltiples (unidas con ' + '), detecta negaciones previas
    y preserva el texto original limpio si no hay coincidencia."""
    t = str(diag_txt or "").strip()
    if not t:
        return ""
    s = unicodedata.normalize('NFD', t)
    t_clean = ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()

    matches = []
    for cat_nombre, patrones in CATEGORIAS_DIAGNOSTICAS:
        pos_min = None
        for p in patrones:
            for m in re.finditer(p, t_clean):
                texto_previo = t_clean[:m.start()]
                palabras_previas = re.findall(r'\b\w+\b', texto_previo)[-3:]
                if any(w in PALABRAS_NEGACION for w in palabras_previas):
                    continue  # Coincidencia negada: ignorar
                if pos_min is None or m.start() < pos_min:
                    pos_min = m.start()
        if pos_min is not None:
            matches.append((pos_min, cat_nombre))

    if matches:
        matches.sort(key=lambda x: x[0])
        encontradas = []
        vistos = set()
        for _, cat in matches:
            if cat not in vistos:
                vistos.add(cat)
                encontradas.append(cat)
        return " + ".join(encontradas)
    return t


def explotar_diagnosticos(df: pd.DataFrame) -> pd.DataFrame:
    """Separa comorbilidades unidas por ' + ' en filas individuales sin duplicar el total de atenciones."""
    if df is None or df.empty or "diagnostico" not in df.columns:
        return df if df is not None else pd.DataFrame()
    df_copia = df.copy()
    df_copia["diagnostico"] = df_copia["diagnostico"].astype(str).str.split(r"\s*\+\s*")
    df_exp = df_copia.explode("diagnostico")
    df_exp["diagnostico"] = df_exp["diagnostico"].astype(str).str.strip()
    df_exp = df_exp[df_exp["diagnostico"].str.len() > 0]
    return df_exp
