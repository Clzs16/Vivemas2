# -*- coding: utf-8 -*-
"""Sanitización de fórmulas, exportación a Excel unificada y lectura tabular robusta."""

import io
from typing import Dict, List, Optional, Union
import pandas as pd


def sanitizar_dataframe_para_export(df: pd.DataFrame) -> pd.DataFrame:
    """Neutraliza inyección de fórmulas CSV/Excel ante '= + - @'."""
    if df is None or df.empty:
        return df
    df_clean = df.copy()
    for col in df_clean.select_dtypes(include=["object", "string"]).columns:
        df_clean[col] = df_clean[col].apply(
            lambda v: f"'{v}" if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@", "\t", "\r") else v
        )
    return df_clean


def sanitizar_dataframe_de_import(df: pd.DataFrame) -> pd.DataFrame:
    """Remueve comillas simples protectoras de celdas importadas."""
    if df is None or df.empty:
        return df
    df_clean = df.copy()
    for col in df_clean.select_dtypes(include=["object", "string"]).columns:
        df_clean[col] = df_clean[col].apply(
            lambda v: v[1:] if isinstance(v, str) and v.startswith("'") and len(v) > 1 and v[1] in ("=", "+", "-", "@") else v
        )
    return df_clean


def df_a_excel_bytes(
    datos: Union[pd.DataFrame, Dict[str, pd.DataFrame]],
    nombre_hoja_defecto: str = "Datos",
    sanitizar: bool = True
) -> bytes:
    """Genera bytes de Excel (.xlsx) a partir de un DataFrame o un diccionario de DataFrames por hoja."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if isinstance(datos, pd.DataFrame):
            df_proc = sanitizar_dataframe_para_export(datos) if (sanitizar and not datos.empty) else datos
            df_proc.to_excel(writer, index=False, sheet_name=nombre_hoja_defecto[:31])
        elif isinstance(datos, dict):
            hojas_agregadas = 0
            for hoja, df_h in datos.items():
                if df_h is not None:
                    nombre_hoja = str(hoja)[:31].strip() or "Hoja"
                    df_proc = sanitizar_dataframe_para_export(df_h) if (sanitizar and not df_h.empty) else df_h
                    df_proc.to_excel(writer, index=False, sheet_name=nombre_hoja)
                    hojas_agregadas += 1
            if hojas_agregadas == 0:
                pd.DataFrame({"Info": ["Sin datos"]}).to_excel(writer, index=False, sheet_name=nombre_hoja_defecto[:31])
    return output.getvalue()


def leer_archivo_tabular_unificado(
    archivo_o_buffer,
    columnas_requeridas: Optional[List[str]] = None
) -> tuple[pd.DataFrame, Optional[str]]:
    """Lee un archivo subido (CSV, XLSX o XLS) con detección automática de codificación.
    Retorna (df_resultado, mensaje_error). Si es exitoso, mensaje_error es None."""
    if archivo_o_buffer is None:
        return pd.DataFrame(), "Archivo no proporcionado"

    nombre = getattr(archivo_o_buffer, "name", "archivo").lower()
    df = None
    error = None

    try:
        if nombre.endswith(".csv"):
            contenido = archivo_o_buffer.read()
            archivo_o_buffer.seek(0)
            exito = False
            for codificacion in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
                try:
                    df = pd.read_csv(io.BytesIO(contenido), encoding=codificacion)
                    exito = True
                    break
                except UnicodeDecodeError:
                    continue
                except Exception:
                    break
            if not exito or df is None:
                archivo_o_buffer.seek(0)
                df = pd.read_csv(archivo_o_buffer, sep=None, engine="python")
        else:
            df = pd.read_excel(archivo_o_buffer)

        if df is not None:
            df.columns = [str(c).strip().lower() for c in df.columns]
            df = sanitizar_dataframe_de_import(df)

            if columnas_requeridas:
                cols_req_norm = [c.strip().lower() for c in columnas_requeridas]
                faltantes = [c for c in cols_req_norm if c not in df.columns]
                if faltantes:
                    return df, f"El archivo no contiene las columnas requeridas: {', '.join(faltantes)}"

            return df, None
    except Exception as e:
        error = f"Error al procesar el archivo: {e}"

    return pd.DataFrame(), error
