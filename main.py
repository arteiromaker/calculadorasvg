from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import re
import os
import math
from typing import Dict, Optional
from svgpathtools import svg2paths

# Importa a biblioteca de DXF
try:
    import ezdxf
    from ezdxf.bbox import extents
    from ezdxf.path import make_path
except ImportError:
    ezdxf = None

app = FastAPI(title="Calculador Híbrido SVG/DXF para Laser + AppSheet")

class ColorSpeed(BaseModel):
    file_url: str
    row_id: str
    app_id: str
    access_key: str
    table_name: str = "Formação Preço"
    velocidades_por_cor: Dict[str, float]
    velocidade_padrao_mms: float = 20.0
    overscan_mm: float = 50.0
    fator_curvas_corte: float = 1.35
    fator_pulos_gravacao: float = 1.10

def normalize_color(color_str: Optional[str]) -> Optional[str]:
    if not color_str or color_str.lower() in ['none', 'transparent']: return None
    color_str = color_str.strip().upper()
    color_map = {'BLACK': '#000000', 'RED': '#FF0000'}
    if color_str in color_map: return color_map[color_str]
    if color_str.startswith('#'):
        if len(color_str) == 4: return f"#{color_str[1]*2}{color_str[2]*2}{color_str[3]*2}"
        return color_str
    return color_str

def get_element_color(attr: dict) -> str:
    stroke = attr.get('stroke')
    if stroke and stroke.lower() != 'none':
        norm = normalize_color(stroke)
        if norm: return norm
    style = attr.get('style', '')
    if style:
        stroke_match = re.search(r'stroke\s*:\s*([^;]+)', style)
        if stroke_match:
            norm = normalize_color(stroke_match.group(1))
            if norm: return norm
    fill = attr.get('fill')
    if fill and fill.lower() != 'none':
        norm = normalize_color(fill)
        if norm: return norm
    return "#000000"

# ==========================================
# PROCESSAMENTO DE SVG
# ==========================================
def process_svg(file_path: str, vel_map: dict, vel_padrao: float, overscan: float, fator_corte: float, fator_gravacao: float):
    SCALE_PX_TO_MM = 25.4 / 72.0  
    SCAN_GAP_MM = 0.05            

    global_xmin, global_xmax = float('inf'), float('-inf')
    global_ymin, global_ymax = float('inf'), float('-inf')
    red_xmin, red_xmax = float('inf'), float('-inf')
    red_ymin, red_ymax = float('inf'), float('-inf')
    cut_perimeter_px = 0.0

    paths, attributes = svg2paths(file_path)
    for path, attr in zip(paths, attributes):
        if not path or path.length() == 0: continue
        cor = get_element_color(attr).upper()
        try:
            xmin, xmax, ymin, ymax = path.bbox()
        except Exception:
            continue 
        
        global_xmin, global_xmax = min(global_xmin, xmin), max(global_xmax, xmax)
        global_ymin, global_ymax = min(global_ymin, ymin), max(global_ymax, ymax)
        
        if cor == '#000000': 
            cut_perimeter_px += path.length()
        elif cor == '#FF0000': 
            red_xmin, red_xmax = min(red_xmin, xmin), max(red_xmax, xmax)
            red_ymin, red_ymax = min(red_ymin, ymin), max(red_ymax, ymax)

    width_cm = ((global_xmax - global_xmin) * SCALE_PX_TO_MM) / 10.0 if global_xmin != float('inf') else 0.0
    height_cm = ((global_ymax - global_ymin) * SCALE_PX_TO_MM) / 10.0 if global_ymin != float('inf') else 0.0

    cut_perimeter_mm = cut_perimeter_px * SCALE_PX_TO_MM
    vel_corte = vel_map.get('#000000', vel_padrao) if vel_map.get('#000000', vel_padrao) > 0 else vel_padrao
    tempo_corte_seg = (cut_perimeter_mm / vel_corte) * fator_corte 

    tempo_gravacao_seg = 0.0
    if red_xmin != float('inf'):
        red_width_mm = (red_xmax - red_xmin) * SCALE_PX_TO_MM
        red_height_mm = (red_ymax - red_ymin) * SCALE_PX_TO_MM
        vel_gravacao = vel_map.get('#FF0000', 300.0) if vel_map.get('#FF0000', 300.0) > 0 else 300.0
        
        largura_real_varredura = red_width_mm + overscan
        distancia_gravacao_mm = (red_height_mm / SCAN_GAP_MM) * largura_real_varredura
        tempo_gravacao_seg = (distancia_gravacao_mm / vel_gravacao) * fator_gravacao 

    return width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg

# ==========================================
# PROCESSAMENTO DE DXF (Blindado para Cores CAD)
# ==========================================
def process_dxf(file_path: str, vel_map: dict, vel_padrao: float, overscan: float, fator_corte: float, fator_gravacao: float):
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    red_entities = []
    black_entities = []
    
    for entity in msp:
        # Pega o índice de cor padrão do AutoCAD (ACI)
        color = entity.dxf.color
        
        # Se for cor por camada (Layer = 256), busca a cor atribuída à camada
        if color == 256: 
            try:
                color = doc.layers.get(entity.dxf.layer).color
            except:
                color = 7 
                
        # O AutoCAD usa a cor 1 para o Vermelho (Gravação).
        # As cores 7 (Branco/Preto padrão), 250 a 255 (Tons de cinza) ou marrons comuns de CAD 
        # serão tratadas como Corte.
        if color == 1:
            red_entities.append(entity)
        else:
            black_entities.append(entity)

    # Medidas globais (Milímetros puros convertidos para centímetros)
    global_bbox = extents(msp)
    width_cm, height_cm = 0.0, 0.0
    if global_bbox.has_data:
        width_cm = (global_bbox.extmax.x - global_bbox.extmin.x) / 10.0
        height_cm = (global_bbox.extmax.y - global_bbox.extmin.y) / 10.0

    # Gravação (Vermelhos - ACI 1)
    tempo_gravacao_seg = 0.0
    if red_entities:
        red_bbox = extents(red_entities)
        if red_bbox.has_data:
            red_width_mm = red_bbox.extmax.x - red_bbox.extmin.x
            red_height_mm = red_bbox.extmax.y - red_bbox.extmin.y
            vel_gravacao = vel_map.get('#FF0000', 300.0) if vel_map.get('#FF0000', 300.0) > 0 else 300.0
            
            SCAN_GAP_MM = 0.05
            largura_real_varredura = red_width_mm + overscan
            distancia_gravacao_mm = (red_height_mm / SCAN_GAP_MM) * largura_real_varredura
            tempo_gravacao_seg = (distancia_gravacao_mm / vel_gravacao) * fator_gravacao

    # Corte (Pretos, Cinzas, Marrons e demais cores)
    cut_perimeter_mm = 0.0
    for entity in black_entities:
        try:
            p = make_path(entity)
            vertices = list(p.flattening(distance=0.1))
            for i in range(1, len(vertices)):
                cut_perimeter_mm += vertices[i-1].distance(vertices[i])
        except:
            continue
            
    vel_corte = vel_map.get('#000000', vel_padrao) if vel_map.get('#000000', vel_padrao) > 0 else vel_padrao
    tempo_corte_seg = (cut_perimeter_mm / vel_corte) * fator_corte

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
    
    res = requests.post(url, json=payload, headers=headers)
    print(f"Update AppSheet Status: {res.status_code} - Resposta: {res.text}")

@app.get("/")
def health_check():
    return {"status": "online", "message": "API Híbrida Operante"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    temp_path = None
    try:
        # Dedo-duro: Mostra no log do Render a URL exata que chegou
        print(f"--- INICIANDO NOVO CÁLCULO ---")
        print(f"URL recebida do AppSheet: {payload.file_url}")
        
        # Download do Arquivo
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(payload.file_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"ERRO DE DOWNLOAD! Status: {response.status_code}")
            print(f"Resposta do AppSheet: {response.text[:300]}")
            raise Exception(f"Erro ao baixar o arquivo. Status HTTP: {response.status_code}")
            
        # Detecta se é SVG ou DXF pelo nome da URL
        is_dxf = payload.file_url.lower().endswith('.dxf')
        temp_path = "/tmp/temp_file.dxf" if is_dxf else "/tmp/temp_file.svg"
        
        with open(temp_path, "wb") as f:
            f.write(response.content)

        vel_map = {}
        if payload.velocidades_por_cor:
            for k, v in payload.velocidades_por_cor.items():
                norm_k = normalize_color(k)
                if norm_k: vel_map[norm_k] = v

        # Processamento inteligente
        if is_dxf:
            if ezdxf is None:
                raise Exception("Biblioteca ezdxf não instalada.")
            width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg = process_dxf(
                temp_path, vel_map, payload.velocidade_padrao_mms, 
                payload.overscan_mm, payload.fator_curvas_corte, payload.fator_pulos_gravacao
            )
        else:
            width_cm, height_cm, tempo_corte_seg, tempo_gravacao_seg = process_svg(
                temp_path, vel_map, payload.velocidade_padrao_mms, 
                payload.overscan_mm, payload.fator_curvas_corte, payload.fator_pulos_gravacao
            )

        resultados = {
            "largura_cm": round(width_cm, 2),
            "altura_cm": round(height_cm, 2),
            "tempo_corte_min": round(tempo_corte_seg / 60.0, 2),
            "tempo_gravacao_min": round(tempo_gravacao_seg / 60.0, 2),
            "tempo_total_min": round((tempo_corte_seg + tempo_gravacao_seg) / 60.0, 2)
        }

        update_appsheet_row(payload.app_id, payload.access_key, payload.table_name, payload.row_id, resultados)
        return {"status": "success", "data": resultados}
        
    except Exception as e:
        print(f"Erro no processamento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
