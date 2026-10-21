import streamlit as st
import psycopg2
import pydicom
import cv2
import numpy as np
import math
from datetime import datetime, timedelta
from PIL import Image
from fpdf import FPDF
from ultralytics import YOLO
import io
import speech_recognition as sr
from audio_recorder_streamlit import audio_recorder
import pandas as pd
import altair as alt

# 1. CONFIGURACIÓN INICIAL DE LA PÁGINA
st.set_page_config(page_title="Sistema de Biometría Fetal (SaMD)", page_icon="icono.jpg", layout="wide")

URL_DB = "postgresql://neondb_owner:npg_5NYJUZt3PoRf@ep-empty-mouse-b5pz0137-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require"

def conectar_db():
    conn = psycopg2.connect(URL_DB)
    try:
        cur = conn.cursor()
        cur.execute("""
            ALTER TABLE estudios_ecograficos 
            ADD COLUMN IF NOT EXISTS dbp REAL,
            ADD COLUMN IF NOT EXISTS cc REAL,
            ADD COLUMN IF NOT EXISTS ca REAL,
            ADD COLUMN IF NOT EXISTS lf REAL,
            ADD COLUMN IF NOT EXISTS observaciones TEXT,
            ADD COLUMN IF NOT EXISTS conclusiones TEXT,
            ADD COLUMN IF NOT EXISTS img_head BYTEA,
            ADD COLUMN IF NOT EXISTS img_body BYTEA,
            ADD COLUMN IF NOT EXISTS pdf_bytes BYTEA;
        """)
        conn.commit()
        cur.close()
    except:
        pass
    return conn

# --- INICIALIZAR ESTADOS DE SESIÓN ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False
if 'rol' not in st.session_state:
    st.session_state.rol = None
if 'usuario_actual' not in st.session_state:
    st.session_state.usuario_actual = None
if 'operador_registrado' not in st.session_state:
    st.session_state.operador_registrado = False
if 'estudio_procesado' not in st.session_state:
    st.session_state.estudio_procesado = False

# ESTILOS VISUALES
hide_menu_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stApp [data-testid="stToolbar"] {display: none;}
    </style>
"""
st.markdown(hide_menu_style, unsafe_allow_html=True)


# =========================================================
# PANTALLA DE LOGIN MULTI-ROL
# =========================================================
if not st.session_state.autenticado:
    st.title("Acceso al Sistema - Biometría Fetal")
    col1, _ = st.columns([1, 2])
    with col1:
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            try:
                conn = conectar_db()
                cur = conn.cursor()
                cur.execute("SELECT usuario, rol FROM usuarios WHERE usuario = %s AND password = %s", (u, p))
                res = cur.fetchone()
                cur.close()
                conn.close()
                if res:
                    st.session_state.autenticado = True
                    st.session_state.usuario_actual = res[0]
                    st.session_state.rol = res[1].lower() if res[1] else 'ecografista'
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas")
            except Exception as e:
                st.error(f"Error de conexión: {e}")
    st.stop()


# =========================================================
# BARRA LATERAL COMÚN (CERRAR SESIÓN)
# =========================================================
st.sidebar.markdown(f" **Usuario:** {st.session_state.usuario_actual}")
st.sidebar.markdown(f" **Rol:** `{st.session_state.rol.upper()}`")
if st.sidebar.button("🔴 Cerrar Sesión", use_container_width=True):
    st.session_state.autenticado = False
    st.session_state.rol = None
    st.session_state.usuario_actual = None
    st.session_state.operador_registrado = False
    st.session_state.estudio_procesado = False
    st.rerun()

st.sidebar.divider()


# =========================================================
# 1. ROL: OPERADOR (GESTIÓN DE USUARIOS)
# =========================================================
if st.session_state.rol == "operador":
    st.title("Panel de Administración - Operador")
    st.subheader("Gestión de Personal y Cuentas de Acceso")
    
    tab_crear, tab_gestionar = st.tabs(["➕ Registrar Nuevo Usuario", "Modificar / Eliminar Usuarios"])
    
    with tab_crear:
        with st.form("form_crear_usuario"):
            c1, c2 = st.columns(2)
            with c1:
                nom = st.text_input("Nombre")
                usr = st.text_input("Nombre de Usuario (Login)")
                pwd = st.text_input("Contraseña", type="password")
            with c2:
                ape = st.text_input("Apellido")
                ci = st.text_input("Cédula de Identidad (CI)")
                rol_sel = st.selectbox("Rol Asignado", ["ecografista", "revisor", "operador"])
                
            if st.form_submit_button("Guardar Usuario"):
                if nom and ape and usr and pwd and ci:
                    try:
                        conn = conectar_db()
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT INTO usuarios (nombre, apellido, usuario, password, ci, rol)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (nom, ape, usr, pwd, ci, rol_sel))
                        conn.commit()
                        cur.close()
                        conn.close()
                        st.success(f"✅ Usuario '{usr}' creado exitosamente.")
                    except Exception as e:
                        st.error(f"Error al registrar: {e}")
                else:
                    st.warning("Completa todos los campos.")
                    
    with tab_gestionar:
        st.markdown("### Listado y Edición de Usuarios")
        try:
            conn = conectar_db()
            cur = conn.cursor()
            cur.execute("SELECT id, nombre, apellido, usuario, rol, ci FROM usuarios WHERE usuario != 'admin'")
            lista_u = cur.fetchall()
            
            if lista_u:
                opciones_u = {f"{u[3]} ({u[1]} {u[2]} - Rol: {u[4]})": u[0] for u in lista_u}
                sel_u = st.selectbox("Seleccione un usuario para modificar o eliminar:", list(opciones_u.keys()))
                id_sel = opciones_u[sel_u]
                
                cur.execute("SELECT nombre, apellido, usuario, password, ci, rol FROM usuarios WHERE id = %s", (id_sel,))
                dt = cur.fetchone()
                
                sub_tab1, sub_tab2 = st.tabs(["Editar Datos", "Eliminar Usuario"])
                
                with sub_tab1:
                    with st.form("form_edit_u"):
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            en = st.text_input("Nombre", value=dt[0])
                            eu = st.text_input("Usuario", value=dt[2])
                            ep = st.text_input("Contraseña", value=dt[3], type="password")
                        with ec2:
                            ea = st.text_input("Apellido", value=dt[1])
                            eci = st.text_input("CI", value=dt[4])
                            
                            roles_disponibles = ["ecografista", "revisor", "operador"]
                            rol_bd = dt[5].lower() if dt[5] in roles_disponibles else "ecografista"
                            idx_rol = roles_disponibles.index(rol_bd)
                            er = st.selectbox("Rol", roles_disponibles, index=idx_rol)
                        
                        if st.form_submit_button("Actualizar Cambios"):
                            cur.execute("UPDATE usuarios SET nombre=%s, apellido=%s, usuario=%s, password=%s, ci=%s, rol=%s WHERE id=%s", (en, ea, eu, ep, eci, er, id_sel))
                            conn.commit()
                            st.success("✅ ¡Datos del usuario actualizados correctamente!")
                            st.rerun()
                            
                with sub_tab2:
                    st.warning("Esta acción eliminará el usuario permanentemente.")
                    if st.button("Eliminar este Usuario", type="primary"):
                        cur.execute("DELETE FROM usuarios WHERE id = %s", (id_sel,))
                        conn.commit()
                        st.success("Usuario eliminado.")
                        st.rerun()
            else:
                st.info("No hay usuarios editables registrados.")
            cur.close()
            conn.close()
        except Exception as e:
            st.error(f"Error: {e}")

    st.stop()


# =========================================================
# FUNCIÓN GENERADORA DE PDF CON DOBLE FIRMA Y DIAGNÓSTICO
# =========================================================
def generar_pdf(medico_ecografista, medico_revisor, paci_nombre, paci_edad, paci_sexo, paci_fecha, modalidad, 
                dbp, cc, ca, lf, pef, eg, obs, conclusiones, diag_percentil, img_head, img_body):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, "REPORTE CLINICO DE BIOMETRIA FETAL", ln=True, align='C')
    pdf.ln(5)
    
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "1. Datos del Estudio y Personal Médico", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.cell(0, 6, f"MÉDICO ECOGRAFISTA: {medico_ecografista}", ln=True)
    if medico_revisor and medico_revisor.strip():
        pdf.cell(0, 6, f"MÉDICO REVISOR: {medico_revisor}", ln=True)
    pdf.cell(0, 6, f"Paciente: {paci_nombre}", ln=True)
    pdf.cell(0, 6, f"Edad: {paci_edad}  |  Sexo: {paci_sexo}  |  Modalidad: {modalidad}", ln=True)
    pdf.cell(0, 6, f"Fecha de Estudio: {paci_fecha}", ln=True)
    pdf.cell(0, 6, f"Fecha de Revisión: {datetime.now().strftime('%d/%m/%Y')}", ln=True)
    pdf.ln(5)
    
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "2. Observaciones Médicas", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.multi_cell(0, 6, obs if obs.strip() else "Sin observaciones adicionales dictadas.")
    pdf.ln(5)
    
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "3. Resultados Biométricos", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.cell(90, 6, f"DBP: {dbp:.1f} mm" if dbp else "DBP: --", ln=False)
    pdf.cell(90, 6, f"CC: {cc:.1f} mm" if cc else "CC: --", ln=True)
    pdf.cell(90, 6, f"CA: {ca:.1f} mm" if ca else "CA: --", ln=False)
    pdf.cell(90, 6, f"LF: {lf:.1f} mm" if lf else "LF: --", ln=True)
    pdf.ln(2)
    pdf.set_font("helvetica", 'B', 11)
    pdf.cell(0, 6, f"Peso Estimado (PEF): {pef}  |  Edad Gestacional (EG): {eg}", ln=True)
    pdf.ln(3)
    
    # Análisis de Percentil Detallado en PDF
    pdf.set_font("helvetica", 'B', 11)
    pdf.cell(0, 6, "Evaluación Percentilar y Crecimiento Fetal:", ln=True)
    pdf.set_font("helvetica", '', 10)
    pdf.multi_cell(0, 5, diag_percentil)
    pdf.ln(4)
    
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "4. Evidencia Ecográfica", ln=True)
    y_img = pdf.get_y()
    
    # Manejo de imágenes guardadas en bytes para el PDF
    if img_head is not None:
        try:
            img_h_np = cv2.imdecode(np.frombuffer(img_head, np.uint8), cv2.IMREAD_COLOR)
            pdf.image(Image.fromarray(cv2.cvtColor(img_h_np, cv2.COLOR_BGR2RGB)), x=20, y=y_img, w=75)
        except:
            pass
            
    if img_body is not None:
        try:
            img_b_np = cv2.imdecode(np.frombuffer(img_body, np.uint8), cv2.IMREAD_COLOR)
            pdf.image(Image.fromarray(cv2.cvtColor(img_b_np, cv2.COLOR_BGR2RGB)), x=110, y=y_img, w=75)
        except:
            pass
            
    pdf.set_y(y_img + 60)
    pdf.ln(5)
    
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "5. Conclusión Ecográfica", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.multi_cell(0, 6, conclusiones)
    return bytes(pdf.output())


# =========================================================
# 2. ROL: MÉDICO REVISOR (HISTORIAL LIMPIO, PERCENTILES Y DOBLE FIRMA)
# =========================================================
if st.session_state.rol == "revisor":
    st.title("Panel de Médico Revisor")
    st.markdown("Historial de estudios:")
    
    try:
        conn = conectar_db()
        # Historial limpio original
        df_revisor = pd.read_sql("SELECT id, operador_nombre, operador_ci, paciente_nombre, fecha_estudio, peso_fetal, edad_gestacional, fecha_probable_parto, fecha_revision FROM estudios_ecograficos ORDER BY id DESC", conn)
        
        cur = conn.cursor()
        cur.execute("SELECT id, operador_nombre, paciente_nombre, fecha_estudio, edad_gestacional, peso_fetal, dbp, cc, ca, lf, observaciones, conclusiones, img_head, img_body, pdf_bytes FROM estudios_ecograficos ORDER BY id DESC")
        estudios_completos = cur.fetchall()
        cur.close()
        conn.close()
        
        if not df_revisor.empty:
            tab_tabla, tab_stats, tab_descarga = st.tabs([" Historial de Estudios", "Estadísticas", "Lista de Descargas"])
            
            with tab_tabla:
                st.subheader("Historial General de Estudios Clínicos")
                st.dataframe(df_revisor, use_container_width=True)
                
            with tab_stats:
                st.subheader("Análisis y Comparación con Curvas de Percentiles")
                st.write("Seleccione un estudio registrado para evaluar su posición en las curvas universales de peso fetal:")
                
                opciones_eval = {f"Estudio #{e[0]} - {e[2]} (EG: {e[4]})": e for e in estudios_completos}
                sel_eval_key = st.selectbox("Seleccione estudio para análisis:", list(opciones_eval.keys()))
                
                if sel_eval_key:
                    ev_datos = opciones_eval[sel_eval_key]
                    ev_id, ev_op, ev_pac, ev_fec, ev_eg, ev_pef_str, ev_dbp, ev_cc, ev_ca, ev_lf, _, _, _, _, _ = ev_datos
                    
                    st.divider()
                    # Letra más discreta y estética en los métricos
                    col_e1, col_e2, col_e3 = st.columns(3)
                    col_e1.markdown(f"**Paciente:**\n### {ev_pac}")
                    col_e2.markdown(f"**Peso Estimado (PEF):**\n### {ev_pef_str}")
                    col_e3.markdown(f"**Edad Gestacional:**\n### {ev_eg}")
                    
                    # Extracción numérica de peso
                    peso_g = 1500
                    try:
                        peso_g = float(''.join(filter(str.isdigit, ev_pef_str)))
                    except:
                        pass
                        
                    # Extracción de semanas
                    semanas_num = 32
                    try:
                        semanas_num = int(''.join(filter(str.isdigit, ev_eg.split("semanas")[0])))
                    except:
                        pass
                        
                    # Tabla de referencia Hadlock (P10, P50, P90)
                    tabla_ref = {
                        20: (300, 350, 420),
                        24: (530, 630, 750),
                        28: (1000, 1150, 1350),
                        32: (1550, 1800, 2100),
                        34: (1900, 2250, 2600),
                        36: (2250, 2650, 3100),
                        38: (2550, 3000, 3550),
                        40: (2800, 3350, 3950)
                    }
                    
                    semana_ref = min(tabla_ref.keys(), key=lambda x: abs(x - semanas_num))
                    p10, p50, p90 = tabla_ref[semana_ref]
                    
                    # Generar narrativa clínica detallada
                    if peso_g < p10:
                        diag_texto = f"El peso estimado del feto es de {peso_g} gramos. Según el rango establecido para su edad gestacional (~{semana_ref} semanas), su valor se encuentra por debajo del Percentil 10 ({p10} g). Diagnóstico: PEG (Pequeño para la Edad Gestacional). Se encuentra por debajo del rango de normalidad esperado, por lo que se sugiere control médico estricto."
                        st.error(f"**DIAGNÓSTICO CLÍNICO: PEG**\n\n{diag_texto}")
                    elif peso_g > p90:
                        diag_texto = f"El peso estimado del feto es de {peso_g} gramos. Según el rango establecido para su edad gestacional (~{semana_ref} semanas), su valor se encuentra por encima del Percentil 90 ({p90} g). Diagnóstico: GEG (Grande para la Edad Gestacional). Se encuentra por encima del rango normal superior establecido."
                        st.warning(f"**DIAGNÓSTICO CLÍNICO: GEG**\n\n{diag_texto}")
                    else:
                        diag_texto = f"El peso estimado del feto es de {peso_g} gramos. Según el rango establecido entre el P10 ({p10} g) y el P90 ({p90} g) para su edad gestacional (~{semana_ref} semanas), su peso se encuentra dentro de los parámetros normales. Diagnóstico: AEG (Adecuado para la Edad Gestacional). Por ende, el crecimiento fetal es adecuado."
                        st.success(f"**DIAGNÓSTICO CLÍNICO: AEG**\n\n{diag_texto}")
                        
                    st.divider()
                    st.write("**Gráfica Percentil:**")
                    
                    # Gráfica Altair interactiva con la curva de referencia y el punto del paciente
                    df_curva = pd.DataFrame({
                        'Percentil': ['P10 (Mínimo)', 'P50 (Mediana)', 'P90 (Máximo)'],
                        'Peso_g': [p10, p50, p90]
                    })
                    
                    df_paciente = pd.DataFrame({
                        'Percentil': ['Peso del Feto (IA)'],
                        'Peso_g': [peso_g]
                    })
                    
                    base = alt.Chart(df_curva).encode(
                        x=alt.X('Percentil:N', sort=['P10 (Mínimo)', 'P50 (Mediana)', 'P90 (Máximo)'], title="Rangos de Referencia"),
                        y=alt.Y('Peso_g:Q', title="Peso (g)")
                    )
                    
                    bars = base.mark_bar(opacity=0.6, color='skyblue')
                    points = alt.Chart(df_paciente).mark_point(color='red', size=250, shape='diamond').encode(
                        x=alt.X('Percentil:N'),
                        y=alt.Y('Peso_g:Q', title="Peso (g)")
                    )
                    
                    st.altair_chart(bars + points, use_container_width=True)
                    st.caption("♦.")
                    
                    st.divider()
                    st.subheader("Resumen General de Estudios Registrados")
                    st.metric("Total de Estudios en el Sistema", len(df_revisor))
                    st.write("Distribución de estudios por Edad Gestacional:")
                    if 'edad_gestacional' in df_revisor.columns:
                        conteo_eg = df_revisor['edad_gestacional'].value_counts()
                        st.bar_chart(conteo_eg)

            with tab_descarga:
                st.subheader("Generación de PDF")
                
                opciones_estudios = {f"Estudio #{e[0]} - Paciente: {e[2]} (Ecografista: {e[1]})": e for e in estudios_completos}
                
                if opciones_estudios:
                    sel_key = st.selectbox("Seleccione un estudio clínico para descargar:", list(opciones_estudios.keys()))
                    datos_e = opciones_estudios[sel_key]
                    
                    e_id, e_op, e_pac, e_fec, e_eg, e_pef, e_dbp, e_cc, e_ca, e_lf, e_obs, e_conc, e_img_head, e_img_body, _ = datos_e
                    
                    col_r1, col_r2 = st.columns(2)
                    with col_r1:
                        st.write(f"**Paciente:** {e_pac}")
                        st.write(f"**Ecografista Original:** {e_op}")
                        st.write(f"**Fecha:** {e_fec}")
                    with col_r2:
                        st.write(f"**Peso Fetal:** {e_pef}")
                        st.write(f"**Edad Gestacional:** {e_eg}")
                        
                    nombre_revisor_input = st.text_input("Ingrese su Nombre y Apellido (Médico Revisor):", value=f"Dr. {st.session_state.usuario_actual}")
                    
                    if st.button("Generar PDF", type="primary"):
                        # Recálculo del diagnóstico para el PDF
                        try:
                            p_val = float(''.join(filter(str.isdigit, e_pef)))
                            s_val = int(''.join(filter(str.isdigit, e_eg.split("semanas")[0])))
                        except:
                            p_val, s_val = 1500, 32
                            
                        t_ref = {20: (300, 350, 420), 24: (530, 630, 750), 28: (1000, 1150, 1350), 32: (1550, 1800, 2100), 34: (1900, 2250, 2600), 36: (2250, 2650, 3100), 38: (2550, 3000, 3550), 40: (2800, 3350, 3950)}
                        s_key = min(t_ref.keys(), key=lambda x: abs(x - s_val))
                        p10_r, p50_r, p90_r = t_ref[s_key]
                        
                        if p_val < p10_r:
                            diag_pdf = f"Peso estimado: {p_val}g. Por debajo de P10 ({p10_r}g). Diagnóstico: PEG (Pequeño para la Edad Gestacional). Control médico estricto."
                        elif p_val > p90_r:
                            diag_pdf = f"Peso estimado: {p_val}g. Por encima de P90 ({p90_r}g). Diagnóstico: GEG (Grande para la Edad Gestacional)."
                        else:
                            diag_pdf = f"Peso estimado: {p_val}g. Dentro del rango normal P10 ({p10_r}g) - P90 ({p90_r}g). Diagnóstico: AEG (Adecuado para la Edad Gestacional)."

                        # Generar PDF con imágenes guardadas y textos editados originales
                        pdf_revisado_bytes = generar_pdf(
                            medico_ecografista=e_op,
                            medico_revisor=f"Dr. {nombre_revisor_input}",
                            paci_nombre=e_pac,
                            paci_edad="--",
                            paci_sexo="--",
                            paci_fecha=e_fec,
                            modalidad="US (Ultrasonido)",
                            dbp=float(e_dbp) if e_dbp is not None else 0.0,
                            cc=float(e_cc) if e_cc is not None else 0.0,
                            ca=float(e_ca) if e_ca is not None else 0.0,
                            lf=float(e_lf) if e_lf is not None else 0.0,
                            pef=e_pef,
                            eg=e_eg,
                            obs=e_obs if e_obs else "",
                            conclusiones=e_conc if e_conc else "",
                            diag_percentil=diag_pdf,
                            img_head=e_img_head,
                            img_body=e_img_body
                        )
                        
                        st.download_button(
                            label="Descargar Archivo PDF",
                            data=pdf_revisado_bytes,
                            file_name=f"Reporte_DobleFirma_Estudio_{e_id}.pdf",
                            mime="application/pdf",
                            type="primary"
                        )
                else:
                    st.info("No hay estudios disponibles para revisar.")
        else:
            st.info("No hay estudios registrados en el sistema.")
    except Exception as e:
        st.error(f"Error al cargar el panel de revisión: {e}")
        
    st.stop()


# =========================================================
# 3. ROL: MÉDICO ECOGRAFISTA (CARGA DICOM, IA Y REPORTE)
# =========================================================

if not st.session_state.operador_registrado:
    st.title("Registro de Datos")
    with st.form("form_ecografista_datos"):
        nombres_op = st.text_input("Nombres")
        apellidos_op = st.text_input("Apellidos")
        ci_op = st.text_input("Cédula de Identidad (CI)")
        
        if st.form_submit_button("Continuar al Sistema de Ecografía"):
            if nombres_op and apellidos_op and ci_op:
                st.session_state.operador_nombres = nombres_op
                st.session_state.operador_apellidos = apellidos_op
                st.session_state.operador_ci = ci_op
                st.session_state.operador_registrado = True
                st.rerun()
            else:
                st.warning("Complete todos los campos.")
    st.stop()

st.sidebar.markdown(f"**Dr.(a):** {st.session_state.operador_nombres} {st.session_state.operador_apellidos}")
st.sidebar.header("Carga de Archivos")
archivos_subidos = st.sidebar.file_uploader("Sube imágenes DICOM (.dcm)", type=["dcm"], accept_multiple_files=True)
procesar_btn = st.sidebar.button("Procesar Estudio", type="primary", use_container_width=True)

def guardar_estudio_automatico(op_nom, op_ape, op_ci, pac_nom, fecha_est, dbp, cc, ca, lf, pef, eg, fpp, obs, conc, img_h, img_b, pdf_bytes):
    try:
        conn = conectar_db()
        cur = conn.cursor()
        fecha_revision_exacta = datetime.now().strftime("%d/%m/%Y")
        
        val_dbp = float(dbp) if dbp is not None else None
        val_cc = float(cc) if cc is not None else None
        val_ca = float(ca) if ca is not None else None
        val_lf = float(lf) if lf is not None else None

        # Codificar imágenes procesadas a bytes PNG para persistencia en BD
        img_h_bytes = cv2.imencode('.png', cv2.cvtColor(img_h, cv2.COLOR_RGB2BGR))[1].tobytes() if img_h is not None else None
        img_b_bytes = cv2.imencode('.png', cv2.cvtColor(img_b, cv2.COLOR_RGB2BGR))[1].tobytes() if img_b is not None else None

        sql = """
            INSERT INTO estudios_ecograficos 
            (operador_nombre, operador_ci, paciente_nombre, fecha_estudio, dbp, cc, ca, lf, peso_fetal, edad_gestacional, fecha_probable_parto, observaciones, conclusiones, img_head, img_body, pdf_generado, fecha_revision, pdf_bytes) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        operador_completo = f"Dr. {op_nom} {op_ape}"
        cur.execute(sql, (operador_completo, op_ci, pac_nom, fecha_est, val_dbp, val_cc, val_ca, val_lf, pef, eg, fpp, obs, conc, img_h_bytes, img_b_bytes, "reporte_biometria_fetal.pdf", fecha_revision_exacta, psycopg2.Binary(pdf_bytes)))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error al guardar en BD: {e}")
        return False

st.title("Sistema de Biometría Fetal")

@st.cache_resource
def cargar_modelos():
    return YOLO('best.pt'), YOLO('best_lf_ca.pt')

try:
    model_head, model_body = cargar_modelos()
except Exception as e:
    st.error(f"Error cargando modelos: {e}")

def leer_dicom_bytes(file_bytes):
    ds = pydicom.dcmread(pydicom.filebase.BytesIO(file_bytes))
    imagen = ds.pixel_array
    if imagen.dtype != np.uint8:
        imagen = cv2.normalize(imagen, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    img_bgr = cv2.cvtColor(imagen, cv2.COLOR_GRAY2BGR) if len(imagen.shape) == 2 else cv2.cvtColor(imagen, cv2.COLOR_RGB2BGR)
    mm_per_px = float(ds.PixelSpacing[0]) if "PixelSpacing" in ds else (float(ds.ImagerPixelSpacing[0]) if "ImagerPixelSpacing" in ds else 0.17)
    return img_bgr, mm_per_px, ds

def limpiar_marcas_amarillas(img_bgr):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mascara = cv2.inRange(hsv, np.array([15, 80, 80]), np.array([35, 255, 255]))
    mascara_dilatada = cv2.dilate(mascara, np.ones((3, 3), np.uint8), iterations=1)
    return cv2.inpaint(img_bgr, mascara_dilatada, 3, cv2.INPAINT_TELEA)

def calcular_biometria_fetal(dbp_mm, cc_mm, ca_mm, lf_mm):
    dbp_cm = dbp_mm / 10.0 if dbp_mm else None
    cc_cm = cc_mm / 10.0 if cc_mm else None
    ca_cm = ca_mm / 10.0 if ca_mm else None
    lf_cm = lf_mm / 10.0 if lf_mm else None

    pef_g = None
    if dbp_cm and cc_cm and ca_cm and lf_cm:
        pef_g = 10 ** (1.3596 - (0.00386 * ca_cm * lf_cm) + (0.0064 * cc_cm) + (0.00061 * dbp_cm * ca_cm) + (0.0424 * ca_cm) + (0.174 * lf_cm))
    elif dbp_cm and ca_cm and lf_cm:
        pef_g = 10 ** (1.335 - (0.0034 * ca_cm * lf_cm) + (0.0316 * dbp_cm) + (0.0457 * ca_cm) + (0.1623 * lf_cm))
    elif ca_cm and lf_cm:
        pef_g = 10 ** (1.304 + (0.0528 * ca_cm) + (0.1938 * lf_cm) - (0.004 * ca_cm * lf_cm))

    eg_list = []
    if dbp_cm and 1.5 <= dbp_cm <= 10.0: eg_list.append(9.54 + (1.482 * dbp_cm) + (0.1676 * (dbp_cm ** 2)))
    if cc_cm and 5.0 <= cc_cm <= 36.0: eg_list.append(8.96 + (0.540 * cc_cm) + (0.0003 * (cc_cm ** 3)))
    if ca_cm and 5.0 <= ca_cm <= 38.0: eg_list.append(8.14 + (0.753 * ca_cm) + (0.0036 * (ca_cm ** 2)))
    if lf_cm and 1.0 <= lf_cm <= 8.5: eg_list.append(10.35 + (2.46 * lf_cm) + (0.17 * (lf_cm ** 2)))

    sem, dias = 0, 0
    eg_str = "--"
    if eg_list:
        eg_prom = float(np.mean(eg_list))
        sem, dias = int(eg_prom), int(round((eg_prom - int(eg_prom)) * 7))
        if dias == 7: sem += 1; dias = 0
        eg_str = f"{sem} semanas + {dias} días"

    pef_str = f"{int(round(pef_g))} g" if pef_g else "--"
    return pef_str, eg_str, sem, dias

def extraer_datos_paciente(ds):
    nombre = str(ds.PatientName).replace("^", " ") if 'PatientName' in ds else "Desconocido"
    id_paciente = str(ds.PatientID) if 'PatientID' in ds else "--"
    fecha = str(ds.StudyDate) if 'StudyDate' in ds else "--"
    if len(fecha) == 8: fecha = f"{fecha[6:8]}/{fecha[4:6]}/{fecha[0:4]}"
    edad = str(ds.PatientAge) if 'PatientAge' in ds else "--"
    if edad.endswith('Y'): edad = f"{int(edad[:-1])} años"
    sexo = str(ds.PatientSex) if 'PatientSex' in ds else "--"
    modalidad = str(ds.Modality) if 'Modality' in ds else "US (Ultrasonido)"
    return nombre, id_paciente, fecha, edad, sexo, modalidad

def calcular_fpp(fecha_estudio_str, sem, dias):
    if sem == 0 or fecha_estudio_str == "--":
        return "--"
    try:
        fecha_estudio = datetime.strptime(fecha_estudio_str, "%d/%m/%Y")
        dias_embarazo_actual = (sem * 7) + dias
        dias_restantes = 280 - dias_embarazo_actual
        fecha_fpp = fecha_estudio + timedelta(days=dias_restantes)
        return fecha_fpp.strftime("%d/%m/%Y")
    except:
        return "--"

# PROCESAMIENTO DICOM E IA
if archivos_subidos and procesar_btn:
    candidatos = []
    paci_nombre, paci_id, paci_fecha, paci_edad, paci_sexo, modalidad = "", "", "", "", "", ""
    
    with st.spinner("Procesando DICOM y ejecutando IA..."):
        for i, file in enumerate(archivos_subidos):
            bytes_data = file.read()
            try:
                img_orig, mm_per_px, ds = leer_dicom_bytes(bytes_data)
                if i == 0: 
                    paci_nombre, paci_id, paci_fecha, paci_edad, paci_sexo, modalidad = extraer_datos_paciente(ds)
                img_limpia = limpiar_marcas_amarillas(img_orig)
            except Exception:
                continue

            res_head = model_head(img_limpia, conf=0.10, verbose=False)
            res_body = model_body(img_limpia, conf=0.10, verbose=False)
            s_head = max([float(b.conf[0]) for b in res_head[0].boxes]) if res_head[0].masks is not None and len(res_head[0].boxes) > 0 else 0
            s_body = sum([float(b.conf[0]) for b in res_body[0].boxes]) if res_body[0].masks is not None and len(res_body[0].boxes) > 0 else 0
            candidatos.append({'nombre': file.name, 'bytes': bytes_data, 's_head': s_head, 's_body': s_body})

    if candidatos:
        cand_cabeza = [c for c in candidatos if c['s_head'] > 0.20]
        mejor_cabeza = max(cand_cabeza, key=lambda x: x['s_head']) if cand_cabeza else None
        cand_cuerpo = [c for c in candidatos if c['s_body'] > 0.10]
        if mejor_cabeza and len(cand_cuerpo) > 1:
            cand_cuerpo = [c for c in cand_cuerpo if c['nombre'] != mejor_cabeza['nombre']]
        mejor_cuerpo = max(cand_cuerpo, key=lambda x: x['s_body']) if cand_cuerpo else None

        val_dbp, val_cc, val_ca, val_lf = None, None, None, None
        img_head_res, img_body_res = None, None

        if mejor_cabeza:
            img_orig, mm_per_px, _ = leer_dicom_bytes(mejor_cabeza['bytes'])
            img = limpiar_marcas_amarillas(img_orig)
            res_h = model_head(img, conf=0.25, verbose=False)
            if res_h[0].masks is not None:
                idx_best = np.argmax([float(b.conf[0]) for b in res_h[0].boxes])
                pts = np.int32(res_h[0].masks.xy[idx_best])
                if len(pts) >= 5:
                    ellipse = cv2.fitEllipse(pts)
                    (xc, yc), (d1, d2), angle = ellipse
                    eje_mayor, eje_menor = max(d1, d2), min(d1, d2)
                    val_dbp = eje_menor * mm_per_px
                    a, b = (eje_mayor / 2) * mm_per_px, (eje_menor / 2) * mm_per_px
                    h = ((a - b) ** 2) / ((a + b) ** 2)
                    val_cc = np.pi * (a + b) * (1 + (3 * h) / (10 + np.sqrt(4 - 3 * h)))
                    
                    cv2.ellipse(img, ellipse, (255, 0, 0), 2)
                    angle_rad = math.radians(angle)
                    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
                    x1, y1 = int(xc + (eje_menor / 2) * cos_a), int(yc + (eje_menor / 2) * sin_a)
                    x2, y2 = int(xc - (eje_menor / 2) * cos_a), int(yc - (eje_menor / 2) * sin_a)
                    cv2.line(img, (x1, y1), (x2, y2), (0, 255, 255), 2)
                    cv2.putText(img, f"DBP: {val_dbp:.1f}mm", (int(xc)-40, int(yc)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
                    cv2.putText(img, f"CC: {val_cc:.1f}mm", (int(xc)-40, int(yc)+20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
                    img_head_res = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if mejor_cuerpo:
            img_orig, mm_per_px, _ = leer_dicom_bytes(mejor_cuerpo['bytes'])
            img = limpiar_marcas_amarillas(img_orig)
            res_b = model_body(img, conf=0.10, verbose=False)
            if res_b[0].masks is not None:
                for mask, box in zip(res_b[0].masks.xy, res_b[0].boxes):
                    cls_name = model_body.names[int(box.cls[0])]
                    pts = np.int32(mask)
                    rect = cv2.minAreaRect(pts)
                    es_femur = any(k in cls_name.lower() for k in ['lf', 'femur', 'fl']) or (max(rect[1]) / (min(rect[1]) + 1e-5)) > 2.5
                    if es_femur:
                        pts_flat = pts.reshape(-1, 2)
                        diff = pts_flat[:, np.newaxis, :] - pts_flat[np.newaxis, :, :]
                        idx1, idx2 = np.unravel_index(np.argmax(np.sum(diff ** 2, axis=-1)), (len(pts), len(pts)))
                        p1, p2 = tuple(map(int, pts_flat[idx1])), tuple(map(int, pts_flat[idx2]))
                        val_lf = math.sqrt(((p2[0] - p1[0]) ** 2) + ((p2[1] - p1[1]) ** 2)) * mm_per_px
                        cv2.line(img, p1, p2, (0, 255, 255), 2)
                        cv2.putText(img, f"LF: {val_lf:.1f}mm", (p1[0], max(0, p1[1]-15)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 2)
                    else:
                        if len(pts) >= 5:
                            ellipse = cv2.fitEllipse(pts)
                            (xc, yc), (d1, d2), _ = ellipse
                            eje_mayor, eje_menor = max(d1, d2), min(d1, d2)
                            a, b = (eje_mayor / 2) * mm_per_px, (eje_menor / 2) * mm_per_px
                            h = ((a - b) ** 2) / ((a + b) ** 2)
                            val_ca = np.pi * (a + b) * (1 + (3 * h) / (10 + np.sqrt(4 - 3 * h)))
                            cv2.ellipse(img, ellipse, (0, 255, 0), 2)
                            cv2.putText(img, f"CA: {val_ca:.1f}mm", (int(xc)-40, int(yc)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)
                img_body_res = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        pef_res, eg_res, sem, dias = calcular_biometria_fetal(val_dbp, val_cc, val_ca, val_lf)
        fpp_calculada = calcular_fpp(paci_fecha, sem, dias)
        
        plantilla_conclusiones = (
            f"1) Embarazo de {eg_res} x ECO (+/- 21 días)\n"
            f"2) Presentación: Cefálica \n"
            f"3) Feto único vivo, sexo: Femenino / Masculino / No visible\n"
            f"4) Líquido Amniótico: Normohidramnios\n"
            f"5) FPP (Fecha Probable de Parto): {fpp_calculada}"
        )

        st.session_state.update({
            'estudio_procesado': True, 'paci_nombre': paci_nombre, 'paci_id': paci_id, 
            'paci_fecha': paci_fecha, 'paci_edad': paci_edad, 'paci_sexo': paci_sexo, 'modalidad': modalidad,
            'val_dbp': val_dbp, 'val_cc': val_cc, 'val_ca': val_ca, 'val_lf': val_lf,
            'pef_res': pef_res, 'eg_res': eg_res, 'img_head_res': img_head_res, 'img_body_res': img_body_res,
            'plantilla_conclusiones': plantilla_conclusiones,
            'eg_semanas': sem, 'eg_dias': dias
        })

# INTERFAZ DE RESULTADOS DEL ECOGRAFISTA
if st.session_state.estudio_procesado:
    ss = st.session_state
    
    st.success("Datos clínicos extraídos con éxito.")
    col_p1, col_p2, col_p3 = st.columns(3)
    col_p1.info(f"Paciente: {ss.paci_nombre}\n\nEdad: {ss.paci_edad}")
    col_p2.info(f"ID: {ss.paci_id}\n\nSexo: {ss.paci_sexo}")
    col_p3.info(f"Fecha: {ss.paci_fecha}\n\nModalidad: {ss.modalidad}")

    st.divider()

    st.subheader("Resultados de Biometría")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("DBP", f"{ss.val_dbp:.1f} mm" if ss.val_dbp else "--")
    c2.metric("CC", f"{ss.val_cc:.1f} mm" if ss.val_cc else "--")
    c3.metric("CA", f"{ss.val_ca:.1f} mm" if ss.val_ca else "--")
    c4.metric("LF", f"{ss.val_lf:.1f} mm" if ss.val_lf else "--")

    tab1, tab2 = st.tabs(["Cráneo", "Abdomen y Fémur"])
    with tab1:
        if ss.img_head_res is not None: st.image(ss.img_head_res, use_container_width=True)
    with tab2:
        if ss.img_body_res is not None: st.image(ss.img_body_res, use_container_width=True)

    st.divider()

    col_izq, col_der = st.columns(2)
    
    with col_izq:
        st.subheader("Observaciones Médicas")
        audio_bytes = audio_recorder(text="Grabar audio", icon_size="2x")
        texto_transcrito = ""
        
        if audio_bytes:
            with st.spinner("Transcribiendo..."):
                try:
                    r = sr.Recognizer()
                    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
                        texto_transcrito = r.recognize_google(r.record(source), language="es-ES")
                except Exception:
                    st.error("Error al transcribir.")
        
        observaciones = st.text_area("Texto de Observaciones:", value=texto_transcrito, height=150)

    with col_der:
        st.subheader("Conclusión Ecográfica")
        conclusiones_texto = st.text_area("Conclusiones para el PDF:", value=ss.plantilla_conclusiones, height=150)

    st.divider()
    
    # Cálculo previo del diagnóstico para el PDF del ecografista
    try:
        p_val_ecog = float(''.join(filter(str.isdigit, ss.pef_res)))
        s_val_ecog = int(''.join(filter(str.isdigit, ss.eg_res.split("semanas")[0])))
    except:
        p_val_ecog, s_val_ecog = 1500, 32
        
    t_ref_ecog = {20: (300, 350, 420), 24: (530, 630, 750), 28: (1000, 1150, 1350), 32: (1550, 1800, 2100), 34: (1900, 2250, 2600), 36: (2250, 2650, 3100), 38: (2550, 3000, 3550), 40: (2800, 3350, 3950)}
    s_key_ecog = min(t_ref_ecog.keys(), key=lambda x: abs(x - s_val_ecog))
    p10_e, p50_e, p90_e = t_ref_ecog[s_key_ecog]
    
    if p_val_ecog < p10_e:
        diag_str_ecog = f"Peso estimado: {p_val_ecog}g. Por debajo de P10 ({p10_e}g). Diagnóstico: PEG (Pequeño para la Edad Gestacional). Control médico estricto."
    elif p_val_ecog > p90_e:
        diag_str_ecog = f"Peso estimado: {p_val_ecog}g. Por encima de P90 ({p90_e}g). Diagnóstico: GEG (Grande para la Edad Gestacional)."
    else:
        diag_str_ecog = f"Peso estimado: {p_val_ecog}g. Dentro del rango normal P10 ({p10_e}g) - P90 ({p90_e}g). Diagnóstico: AEG (Adecuado para la Edad Gestacional)."

    nombre_completo_ecografista = f"Dr. {st.session_state.operador_nombres} {st.session_state.operador_apellidos} (CI: {st.session_state.operador_ci})"
    
    pdf_bytes = generar_pdf(
        medico_ecografista=nombre_completo_ecografista,
        medico_revisor="",
        paci_nombre=ss.paci_nombre,
        paci_edad=ss.paci_edad,
        paci_sexo=ss.paci_sexo,
        paci_fecha=ss.paci_fecha,
        modalidad=ss.modalidad,
        dbp=ss.val_dbp,
        cc=ss.val_cc,
        ca=ss.val_ca,
        lf=ss.val_lf,
        pef=ss.pef_res,
        eg=ss.eg_res,
        obs=observaciones,
        conclusiones=conclusiones_texto,
        diag_percentil=diag_str_ecog,
        img_head=ss.img_head_res,
        img_body=ss.img_body_res
    )
    
    st.download_button(
        label="Descargar Reporte Clínico en PDF",
        data=pdf_bytes,
        file_name=f"Reporte_Biometria_{ss.paci_nombre.replace(' ','_')}.pdf",
        mime="application/pdf",
        type="primary",
        use_container_width=True
    )
    
    # GUARDADO AUTOMÁTICO CON IMÁGENES Y PARÁMETROS EN LA BD
    clave_estudio_actual = f"{ss.get('paci_nombre', '')}_{ss.get('paci_fecha', '')}"
    if ss.get('ultimo_estudio_guardado') != clave_estudio_actual:
        guardado_ok = guardar_estudio_automatico(
            op_nom=st.session_state.get('operador_nombres', ''),
            op_ape=st.session_state.get('operador_apellidos', ''),
            op_ci=st.session_state.get('operador_ci', ''),
            pac_nom=ss.get('paci_nombre', 'Desconocido'),
            fecha_est=str(ss.get('paci_fecha', '')),
            dbp=ss.get('val_dbp', 0),
            cc=ss.get('val_cc', 0),
            ca=ss.get('val_ca', 0),
            lf=ss.get('val_lf', 0),
            pef=str(ss.get('pef_res', '')),
            eg=str(ss.get('eg_res', '')),
            fpp=str(calcular_fpp(ss.get('paci_fecha', ''), ss.get('eg_semanas', 0), ss.get('eg_dias', 0))),
            obs=observaciones,
            conc=conclusiones_texto,
            img_h=ss.img_head_res,
            img_b=ss.img_body_res,
            pdf_bytes=pdf_bytes
        )
        if guardado_ok:
            ss.ultimo_estudio_guardado = clave_estudio_actual
            st.success("Estudio guardado correctamente en la base de datos.")
