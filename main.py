from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import re
import os
from typing import Dict, Optional
from svgpathtools import svg2paths

app = FastAPI(title="Calculador de SVG para Laser por Cores + AppSheet Integration")

# 1. Adicionamos os novos parâmetros opcionais no modelo (com valores padrão de segurança)
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
    if not color_str or color_str.lower() in ['none', 'transparent']:
        return None
    color_str = color_str.strip().upper()
    color_map = {
        'BLACK': '#000000', 'RED': '#FF0000', 'BLUE': '#0000FF',
        'GREEN': '#008000', 'YELLOW': '#FFFF00', 'CYAN': '#00FFFF', 'MAGENTA': '#FF00FF'
    }
    if color_str in color_map:
        return color_map[color_str]
    if color_str.startswith('#'):
        if len(color_str) == 4:
            return f"#{color_str[1]*2}{color_str[2]*2}{color_str[3]*2}"
        return color_str
    rgb_match = re.search(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', color_str.lower())
    if rgb_match:
        r, g, b = map(int, rgb_match.groups())
        return f"#{r:02X}{g:02X}{b:02X}"
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

# 2. Atualizamos a função para receber os novos parâmetros
def process_svg_and_calculate(svg_url: str, vel_map: dict, vel_padrao: float, overscan: float, fator_corte: float, fator_gravacao: float):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(svg_url, headers=headers, timeout=15)
        if response.status_code != 200:
            raise Exception("Erro ao baixar o SVG.")
        svg_content = response.content
    except Exception as e:
        raise Exception(f"Falha no download: {str(e)}")

    temp_path = "/tmp/temp_file.svg"
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    SCALE_PX_TO_MM = 25.4 / 72.0  
    SCAN_GAP_MM = 0.05            

    global_xmin, global_xmax = float('inf'), float('-inf')
    global_ymin, global_ymax = float('inf'), float('-inf')
    red_xmin, red_xmax = float('inf'), float('-inf')
    red_ymin, red_ymax = float('inf'), float('-inf')
    
    cut_perimeter_px = 0.0

    try:
        paths, attributes = svg2paths(temp_path)
        for path, attr in zip(paths, attributes):
            if not path or path.length() == 0:
                continue
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
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    width_cm, height_cm = 0.0, 0.0
    if global_xmin != float('inf'):
        width_cm = ((global_xmax - global_xmin) * SCALE_PX_TO_MM) / 10.0
        height_cm = ((global_ymax - global_ymin) * SCALE_PX_TO_MM) / 10.0

    # Usa os fatores que vieram do AppSheet
    cut_perimeter_mm = cut_perimeter_px * SCALE_PX_TO_MM
    vel_corte = vel_map.get('#000000', vel_padrao)
    if vel_corte <= 0: vel_corte = vel_padrao
    
    tempo_corte_matematico = cut_perimeter_mm / vel_corte
    tempo_corte_seg = tempo_corte_matematico * fator_corte 

    tempo_gravacao_seg = 0.0
    if red_xmin != float('inf'):
        red_width_mm = (red_xmax - red_xmin) * SCALE_PX_TO_MM
        red_height_mm = (red_ymax - red_ymin) * SCALE_PX_TO_MM
        
        vel_gravacao = vel_map.get('#FF0000', 300.0) 
        if vel_gravacao <= 0: vel_gravacao = 300.0
        
        largura_real_varredura = red_width_mm + overscan
        distancia_gravacao_mm = (red_height_mm / SCAN_GAP_MM) * largura_real_varredura
        
        tempo_gravacao_matematico = distancia_gravacao_mm / vel_gravacao
        tempo_gravacao_seg = tempo_gravacao_matematico * fator_gravacao 

    return {
        "largura_cm": round(width_cm, 2),
        "altura_cm": round(height_cm, 2),
        "tempo_corte_min": round(tempo_corte_seg / 60.0, 2),
        "tempo_gravacao_min": round(tempo_gravacao_seg / 60.0, 2),
        "tempo_total_min": round((tempo_corte_seg + tempo_gravacao_seg) / 60.0, 2)
    }

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
    return {"status": "online", "message": "API Operante"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    try:
        vel_map = {}
        if payload.velocidades_por_cor:
            for k, v in payload.velocidades_por_cor.items():
                norm_k = normalize_color(k)
                if norm_k:
                    vel_map[norm_k] = v

        # 3. Agora enviamos os parâmetros extraídos do AppSheet para o cálculo
        resultados = process_svg_and_calculate(
            svg_url=payload.file_url,
            vel_map=vel_map,
            vel_padrao=payload.velocidade_padrao_mms,
            overscan=payload.overscan_mm,
            fator_corte=payload.fator_curvas_corte,
            fator_gravacao=payload.fator_pulos_gravacao
        )

        update_appsheet_row(
            app_id=payload.app_id,
            access_key=payload.access_key,
            table_name=payload.table_name,
            row_id=payload.row_id,
            resultados=resultados
        )

        return {"status": "success", "data": resultados}
        
    except Exception as e:
        print(f"Erro no processamento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
