import os
import math
import cv2
import numpy as np
import pydicom
from ultralytics import YOLO
import streamlit as st
from PIL import Image
import io
import speech_recognition as sr
from audio_recorder_streamlit import audio_recorder
from fpdf import FPDF
from datetime import datetime, timedelta

# ====================================================
# 1. CONFIGURACIÓN Y ESTILOS
# ====================================================
st.set_page_config(page_title="Sistema de Biometría Fetal", layout="wide")

hide_menu_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stApp [data-testid="stToolbar"] {display: none;}
    </style>
"""
st.markdown(hide_menu_style, unsafe_allow_html=True)

st.title(" Sistema de Biometría Fetal - Análisis Clínico")

# ====================================================
# 2. CARGA DE MODELOS YOLO
# ====================================================
@st.cache_resource
def cargar_modelos():
    return YOLO('best.pt'), YOLO('best_lf_ca.pt')

try:
    model_head, model_body = cargar_modelos()
except Exception as e:
    st.error(f"Error cargando modelos: {e}")

# ====================================================
# 3. FUNCIONES DEL PROYECTO
# ====================================================
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
    # Limpiar formato de edad DICOM (ej: '028Y' -> '28 años')
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

def generar_pdf(operador, medico_solicitante, paci_nombre, paci_edad, paci_sexo, paci_fecha, modalidad, 
                dbp, cc, ca, lf, pef, eg, obs, conclusiones, img_head, img_body):
    pdf = FPDF()
    pdf.add_page()
    
    # TITULO
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, "REPORTE CLINICO DE BIOMETRIA FETAL", ln=True, align='C')
    pdf.ln(5)
    
    # 1. DATOS DEL ESTUDIO
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "1. Datos del Estudio", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.cell(0, 6, f"Medico Operador: {operador}", ln=True)
    pdf.cell(0, 6, f"Medico Solicitante: {medico_solicitante}", ln=True)
    pdf.cell(0, 6, f"Paciente: {paci_nombre}", ln=True)
    pdf.cell(0, 6, f"Edad: {paci_edad}  |  Sexo: {paci_sexo}  |  Modalidad: {modalidad}", ln=True)
    pdf.cell(0, 6, f"Fecha de Estudio: {paci_fecha}", ln=True)
    pdf.ln(5)
    
    # 2. OBSERVACIONES MEDICAS (Antiguo 4)
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "2. Observaciones Medicas", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.multi_cell(0, 6, obs if obs.strip() else "Sin observaciones adicionales dictadas.")
    pdf.ln(5)

    # 3. RESULTADOS BIOMETRICOS (Antiguo 2)
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "3. Resultados Biometricos", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.cell(90, 6, f"DBP: {dbp:.1f} mm" if dbp else "DBP: --", ln=False)
    pdf.cell(90, 6, f"CC: {cc:.1f} mm" if cc else "CC: --", ln=True)
    pdf.cell(90, 6, f"CA: {ca:.1f} mm" if ca else "CA: --", ln=False)
    pdf.cell(90, 6, f"LF: {lf:.1f} mm" if lf else "LF: --", ln=True)
    pdf.ln(2)
    pdf.set_font("helvetica", 'B', 11)
    pdf.cell(0, 6, f"Peso Estimado (PEF): {pef}  |  Edad Gestacional (EG): {eg}", ln=True)
    pdf.ln(5)

    # 4. EVIDENCIAS ECOGRAFICAS (Antiguo 3)
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "4. Evidencia Ecografica", ln=True)
    y_img = pdf.get_y()
    if img_head is not None:
        img_pil_h = Image.fromarray(img_head)
        pdf.image(img_pil_h, x=20, y=y_img, w=75)
    if img_body is not None:
        img_pil_b = Image.fromarray(img_body)
        pdf.image(img_pil_b, x=110, y=y_img, w=75)
    
    pdf.set_y(y_img + 60) # Bajar el cursor
    pdf.ln(5)

    # 5. CONCLUSION ECOGRAFICA (NUEVO)
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(0, 8, "5. Conclusion Ecografica", ln=True)
    pdf.set_font("helvetica", '', 11)
    pdf.multi_cell(0, 6, conclusiones)
    
    return bytes(pdf.output())

# ====================================================
# ETAPA 1: PANEL LATERAL
# ====================================================
st.sidebar.header(" Datos del Personal")
nombre_medico = st.sidebar.text_input("Médico Operador:", "Dr. ")
medico_solicitante = st.sidebar.text_input("Médico Solicitante:", "Dr. ")

st.sidebar.divider()
st.sidebar.header(" Carga de Archivos")
archivos_subidos = st.sidebar.file_uploader("Sube imágenes DICOM (.dcm)", type=["dcm"], accept_multiple_files=True)
procesar_btn = st.sidebar.button(" Procesar Estudio Biométrico", type="primary", use_container_width=True)

if 'estudio_procesado' not in st.session_state:
    st.session_state.estudio_procesado = False

# ====================================================
# PROCESAMIENTO
# ====================================================
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
        
        # Plantilla automática para Conclusiones
        plantilla_conclusiones = (
            f"1) Embarazo de {eg_res} x ECO (+/- 21 días)\n"
            f"2) Presentación: Cefálica / Pélvica [Borrar la incorrecta]\n"
            f"3) Feto único vivo, sexo: Femenino / Masculino / No visible\n"
            f"4) Líquido Amniótico: Normohidramnios\n"
            f"5) FPP (Fecha Probable de Parto): {fpp_calculada}"
        )

        st.session_state.update({
            'estudio_procesado': True, 'paci_nombre': paci_nombre, 'paci_id': paci_id, 
            'paci_fecha': paci_fecha, 'paci_edad': paci_edad, 'paci_sexo': paci_sexo, 'modalidad': modalidad,
            'val_dbp': val_dbp, 'val_cc': val_cc, 'val_ca': val_ca, 'val_lf': val_lf,
            'pef_res': pef_res, 'eg_res': eg_res, 'img_head_res': img_head_res, 'img_body_res': img_body_res,
            'plantilla_conclusiones': plantilla_conclusiones
        })

# ====================================================
# INTERFAZ DE RESULTADOS
# ====================================================
if st.session_state.estudio_procesado:
    ss = st.session_state
    
    st.success(" Datos clínicos y biométricos extraídos con éxito.")
    col_p1, col_p2, col_p3 = st.columns(3)
    col_p1.info(f"** Paciente:** {ss.paci_nombre}\n\n**Edad:** {ss.paci_edad}")
    col_p2.info(f"** ID:** {ss.paci_id}\n\n**Sexo:** {ss.paci_sexo}")
    col_p3.info(f"** Fecha:** {ss.paci_fecha}\n\n**Mod:** {ss.modalidad}")

    st.divider()

    st.subheader(" Resultados de Biometría")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("DBP", f"{ss.val_dbp:.1f} mm" if ss.val_dbp else "--")
    c2.metric("CC", f"{ss.val_cc:.1f} mm" if ss.val_cc else "--")
    c3.metric("CA", f"{ss.val_ca:.1f} mm" if ss.val_ca else "--")
    c4.metric("LF", f"{ss.val_lf:.1f} mm" if ss.val_lf else "--")

    tab1, tab2 = st.tabs(["🧠 Cráneo", "🦴 Abdomen y Fémur"])
    with tab1:
        if ss.img_head_res is not None: st.image(ss.img_head_res, use_container_width=True)
    with tab2:
        if ss.img_body_res is not None: st.image(ss.img_body_res, use_container_width=True)

    st.divider()

    col_izq, col_der = st.columns(2)
    
    with col_izq:
        st.subheader(" Observaciones Médicas")
        st.write("Haz clic en el micrófono para iniciar, y clic nuevamente para detener el dictado.")
        audio_bytes = audio_recorder(text="Grabar audio", icon_size="2x")
        texto_transcrito = ""
        
        if audio_bytes:
            with st.spinner("Transcribiendo audio..."):
                try:
                    r = sr.Recognizer()
                    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
                        audio_data = r.record(source)
                        texto_transcrito = r.recognize_google(audio_data, language="es-ES")
                except Exception:
                    st.error("Error al transcribir. Asegúrate de hablar claro.")
        
        observaciones = st.text_area("Texto de Observaciones:", value=texto_transcrito, height=150)

    with col_der:
        st.subheader(" Conclusión Ecográfica")
        st.write("Rellena o modifica los hallazgos clínicos:")
        conclusiones_texto = st.text_area("Conclusiones para el PDF:", value=ss.plantilla_conclusiones, height=150)

    st.divider()
    st.subheader(" Generación de Reporte")
    
    pdf_bytes = generar_pdf(
        nombre_medico, medico_solicitante, ss.paci_nombre, ss.paci_edad, ss.paci_sexo, ss.paci_fecha, ss.modalidad,
        ss.val_dbp, ss.val_cc, ss.val_ca, ss.val_lf, 
        ss.pef_res, ss.eg_res, observaciones, conclusiones_texto, ss.img_head_res, ss.img_body_res
    )
    
    st.download_button(
        label="📥 Descargar Reporte Clínico en PDF",
        data=pdf_bytes,
        file_name=f"Reporte_Biometria_{ss.paci_nombre.replace(' ','_')}.pdf",
        mime="application/pdf",
        type="primary",
        use_container_width=True
    )
