from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import xml.etree.ElementTree as ET
import os
import re
from typing import Dict, Optional
from svgpathtools import svg2paths

app = FastAPI(title="Calculador de SVG para Laser por Cores + AppSheet Integration")

class ColorSpeed(BaseModel):
    file_url: str
    row_id: str                          # ID/Key do registro na tabela do AppSheet
    app_id: str                          # App ID do AppSheet
    access_key: str                      # Application Access Key do AppSheet
    table_name: str = "Formação Preço"   # Nome da tabela no AppSheet
    velocidades_por_cor: Dict[str, float]
    velocidade_padrao_mms: float = 20.0

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

def process_svg_by_color(svg_url: str):
    response = requests.get(svg_url)
    if response.status_code != 200:
        raise Exception("Não foi possível baixar o arquivo SVG.")
    
    svg_content = response.content
    temp_path = "/tmp/temp_file.svg"
    
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    paths, attributes = svg2paths(temp_path)
    
    scale_factor_mm = 0.26458333
    try:
        tree = ET.fromstring(svg_content)
        width_attr = tree.attrib.get('width', '')
        if 'mm' in width_attr:
            scale_factor_mm = 1.0
    except Exception:
        pass

    if os.path.exists(temp_path):
        os.remove(temp_path)

    perimetros_por_cor: Dict[str, float] = {}

    for path, attr in zip(paths, attributes):
        cor = get_element_color(attr)
        comprimento_mm = path.length() * scale_factor_mm
        
        if cor in perimetros_por_cor:
            perimetros_por_cor[cor] += comprimento_mm
        else:
            perimetros_por_cor[cor] = comprimento_mm

    for cor in perimetros_por_cor:
        perimetros_por_cor[cor] = round(perimetros_por_cor[cor], 2)

    return perimetros_por_cor

def update_appsheet_row(app_id: str, access_key: str, table_name: str, row_id: str, perimetro_mm: float, tempo_minutos: float):
    """Envia requisição POST para a API do AppSheet para atualizar a linha"""
    url = f"https://api.appsheet.com/api/v2/apps/{app_id}/tables/{table_name}/Action"
    
    headers = {
        "ApplicationAccessKey": access_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "Action": "Edit",
        "Properties": {
            "Locale": "pt-BR",
            "Timezone": "E. South America Standard Time"
        },
        "Rows": [
            {
                "ID": row_id,  # Certifique-se de que a coluna de chave primária se chama ID na sua tabela
                "Tempo_Corte_Minutos": tempo_minutos,
                "Perimetro_Total_MM": perimetro_mm
            }
        ]
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code not in [200, 201]:
        print(f"Erro ao atualizar AppSheet: {response.text}")

@app.get("/")
def health_check():
    return {"status": "online", "message": "API de Cálculo de SVG com Atualização do AppSheet Operante!"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    try:
        perimetros_por_cor = process_svg_by_color(payload.file_url)
        
        detalhes_por_cor = {}
        tempo_total_segundos = 0.0
        perimetro_total_mm = 0.0

        vel_map = {k.upper(): v for k, v in payload.velocidades_por_cor.items()}

        for cor, perimetro_mm in perimetros_por_cor.items():
            velocidade = vel_map.get(cor, payload.velocidade_padrao_mms)
            tempo_seg = round(perimetro_mm / velocidade, 2)
            tempo_min = round(tempo_seg / 60.0, 2)
            
            detalhes_por_cor[cor] = {
                "perimetro_mm": perimetro_mm,
                "velocidade_mms": velocidade,
                "tempo_segundos": tempo_seg,
                "tempo_minutos": tempo_min
            }
            
            tempo_total_segundos += tempo_seg
            perimetro_total_mm += perimetro_mm

        perimetro_final = round(perimetro_total_mm, 2)
        tempo_minutos_final = round(tempo_total_segundos / 60.0, 2)

        # Atualiza o AppSheet via REST API
        update_appsheet_row(
            app_id=payload.app_id,
            access_key=payload.access_key,
            table_name=payload.table_name,
            row_id=payload.row_id,
            perimetro_mm=perimetro_final,
            tempo_minutos=tempo_minutos_final
        )

        return {
            "status": "success",
            "perimetro_total_mm": perimetro_final,
            "tempo_total_segundos": round(tempo_total_segundos, 2),
            "tempo_total_minutos": tempo_minutos_final,
            "detalhes_por_cor": detalhes_por_cor
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
