import os
import math
import cv2
import numpy as np
import pydicom
from ultralytics import YOLO
import streamlit as st
from PIL import Image

# 1. CONFIGURACIÓN DE PÁGINA Y ESTILOS
st.set_page_config(
    page_title="Sistema de Biometría Fetal",
    page_icon="",
    layout="wide"
)

st.title(" Sistema de Biometría Fetal - Análisis DICOM")
st.markdown("Carga de archivos DICOM, segmentación automática con YOLO y estimación biométrica (Hadlock).")

# 2. CARGA DE MODELOS (CON CACHÉ PARA RAPIDEZ)
@st.cache_resource
def cargar_modelos():
    m_head = YOLO('best.pt')
    m_body = YOLO('best_lf_ca.pt')
    return m_head, m_body

try:
    model_head, model_body = cargar_modelos()
    st.sidebar.success("Modelos YOLO cargados correctamente")
except Exception as e:
    st.sidebar.error(f"Error cargando modelos: {e}")

# 3. FUNCIONES DE PROCESAMIENTO Y MATEMÁTICAS
def leer_dicom_bytes(file_bytes):
    ds = pydicom.dcmread(pydicom.filebase.BytesIO(file_bytes))
    imagen = ds.pixel_array
    
    if imagen.dtype != np.uint8:
        imagen = cv2.normalize(imagen, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        
    img_bgr = cv2.cvtColor(imagen, cv2.COLOR_GRAY2BGR) if len(imagen.shape) == 2 else cv2.cvtColor(imagen, cv2.COLOR_RGB2BGR)

    if "PixelSpacing" in ds:
        mm_per_px = float(ds.PixelSpacing[0])
    elif "ImagerPixelSpacing" in ds:
        mm_per_px = float(ds.ImagerPixelSpacing[0])
    else:
        mm_per_px = 0.17
        
    return img_bgr, mm_per_px

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

    # PEF - Hadlock
    pef_g = None
    if dbp_cm and cc_cm and ca_cm and lf_cm:
        log_efw = 1.3596 - (0.00386 * ca_cm * lf_cm) + (0.0064 * cc_cm) + (0.00061 * dbp_cm * ca_cm) + (0.0424 * ca_cm) + (0.174 * lf_cm)
        pef_g = 10 ** log_efw
    elif dbp_cm and ca_cm and lf_cm:
        log_efw = 1.335 - (0.0034 * ca_cm * lf_cm) + (0.0316 * dbp_cm) + (0.0457 * ca_cm) + (0.1623 * lf_cm)
        pef_g = 10 ** log_efw
    elif ca_cm and lf_cm:
        log_efw = 1.304 + (0.0528 * ca_cm) + (0.1938 * lf_cm) - (0.004 * ca_cm * lf_cm)
        pef_g = 10 ** log_efw

    # EG Promedio
    eg_list = []
    if dbp_cm and 1.5 <= dbp_cm <= 10.0:
        eg_list.append(9.54 + (1.482 * dbp_cm) + (0.1676 * (dbp_cm ** 2)))
    if cc_cm and 5.0 <= cc_cm <= 36.0:
        eg_list.append(8.96 + (0.540 * cc_cm) + (0.0003 * (cc_cm ** 3)))
    if ca_cm and 5.0 <= ca_cm <= 38.0:
        eg_list.append(8.14 + (0.753 * ca_cm) + (0.0036 * (ca_cm ** 2)))
    if lf_cm and 1.0 <= lf_cm <= 8.5:
        eg_list.append(10.35 + (2.46 * lf_cm) + (0.17 * (lf_cm ** 2)))

    eg_str = "--"
    if eg_list:
        eg_prom = float(np.mean(eg_list))
        sem = int(eg_prom)
        dias = int(round((eg_prom - sem) * 7))
        if dias == 7: sem += 1; dias = 0
        eg_str = f"{sem} sem + {dias} d"

    pef_str = f"{int(round(pef_g))} g" if pef_g else "--"
    return pef_str, eg_str

# 4. PANEL LATERAL (CARGA DE ARCHIVOS)
st.sidebar.header(" Carga de Archivos")
archivos_subidos = st.sidebar.file_uploader(
    "Selecciona uno o varios archivos DICOM (.dcm)",
    type=["dcm"],
    accept_multiple_files=True
)

if archivos_subidos:
    if st.sidebar.button("Analizar Biometría", type="primary"):
        candidatos = []
        
        # 1. ESCANEO Y SELECCIÓN AUTOMÁTICA
        with st.spinner("Procesando y escaneando imágenes DICOM..."):
            for file in archivos_subidos:
                bytes_data = file.read()
                try:
                    img_orig, _ = leer_dicom_bytes(bytes_data)
                    img_limpia = limpiar_marcas_amarillas(img_orig)
                except Exception:
                    continue

                res_head = model_head(img_limpia, conf=0.10, verbose=False)
                res_body = model_body(img_limpia, conf=0.10, verbose=False)

                s_head = max([float(b.conf[0]) for b in res_head[0].boxes]) if res_head[0].masks is not None and len(res_head[0].boxes) > 0 else 0
                s_body = sum([float(b.conf[0]) for b in res_body[0].boxes]) if res_body[0].masks is not None and len(res_body[0].boxes) > 0 else 0

                candidatos.append({'nombre': file.name, 'bytes': bytes_data, 's_head': s_head, 's_body': s_body})

        if not candidatos:
            st.error("No se pudieron procesar los archivos subidos.")
        else:
            # Selección de los mejores candidatos
            cand_cabeza = [c for c in candidatos if c['s_head'] > 0.20]
            mejor_cabeza = max(cand_cabeza, key=lambda x: x['s_head']) if cand_cabeza else None

            cand_cuerpo = [c for c in candidatos if c['s_body'] > 0.10]
            if mejor_cabeza and len(cand_cuerpo) > 1:
                cand_cuerpo = [c for c in cand_cuerpo if c['nombre'] != mejor_cabeza['nombre']]
            mejor_cuerpo = max(cand_cuerpo, key=lambda x: x['s_body']) if cand_cuerpo else None

            val_dbp, val_cc, val_ca, val_lf = None, None, None, None
            img_head_res, img_body_res = None, None

            # 2. PROCESAR CABEZA
            # 2. PROCESAR CABEZA
            if mejor_cabeza:
                img_orig, mm_per_px = leer_dicom_bytes(mejor_cabeza['bytes'])
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
                        
                        # Dibujar elipse de la Cabeza (CC) - Azul
                        cv2.ellipse(img, ellipse, (255, 0, 0), 2)
                        
                        # Calcular y dibujar la línea del DBP - Amarilla
                        angle_rad = math.radians(angle)
                        cos_a = math.cos(angle_rad)
                        sin_a = math.sin(angle_rad)
                        
                        x1 = int(xc + (eje_menor / 2) * cos_a)
                        y1 = int(yc + (eje_menor / 2) * sin_a)
                        x2 = int(xc - (eje_menor / 2) * cos_a)
                        y2 = int(yc - (eje_menor / 2) * sin_a)
                        
                        cv2.line(img, (x1, y1), (x2, y2), (0, 255, 255), 2)
                        
                        cv2.putText(img, f"DBP: {val_dbp:.1f}mm", (int(xc)-40, int(yc)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
                        cv2.putText(img, f"CC: {val_cc:.1f}mm", (int(xc)-40, int(yc)+20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
                        img_head_res = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            # 3. PROCESAR CUERPO (CA / LF PITÁGORAS)
            if mejor_cuerpo:
                img_orig, mm_per_px = leer_dicom_bytes(mejor_cuerpo['bytes'])
                img = limpiar_marcas_amarillas(img_orig)
                res_b = model_body(img, conf=0.10, verbose=False)
                
                if res_b[0].masks is not None:
                    for mask, box in zip(res_b[0].masks.xy, res_b[0].boxes):
                        cls_id = int(box.cls[0])
                        cls_name = model_body.names[cls_id]
                        pts = np.int32(mask)
                        
                        rect = cv2.minAreaRect(pts)
                        aspect_ratio = max(rect[1]) / (min(rect[1]) + 1e-5)
                        es_femur = any(k in cls_name.lower() for k in ['lf', 'femur', 'fl']) or aspect_ratio > 2.5

                        if es_femur:
                            pts_flat = pts.reshape(-1, 2)
                            diff = pts_flat[:, np.newaxis, :] - pts_flat[np.newaxis, :, :]
                            dist_sq = np.sum(diff ** 2, axis=-1)
                            idx1, idx2 = np.unravel_index(np.argmax(dist_sq), dist_sq.shape)
                            p1, p2 = tuple(map(int, pts_flat[idx1])), tuple(map(int, pts_flat[idx2]))
                            
                            cateto_x = p2[0] - p1[0]
                            cateto_y = p2[1] - p1[1]
                            hipotenusa_px = math.sqrt((cateto_x ** 2) + (cateto_y ** 2))
                            val_lf = hipotenusa_px * mm_per_px
                            
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

            # 4. MOSTRAR RESULTADOS EN PANTALLA WEB
            pef_res, eg_res = calcular_biometria_fetal(val_dbp, val_cc, val_ca, val_lf)

            # Métricas superiores
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("DBP", f"{val_dbp:.1f} mm" if val_dbp else "--")
            c2.metric("CC", f"{val_cc:.1f} mm" if val_cc else "--")
            c3.metric("CA", f"{val_ca:.1f} mm" if val_ca else "--")
            c4.metric("LF", f"{val_lf:.1f} mm" if val_lf else "--")

            st.divider()

            # Resumen de Biometría
            b1, b2 = st.columns(2)
            b1.success(f"**Peso Fetal Estimado (PEF):** {pef_res}")
            b2.info(f"**Edad Gestacional (EG):** {eg_res}")

            # Visualización de Imágenes en Pestañas
            tab1, tab2 = st.tabs(["Cabeza (DBP/CC)", "Cuerpo (CA/LF)"])
            with tab1:
                if img_head_res is not None:
                    st.image(img_head_res, caption=f"Candidato: {mejor_cabeza['nombre']}", use_container_width=True)
                else:
                    st.warning("No se detectó estructura de Cabeza.")

            with tab2:
                if img_body_res is not None:
                    st.image(img_body_res, caption=f"Candidato: {mejor_cuerpo['nombre']}", use_container_width=True)
                else:
                    st.warning("No se detectó estructura de Cuerpo.")