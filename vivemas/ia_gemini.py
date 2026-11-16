# -*- coding: utf-8 -*-
"""Integración con Google Gemini (genai), generación streaming segura y análisis multimodal de etiquetas."""

import os
import time
import json
import logging
import itertools
from typing import Optional, List, Dict, Any
import pandas as pd
import streamlit as st

from vivemas.config import DEFAULT_MODELOS_GEMINI
from vivemas.diagnosticos import explotar_diagnosticos

logger = logging.getLogger("vivemas.ia_gemini")

_modelos_env = os.environ.get("GEMINI_MODELOS", ",".join(DEFAULT_MODELOS_GEMINI))
MODELOS_GEMINI = [m.strip() for m in _modelos_env.split(",") if m.strip()]


def _es_error_api_key(err: Exception) -> bool:
    """Detecta si el error se debe a una API key inválida para suspender reintentos inmediatamente."""
    err_str = str(err).lower()
    return any(k in err_str for k in ["api_key_invalid", "api key not valid", "api key invalid", "invalid api key", "api_key"])


def _es_error_modelo_inexistente(err: Exception) -> bool:
    """Detecta errores de modelo no encontrado o no soportado (404/invalid)."""
    if _es_error_api_key(err):
        return False
    err_str = str(err).lower()
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code == 404:
        return True
    if code == 400 and any(k in err_str for k in ["model", "not found", "not_found", "not supported", "unknown model", "invalid model"]):
        return True
    return any(k in err_str for k in ["models/", "not found", "not_found", "is not supported", "unknown model", "invalid model", "does not exist"])


def _es_error_reintentable_espera(err: Exception) -> bool:
    """Detecta saturación o cuota (429/503) donde sí amerita reintento con backoff exponencial."""
    if _es_error_api_key(err):
        return False
    err_str = str(err).lower()
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code in (429, 503):
        return True
    return any(k in err_str for k in ["429", "503", "resource_exhausted", "quota", "rate limit", "unavailable", "overloaded"])


def resolver_api_key_gemini() -> str:
    """Resuelve la clave de API de Gemini según prioridad:
    1. st.secrets (secrets.toml)
    2. Variable de entorno GEMINI_API_KEY
    3. Sesión activa en memoria st.session_state (no persistida en disco)
    """
    try:
        k_sec = st.secrets.get("GEMINI_API_KEY", "")
        if k_sec and str(k_sec).strip():
            return str(k_sec).strip()
    except Exception:
        pass

    k_env = os.environ.get("GEMINI_API_KEY", "")
    if k_env and str(k_env).strip():
        return str(k_env).strip()

    try:
        from vivemas.db import obtener_config
        k_db = obtener_config("gemini_api_key", "")
        if k_db and str(k_db).strip():
            return str(k_db).strip()
    except Exception:
        pass

    k_ses = st.session_state.get("gemini_api_key_sesion", "")
    if k_ses and str(k_ses).strip():
        return str(k_ses).strip()

    return ""


def obtener_origen_api_key_gemini() -> str:
    """Identifica el origen exacto de la API Key activa para visibilidad clara en la interfaz."""
    try:
        k_sec = st.secrets.get("GEMINI_API_KEY", "")
        if k_sec and str(k_sec).strip():
            return "secrets.toml"
    except Exception:
        pass

    k_env = os.environ.get("GEMINI_API_KEY", "")
    if k_env and str(k_env).strip():
        return "Variable de Entorno (GEMINI_API_KEY)"

    try:
        from vivemas.db import obtener_config
        k_db = obtener_config("gemini_api_key", "")
        if k_db and str(k_db).strip():
            return "Configuración Local Permanente"
    except Exception:
        pass

    k_ses = st.session_state.get("gemini_api_key_sesion", "")
    if k_ses and str(k_ses).strip():
        return "Sesión de Usuario (No persistida)"

    return "No configurada"


def guardar_api_key_gemini_permanente(api_key: str) -> bool:
    """Guarda de forma permanente la Gemini API Key en SQLite y secrets.toml."""
    try:
        from vivemas.db import guardar_config
        key_limpia = api_key.strip()
        guardar_config("gemini_api_key", key_limpia)

        for s_path in [
            os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
            os.path.join(os.getcwd(), "dist", ".streamlit", "secrets.toml"),
        ]:
            dir_s = os.path.dirname(s_path)
            if not os.path.exists(dir_s):
                os.makedirs(dir_s, exist_ok=True)
            lineas = []
            if os.path.exists(s_path):
                with open(s_path, "r", encoding="utf-8") as f:
                    lineas = f.readlines()
            nuevas = []
            encontrado = False
            for l in lineas:
                if l.strip().startswith("GEMINI_API_KEY"):
                    nuevas.append(f'GEMINI_API_KEY = "{key_limpia}"\n')
                    encontrado = True
                else:
                    nuevas.append(l)
            if not encontrado:
                nuevas.append(f'GEMINI_API_KEY = "{key_limpia}"\n')
            with open(s_path, "w", encoding="utf-8") as f:
                f.writelines(nuevas)
        return True
    except Exception as e:
        logger.error(f"Error guardando API Key permanente: {e}")
        return False



def obtener_cliente_gemini(api_key: str):
    """Crea un cliente oficial de Google GenAI."""
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        logger.error(f"Error inicializando cliente de Gemini: {e}")
        return None


@st.cache_resource(ttl=3600)
def obtener_modelos_gemini_disponibles(api_key: str) -> List[str]:
    """Consulta client.models.list() una vez, filtra modelos con generateContent y aplica orden preferido."""
    if not api_key:
        return MODELOS_GEMINI
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        modelos_remotos = []
        for m in client.models.list():
            nom = m.name.replace("models/", "") if hasattr(m, "name") and m.name else str(m)
            supported = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None)
            if supported:
                if "generateContent" in supported:
                    modelos_remotos.append(nom)
            else:
                modelos_remotos.append(nom)
        if modelos_remotos:
            preferidos = [m for m in MODELOS_GEMINI if m in modelos_remotos]
            otros = [m for m in modelos_remotos if "flash" in m.lower() and m not in preferidos]
            resultado = preferidos + otros
            if resultado:
                return resultado
    except Exception as e:
        logger.warning(f"No se pudieron descubrir modelos de Gemini en vivo: {e}")
    return MODELOS_GEMINI


def construir_contexto_gemini_agregado(df_analisis: pd.DataFrame, prompt_usuario: str, filtro_txt: str = "Totalidad") -> str:
    """Construye contexto estadístico estrictamente agregado para Gemini.
    NUNCA envía paciente_id, edad exacta, nombres ni registros fila a fila."""
    total_pacientes = len(df_analisis) if df_analisis is not None else 0

    top_patologias = "Sin datos suficientes"
    if df_analisis is not None and "diagnostico" in df_analisis.columns and not df_analisis.empty:
        df_exp = explotar_diagnosticos(df_analisis)
        if not df_exp.empty:
            top_patologias = df_exp["diagnostico"].value_counts().head(10).to_string()

    top_farmacos = "Sin datos suficientes"
    if df_analisis is not None and "farmaco_entregado" in df_analisis.columns and "cantidad_entregada" in df_analisis.columns and not df_analisis.empty:
        top_farmacos = df_analisis.groupby("farmaco_entregado")["cantidad_entregada"].sum().sort_values(ascending=False).head(10).to_string()

    distribucion_zonas = "Sin datos suficientes"
    if df_analisis is not None and "zona" in df_analisis.columns and not df_analisis.empty:
        distribucion_zonas = df_analisis["zona"].value_counts().head(8).to_string()

    return f"""
    Eres un epidemiólogo y auditor farmacéutico del sistema de salud comunitaria 'Vive Más'.
    Responde de forma directa, concisa y profesional a la pregunta del usuario.
    Usa viñetas limpias y resalta los nombres y cantidades en negrita.

    DATOS ESTADÍSTICOS AGREGADOS ({filtro_txt}):
    - Total de atenciones analizadas: {total_pacientes}

    DISTRIBUCIÓN GEOGRÁFICA AGREGADA (ATENCIONES POR ZONA):
    {distribucion_zonas}

    TOP DIAGNÓSTICOS FRECUENTES (CONTEO AGREGADO):
    {top_patologias}

    TOP FÁRMACOS ENTREGADOS (SUMATORIA UNIDADES):
    {top_farmacos}

    PREGUNTA DEL USUARIO:
    {prompt_usuario}
    """


def abrir_stream_gemini(client, contexto: str, modelos: Optional[List[str]] = None, intentos: int = 2):
    """Abre el stream de Gemini con gestión de fallos (429/503 y suspensión por clave inválida)."""
    if client is None:
        return None
    lista_modelos = modelos if modelos else MODELOS_GEMINI
    for modelo in lista_modelos:
        for intento in range(intentos):
            try:
                it = iter(client.models.generate_content_stream(model=modelo, contents=contexto))
                primero = next(it)
                return itertools.chain([primero], it)
            except StopIteration:
                return None
            except Exception as e:
                logger.warning(f"Error abriendo stream con modelo '{modelo}' (intento {intento+1}/{intentos}): {e}")
                if _es_error_api_key(e):
                    logger.error("La API key de Gemini no es válida.")
                    st.error("La API key de Gemini no es válida")
                    return None
                elif _es_error_modelo_inexistente(e):
                    logger.info(f"Modelo '{modelo}' no disponible. Intentando siguiente.")
                    break
                elif _es_error_reintentable_espera(e):
                    if intento < intentos - 1:
                        time.sleep(2 ** intento)
                else:
                    if intento < intentos - 1:
                        time.sleep(1)
                    else:
                        break
    return None


def analizar_etiqueta_con_ia(archivo_imagen) -> Dict[str, Any]:
    """Analiza una foto de una caja o etiqueta médica usando Gemini IA para extraer datos estructurados."""
    if not archivo_imagen:
        return {}
    api_key = resolver_api_key_gemini()
    if not api_key:
        logger.warning("No hay API Key de Gemini disponible para analizar la etiqueta.")
        return {}
    try:
        from google import genai
        from google.genai import types
        client = obtener_cliente_gemini(api_key)
        nom = getattr(archivo_imagen, "name", "foto.jpg").lower()
        mime = "image/png" if nom.endswith(".png") else "image/jpeg"
        bytes_data = archivo_imagen.getvalue()

        prompt = """Eres un asistente farmacéutico. Analiza esta foto de una caja, código QR o etiqueta de medicamento y extrae estrictamente en formato JSON:
        {
          "caja": "Nombre o número de caja (ej: Caja 01), o 'Caja Donación'",
          "contenido": "Nombre del medicamento o insumo (ej: Amoxicilina 500mg)",
          "cantidad": número entero de unidades (ej: 50, 100) o 1 si no se especifica,
          "tipo_item": "Medicamento" o "Insumo Médico / Descartable" o "Material Quirúrgico",
          "lote_vencimiento": "Lote o fecha de vencimiento si aparece"
        }
        Responde SOLO el JSON sin texto adicional ni bloques extra."""

        modelos_consulta = obtener_modelos_gemini_disponibles(api_key)
        for modelo in modelos_consulta:
            for intento in range(2):
                try:
                    resp = client.models.generate_content(
                        model=modelo,
                        contents=[types.Part.from_bytes(data=bytes_data, mime_type=mime), prompt]
                    )
                    if resp and resp.text:
                        t = resp.text.strip()
                        if "```" in t:
                            for p in t.split("```"):
                                p = p.replace("json", "").strip()
                                if p.startswith("{") and p.endswith("}"):
                                    t = p
                                    break
                        return json.loads(t)
                except Exception as e_mod:
                    logger.warning(f"Error analizando etiqueta con modelo '{modelo}' (intento {intento+1}): {e_mod}")
                    if _es_error_api_key(e_mod):
                        st.error("La API key de Gemini no es válida")
                        return {}
                    elif _es_error_modelo_inexistente(e_mod):
                        break
                    elif _es_error_reintentable_espera(e_mod):
                        if intento < 1:
                            time.sleep(2)
                    else:
                        break
    except Exception as e:
        logger.error(f"Error general en analizar_etiqueta_con_ia: {e}", exc_info=True)
    return {}
