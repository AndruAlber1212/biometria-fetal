import streamlit as st
import psycopg2
import pandas as pd

# Configuración de página de Streamlit
st.set_page_config(page_title="Sistema de Biometría Fetal (SaMD)", layout="wide")

# URL de conexión a Neon
URL_DB = "postgresql://neondb_owner:npg_5NYJUZt3PoRf@ep-empty-mouse-b5pz0137-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

def conectar_db():
    return psycopg2.connect(URL_DB)

# Inicializar estados de sesión
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False
if 'operador_registrado' not in st.session_state:
    st.session_state.operador_registrado = False

# ==========================================
# FASE 1: PANTALLA DE LOGIN
# ==========================================
if not st.session_state.autenticado:
    st.title("🔒 Acceso al Sistema - Biometría Fetal")
    col1, _ = st.columns([1, 2])
    with col1:
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.button("Iniciar Sesión"):
            try:
                conn = conectar_db()
                cur = conn.cursor()
                cur.execute("SELECT nombre FROM usuarios WHERE usuario = %s AND password = %s", (u, p))
                res = cur.fetchone()
                conn.close()
                if res:
                    st.session_state.autenticado = True
                    st.session_state.user_default = res[0]
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos")
            except Exception as e:
                st.error(f"Error de conexión: {e}")
    st.stop()

# ==========================================
# FASE 2: REGISTRO DEL OPERADOR (Nombres, Apellidos, CI)
# ==========================================
if not st.session_state.operador_registrado:
    st.title("🩺 Registro del Operador en Turno")
    st.info("Ingrese los datos del profesional médico responsable antes de iniciar las ecografías.")
    
    with st.form("form_operador"):
        nombres_op = st.text_input("Nombres")
        apellidos_op = st.text_input("Apellidos")
        ci_op = st.text_input("Cédula de Identidad (CI)")
        
        btn_continuar = st.form_submit_button("Ingresar al Sistema Clínico")
        if btn_continuar:
            if nombres_op and apellidos_op and ci_op:
                st.session_state.operador_nombres = nombres_op
                st.session_state.operador_apellidos = apellidos_op
                st.session_state.operador_ci = ci_op
                st.session_state.operador_registrado = True
                st.rerun()
            else:
                st.warning("Por favor complete todos los campos: Nombres, Apellidos y CI.")
    st.stop()

# ==========================================
# FASE 3: APLICACIÓN PRINCIPAL Y BARRA LATERAL LIMPIA
# ==========================================

# Barra lateral limpia solo con info del operador y opción de ver base de datos
st.sidebar.title("Panel de Control")
st.sidebar.markdown(f"**Médico:** {st.session_state.operador_nombres} {st.session_state.operador_apellidos}")
st.sidebar.markdown(f"**CI:** {st.session_state.operador_ci}")

ver_db = st.sidebar.checkbox("📂 Ver Base de Datos de Estudios")

if st.sidebar.button("Cerrar Sesión / Cambiar Operador"):
    st.session_state.autenticado = False
    st.session_state.operador_registrado = False
    st.rerun()

st.title("🖥️ Sistema de Análisis de Biometría Fetal (SaMD)")

# Función de guardado automático en la base de datos
def guardar_estudio_automatico(op_nom, op_ape, op_ci, pac_nom, pac_id, pac_edad, fecha_est, pef, eg, fpp, pdf_link):
    try:
        conn = conectar_db()
        cur = conn.cursor()
        sql = """
            INSERT INTO estudios_ecograficos 
            (operador_nombre, operador_ci, paciente_nombre, paciente_id, paciente_edad, fecha_estudio, peso_fetal, edad_gestacional, fecha_probable_parto, pdf_generado) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        operador_completo = f"{op_nom} {op_ape}"
        cur.execute(sql, (operador_completo, op_ci, pac_nom, pac_id, pac_edad, fecha_est, pef, eg, fpp, pdf_link))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error al guardar en BD: {e}")
        return False

# Si el usuario activó la casilla en la barra lateral, muestra la tabla de la BD
if ver_db:
    st.subheader("📋 Historial General de Estudios Clínicos en PostgreSQL")
    try:
        conn = conectar_db()
        df_estudios = pd.read_sql("SELECT * FROM estudios_ecograficos ORDER BY id DESC", conn)
        conn.close()
        if not df_estudios.empty:
            st.dataframe(df_estudios, use_container_width=True)
        else:
            st.info("Aún no hay estudios registrados en la base de datos.")
    except Exception as e:
        st.error(f"Error al consultar la base de datos: {e}")
    st.stop("---") # Detiene la ejecución aquí para mostrar solo la tabla de la base de datos ordenadamente

# Interfaz única para cargar DICOM y procesar
st.markdown("### Carga de Archivo DICOM Ecográfico")
uploaded_file = st.file_uploader("Sube la imagen DICOM (.dcm) para procesar biometría", type=["dcm"])

if uploaded_file is not None:
    st.success("¡Archivo DICOM cargado correctamente!")
    
    # Simulación de extracción de metadatos y cálculo de IA (reemplaza esto con tu lógica real de procesamiento)
    paciente_nombre_ext = "Carla Mendoza"
    paciente_id_ext = "987654"
    paciente_edad_ext = "30 años"
    fecha_estudio_ext = "2026-10-10"
    peso_fetal_ext = "1520 g"
    edad_gestacional_ext = "30 semanas"
    fpp_ext = "2026-12-20"
    pdf_url_o_nombre = "Reporte_Biometria_Carla_Mendoza.pdf"
    
    # Guardado automático transparente en la base de datos de Neon
    guardado_ok = guardar_estudio_automatico(
        st.session_state.operador_nombres,
        st.session_state.operador_apellidos,
        st.session_state.operador_ci,
        paciente_nombre_ext,
        paciente_id_ext,
        paciente_edad_ext,
        fecha_estudio_ext,
        peso_fetal_ext,
        edad_gestacional_ext,
        fpp_ext,
        pdf_url_o_nombre
    )
    
    if guardado_ok:
        st.info("✅ Los datos del estudio y el reporte se han guardado automáticamente en la base de datos.")
