# -*- coding: utf-8 -*-
"""Vive Más - Sistema de Farmacovigilancia y Gestión de Jornadas Médicas de Campo.

Punto de entrada modular y delgado. Re-exporta la API pública completa para máxima
compatibilidad con suites de pruebas (pytest) y lanzadores ejecutables.
"""

import os
import streamlit as st

# 1. Configuración, constantes y estado de sesión
from vivemas.config import (
    SS,
    NOMBRE_BD_LOCAL,
    CARPETA_RESPALDOS,
    MAX_COPIAS_SEGURIDAD,
    VERSION_ESQUEMA_ACTUAL,
    DEFAULT_MODELOS_GEMINI,
    LISTA_FARMACOS_COMUNES,
    ZONAS_PREDETERMINADAS,
    obtener_secreto,
    obtener_ruta_recurso,
    obtener_ruta_bd,
    obtener_ruta_firebase,
    obtener_ip_local,
    init_estado,
)
leer_secreto = obtener_secreto

# 2. Normalización de textos y fármacos
from vivemas.textos import (
    normalizar_texto,
    normalizar_nombre_farmaco,
    _detectar_zona_en_texto,
    validar_formato_codigo_paciente,
)

# 3. Diagnósticos y comorbilidades
from vivemas.diagnosticos import (
    CATEGORIAS_DIAGNOSTICAS,
    normalizar_diagnostico,
    explotar_diagnosticos,
)

# 4. Sanitización y exportaciones tabulares
from vivemas.exportes import (
    sanitizar_dataframe_para_export,
    sanitizar_dataframe_de_import,
    df_a_excel_bytes,
    leer_archivo_tabular_unificado,
)

# 5. Base de datos SQLite, migraciones y respaldos cifrados
from vivemas.db import (
    conectar_bd,
    obtener_conexion_bd,
    obtener_version_bd,
    establecer_version_bd,
    _aplicar_esquema_bd,
    inicializar_bd,
    asegurar_bd_inicializada,
    guardar_config,
    obtener_config,
    crear_respaldo_bd,
    _demonio_respaldo_automatico,
    iniciar_servicio_autorespaldo,
    cargar_registros_local,
    cargar_stock_donaciones,
    cargar_registros_historicos,
)
RUTA_BD = obtener_ruta_bd()

# 6. Lógica de stock y transacciones
from vivemas.stock import (
    calcular_stock_disponible_item,
    eliminar_atencion_individual,
    vaciar_stock,
)

# 7. Autenticación PBKDF2 y seguridad por roles
from vivemas.seguridad import (
    generar_hash_pbkdf2,
    verificar_password_pbkdf2,
    obtener_hashes_roles,
    tiene_rol,
    requiere_rol,
    registrar_auditoria,
    verificar_acceso,
)

# 8. Sincronización con Firebase Firestore
from vivemas.sync_firebase import (
    hay_conexion_internet,
    conectar_firestore,
    ejecutar_sincronizacion_pendientes,
    obtener_resumen_jornadas_firebase,
    consultar_registros_firebase,
    contar_pendientes_sincronizacion,
    _a_entero,
)

# 9. Google Gemini IA
from vivemas.ia_gemini import (
    MODELOS_GEMINI,
    _es_error_api_key,
    _es_error_modelo_inexistente,
    _es_error_reintentable_espera,
    resolver_api_key_gemini,
    obtener_origen_api_key_gemini,
    obtener_cliente_gemini,
    obtener_modelos_gemini_disponibles,
    construir_contexto_gemini_agregado,
    abrir_stream_gemini,
    analizar_etiqueta_con_ia,
)

# 10. Interfaz de usuario y pestañas
from vivemas.ui.estilos import aplicar_estilos_tema
from vivemas.ui.componentes import render_barra_superior_estado, paginar_dataframe
from vivemas.ui.sidebar import render_sidebar
from vivemas.ui.registro import render_tab_registro
from vivemas.ui.stock_nuevo import (
    render_tab_stock_nuevo,
    decodificar_qr_imagen,
    parsear_texto_qr_caja,
)
from vivemas.ui.control_stock import render_tab_control_stock
from vivemas.ui.jornadas_anteriores import (
    render_tab_jornadas_anteriores,
    limpiar_fila_historica,
    uuid_fila,
)
from vivemas.ui.auditoria_ia import (
    render_tab_auditoria_ia,
    cargar_datos_auditoria_completa,
)
from vivemas.ui.reportes import render_tab_reportes


# --------------------------------------------------------------------------
# ORQUESTACIÓN PRINCIPAL STREAMLIT
# --------------------------------------------------------------------------
ruta_logo_fav = obtener_ruta_recurso("logo.ico")
st.set_page_config(
    page_title="Vive Más - Sistema de Farmacovigilancia",
    page_icon=ruta_logo_fav if os.path.exists(ruta_logo_fav) else "🏥",
    layout="wide"
)

# Inicializar servicio y base de datos
iniciar_servicio_autorespaldo()
asegurar_bd_inicializada()
init_estado()

# Guarda de autenticación obligatoria
if not verificar_acceso():
    st.stop()

# Inyección de estilos de campo (modo oscuro / luz baja nocturna / alto contraste)
aplicar_estilos_tema(st.session_state.get(SS.TEMA, "oscuro"))

# Imagen 'icono de vivemas' ocupando la parte superior
ruta_icono_vivemas = obtener_ruta_recurso("iconovivemas.jpeg")
if not os.path.exists(ruta_icono_vivemas):
    for candidata in [
        "iconovivemas.jpeg",
        "icono_vivemas.jpeg",
        os.path.join("dist", "iconovivemas.jpeg"),
        os.path.join("dist", "icono_vivemas.jpeg"),
        os.path.join("..", "iconovivemas.jpeg"),
        obtener_ruta_recurso("icono_vivemas.ico"),
    ]:
        if os.path.exists(candidata):
            ruta_icono_vivemas = os.path.abspath(candidata)
            break

if os.path.exists(ruta_icono_vivemas):
    c_sp1, c_sp2, c_sp3 = st.columns([1, 1, 1])
    with c_sp2:
        st.html(
            """
            <style>
            div[data-testid="stImage"] {
                display: flex !important;
                justify-content: center !important;
                align-items: center !important;
            }
            div[data-testid="stImage"] > img {
                margin-left: auto !important;
                margin-right: auto !important;
                display: block !important;
            }
            </style>
            """
        )
        st.image(ruta_icono_vivemas, width=200)


# Barra con estado de red, cola de sincronización y respaldo
render_barra_superior_estado()

# Barra lateral de configuración y jornada activa
render_sidebar()

# Pestañas principales accesibles para todos los usuarios autenticados
t_reg, t_new_stk, t_ctrl_stk, t_hist, t_ia, t_rep = st.tabs([
    "🩺 Registrar Atención",
    "📦 Registrar Nuevo Stock",
    "📊 Control de Stock",
    "📁 Jornadas Anteriores",
    "🧠 Auditoría IA",
    "📈 Reportes y Cierre"
])
with t_reg:
    render_tab_registro()
with t_new_stk:
    render_tab_stock_nuevo()
with t_ctrl_stk:
    render_tab_control_stock()
with t_hist:
    render_tab_jornadas_anteriores()
with t_ia:
    render_tab_auditoria_ia()
with t_rep:
    render_tab_reportes()
