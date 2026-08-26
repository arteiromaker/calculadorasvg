from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import re
import os
from typing import Dict, Optional
from svgpathtools import svg2paths

try:
    import ezdxf
    from ezdxf.bbox import extents
    from ezdxf.path import make_path
except ImportError:
    ezdxf = None

app = FastAPI(title="Calculador Laser Data-Driven + AppSheet")

# 1. O NOVO MODELO (Recebe tudo dinamicamente do AppSheet)
class LaserParams(BaseModel):
    file_url: str
    row_id: str
    app_id: str
    access_key: str
    table_name: str = "Formação Preço"

    # Parâmetros de Corte
    cor_corte_dxf: int = 7          # Ex: 7 (Preto Padrão do AutoLaser)
    cor_corte_svg: str = "#000000"
    vel_corte: float = 20.0
    fator_corte: float = 1.35

    # Parâmetros de Gravação
    cor_gravacao_dxf: int = 1       # Ex: 1 (Vermelho Padrão do AutoLaser)
    cor_gravacao_svg: str = "#FF0000"
    vel_gravacao: float = 300.0
    fator_gravacao: float = 1.10
    overscan_mm: float = 50.0
    scan_gap_mm: float = 0.05       # Intervalo da varredura!

def normalize_color(color_str: Optional[str]) -> Optional[str]:
    if not color_str or color_str.lower() in ['none', 'transparent']: return None
    color_str = color_str.strip().upper()
    if color_str.startswith('#'):
        if len(color_str) == 4: return f"#{color_str[1]*2}{color_str[2]*2}{color_str[3]*2}"
        return color_str
    return color_str

def get_element_color(attr: dict) -> str:
    stroke = attr.get('stroke')
    if stroke and stroke.lower() != 'none':
        norm = normalize_color(stroke)
        if norm: return norm
    fill = attr.get('fill')
    if fill and fill.lower() != 'none':
        norm = normalize_color(fill)
        if norm: return norm
    return "#000000"

# ==========================================
# PROCESSAMENTO DE SVG
# ==========================================
def process_svg(file_path: str, p: LaserParams):
    SCALE_PX_TO_MM = 25.4 / 72.0  
    
    global_xmin, global_xmax = float('inf'), float('-inf')
    global_ymin, global_ymax = float('inf'), float('-inf')
    red_xmin, red_xmax = float('inf'), float('-inf')
    red_ymin, red_ymax = float('inf'), float('-inf')
    cut_perimeter_px = 0.0

    paths, attributes = svg2paths(file_path)
    for path, attr in zip(paths, attributes):
        if not path or path.length() == 0: continue
        cor = get_element_color(attr).upper()
        try: xmin, xmax, ymin, ymax = path.bbox()
        except: continue 
        
        global_xmin, global_xmax = min(global_xmin, xmin), max(global_xmax, xmax)
        global_ymin, global_ymax = min(global_ymin, ymin), max(global_ymax, ymax)
        
        # AQUI É A MÁGICA: Ele checa se a cor é exatamente a que o AppSheet mandou!
        if cor == p.cor_gravacao_svg.upper(): 
            red_xmin, red_xmax = min(red_xmin, xmin), max(red_xmax, xmax)
            red_ymin, red_ymax = min(red_ymin, ymin), max(red_ymax, ymax)
        else: 
            cut_perimeter_px += path.length()

    width_cm = ((global_xmax - global_xmin) * SCALE_PX_TO_MM) / 10.0 if global_xmin != float('inf') else 0.0
    height_cm = ((global_ymax - global_ymin) * SCALE_PX_TO_MM) / 10.0 if global_ymin != float('inf') else 0.0

    cut_perimeter_mm = cut_perimeter_px * SCALE_PX_TO_MM
    vel_corte = p.vel_corte if p.vel_corte > 0 else 20.0
    tempo_corte_seg = (cut_perimeter_mm / vel_corte) * p.fator_corte 

    tempo_gravacao_seg = 0.0
    if red_xmin != float('inf'):
        red_width_mm = (red_xmax - red_xmin) * SCALE_PX_TO_MM
        red_height_mm = (red_ymax - red_ymin) * SCALE_PX_TO_MM
        vel_gravacao = p.vel_gravacao if p.vel_gravacao > 0 else 300.0
        
        largura_real_varredura = red_width_mm + p.overscan_mm
        distancia_gravacao_mm = (red_height_mm / p.scan_gap_mm) * largura_real_varredura
        tempo_gravacao_seg = (distancia_gravacao_mm / vel_gravacao) * p.fator_gravacao 

    return width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg

# ==========================================
# PROCESSAMENTO DE DXF 
# ==========================================
def process_dxf(file_path: str, p: LaserParams):
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    red_entities = []
    black_entities = []
    
    for entity in msp:
        color = entity.dxf.color
        if color == 256: 
            try: color = doc.layers.get(entity.dxf.layer).color
            except: color = 7 
                
        # MÁGICA NO DXF: Compara a cor da linha com a cor que o AppSheet disse que é Gravação
        if color == p.cor_gravacao_dxf:
            red_entities.append(entity)
        else:
            black_entities.append(entity)

    global_bbox = extents(msp)
    width_cm, height_cm = 0.0, 0.0
    if global_bbox.has_data:
        width_cm = (global_bbox.extmax.x - global_bbox.extmin.x) / 10.0
        height_cm = (global_bbox.extmax.y - global_bbox.extmin.y) / 10.0

    tempo_gravacao_seg = 0.0
    if red_entities:
        red_bbox = extents(red_entities)
        if red_bbox.has_data:
            red_width_mm = red_bbox.extmax.x - red_bbox.extmin.x
            red_height_mm = red_bbox.extmax.y - red_bbox.extmin.y
            vel_gravacao = p.vel_gravacao if p.vel_gravacao > 0 else 300.0
            
            largura_real_varredura = red_width_mm + p.overscan_mm
            distancia_gravacao_mm = (red_height_mm / p.scan_gap_mm) * largura_real_varredura
            tempo_gravacao_seg = (distancia_gravacao_mm / vel_gravacao) * p.fator_gravacao

    cut_perimeter_mm = 0.0
    for entity in black_entities:
        try:
            path_obj = make_path(entity)
            vertices = list(path_obj.flattening(distance=0.1))
            for i in range(1, len(vertices)):
                cut_perimeter_mm += vertices[i-1].distance(vertices[i])
        except:
            continue
            
    vel_corte = p.vel_corte if p.vel_corte > 0 else 20.0
    tempo_corte_seg = (cut_perimeter_mm / vel_corte) * p.fator_corte

    return width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg

# ==========================================
# FUNÇÕES DE INTEGRAÇÃO APPSHEET
# ==========================================
def format_appsheet_time(minutos_float):
    h = int(minutos_float // 60)
    m = int(minutos_float % 60)
    s = int(round((minutos_float * 60) % 60))
    return f"{h:02d}:{m:02d}:{s:02d}"

def update_appsheet_row(app_id: str, access_key: str, table_name: str, row_id: str, resultados: dict):
    url = f"https://api.appsheet.com/api/v2/apps/{app_id}/tables/{table_name}/Action"
    headers = {"ApplicationAccessKey": access_key, "Content-Type": "application/json"}
    tempo_formatado = format_appsheet_time(resultados["tempo_total_min"])
    
    payload = {
        "Action": "Edit",
        "Properties": {"Locale": "pt-BR", "Timezone": "E. South America Standard Time"},
        "Rows": [{
            "ID": str(row_id),
            "Altura": str(resultados["altura_cm"]).replace('.', ','),
            "Largura": str(resultados["largura_cm"]).replace('.', ','),
            "TempoServ": tempo_formatado
        }]
    }
    requests.post(url, json=payload, headers=headers)

@app.get("/")
def health_check(): return {"status": "online"}

@app.post("/calcular-corte")
def calcular_corte(p: LaserParams):
    temp_path = None
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(p.file_url, headers=headers, timeout=15)
        if response.status_code != 200: raise Exception("Erro ao baixar o arquivo.")
            
        is_dxf = p.file_url.lower().endswith('.dxf')
        temp_path = "/tmp/temp_file.dxf" if is_dxf else "/tmp/temp_file.svg"
        
        with open(temp_path, "wb") as f: f.write(response.content)

        if is_dxf:
            width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg = process_dxf(temp_path, p)
        else:
            width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg = process_svg(temp_path, p)

        resultados = {
            "largura_cm": round(width_cm, 2),
            "altura_cm": round(height_cm, 2),
            "tempo_total_min": round((tempo_corte_seg + tempo_gravacao_seg) / 60.0, 2)
        }

        update_appsheet_row(p.app_id, p.access_key, p.table_name, p.row_id, resultados)
        return {"status": "success", "data": resultados}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)
