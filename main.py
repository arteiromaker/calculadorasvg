from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import re
import os
from typing import Dict, Optional
from svgpathtools import svg2paths

app = FastAPI(title="Calculador de SVG para Laser por Cores + AppSheet Integration")

class ColorSpeed(BaseModel):
    file_url: str
    row_id: str
    app_id: str
    access_key: str
    table_name: str = "Formação Preço"
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
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(svg_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return {}
        svg_content = response.content
    except Exception:
        return {}

    temp_path = "/tmp/temp_file.svg"
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    perimetros_por_cor: Dict[str, float] = {}

    try:
        paths, attributes = svg2paths(temp_path)
        for path, attr in zip(paths, attributes):
            cor = get_element_color(attr)
            cor = cor.upper() if cor else "#000000"

            try:
                # Comprimento puro retornado pelo svgpathtools
                comprimento_puro = path.length()
            except Exception:
                comprimento_puro = 0.0
                
            perimetros_por_cor[cor] = perimetros_por_cor.get(cor, 0.0) + comprimento_puro

    except Exception as e:
        print(f"Erro no processamento SVG: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return perimetros_por_cor

def update_appsheet_row(app_id: str, access_key: str, table_name: str, row_id: str, perimetro_mm: float, tempo_minutos: float):
    url = f"https://api.appsheet.com/api/v2/apps/{app_id}/tables/{table_name}/Action"
    headers = {
        "ApplicationAccessKey": access_key,
        "Content-Type": "application/json"
    }
    
    try:
        formatted_row_id = int(row_id)
    except ValueError:
        formatted_row_id = str(row_id)

    payload = {
        "Action": "Edit",
        "Properties": {
            "Locale": "pt-BR",
            "Timezone": "E. South America Standard Time"
        },
        "Rows": [
            {
                "ID": formatted_row_id,
                "Tempo_Corte_Minutos": float(tempo_minutos),
                "Perimetro_Total_MM": float(perimetro_mm)
            }
        ]
    }
    
    res = requests.post(url, json=payload, headers=headers)
    print(f"Update AppSheet Status: {res.status_code}")

@app.get("/")
def health_check():
    return {"status": "online", "message": "API Operante"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    try:
        perimetros_puros = process_svg_by_color(payload.file_url)
        tempo_total_segundos = 0.0
        perimetro_total_puro = 0.0

        vel_map = {k.upper(): v for k, v in payload.velocidades_por_cor.items()}

        for cor, comprimento_puro in perimetros_puros.items():
            velocidade = vel_map.get(cor, payload.velocidade_padrao_mms)
            if velocidade <= 0:
                velocidade = payload.velocidade_padrao_mms
            
            # Soma pura das linhas sem nenhum divisor ou multiplicador
            perimetro_total_puro += comprimento_puro
            
            # Tempo simples de percorrer a linha: comprimento_puro / velocidade
            tempo_seg = comprimento_puro / velocidade
            tempo_total_segundos += tempo_seg

        perimetro_final = round(perimetro_total_puro, 2)
        tempo_minutos_final = round(tempo_total_segundos / 60.0, 2)

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
            "perimetro_total_puro": perimetro_final,
            "tempo_total_minutos_puro": tempo_minutos_final
        }

    except Exception as e:
        print(f"Erro no processamento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
