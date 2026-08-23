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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # Prevenção contra estouro de erro 500 no download
    try:
        response = requests.get(svg_url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Erro no download do SVG. Status HTTP: {response.status_code}")
            return {}
        svg_content = response.content
    except Exception as e:
        print(f"Exceção ao baixar arquivo SVG: {str(e)}")
        return {}

    temp_path = "/tmp/temp_file.svg"
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    perimetros_por_cor: Dict[str, float] = {}

    # Prevenção contra falhas na leitura dos vetores do SVG
    try:
        paths, attributes = svg2paths(temp_path)
        scale_factor_mm = 0.26458333
        
        try:
            tree = ET.fromstring(svg_content)
            width_attr = tree.attrib.get('width', '')
            if 'mm' in width_attr:
                scale_factor_mm = 1.0
        except Exception:
            pass

        for path, attr in zip(paths, attributes):
            cor = get_element_color(attr)
            try:
                comprimento_mm = path.length() * scale_factor_mm
            except Exception:
                comprimento_mm = 0.0
            perimetros_por_cor[cor] = perimetros_por_cor.get(cor, 0.0) + comprimento_mm

    except Exception as e:
        print(f"Aviso na leitura svgpathtools: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    for cor in perimetros_por_cor:
        perimetros_por_cor[cor] = round(perimetros_por_cor[cor], 2)

    return perimetros_por_cor

def update_appsheet_row(app_id: str, access_key: str, table_name: str, row_id: str, perimetro_mm: float, tempo_minutos: float):
    url = f"https://api.appsheet.com/api/v2/apps/{app_id}/tables/{table_name}/Action"
    headers = {
        "ApplicationAccessKey": access_key,
        "Content-Type": "application/json"
    }
    
    # Trata o row_id para número inteiro para casar com a coluna ID numórica no AppSheet
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
    print(f"Resposta AppSheet: {res.text}")

@app.get("/")
def health_check():
    return {"status": "online", "message": "API Operante"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    try:
        perimetros_por_cor = process_svg_by_color(payload.file_url)
        tempo_total_segundos = 0.0
        perimetro_total_mm = 0.0

        vel_map = {k.upper(): v for k, v in payload.velocidades_por_cor.items()}

        for cor, perimetro_mm in perimetros_por_cor.items():
            velocidade = vel_map.get(cor, payload.velocidade_padrao_mms)
            if velocidade <= 0:
                velocidade = payload.velocidade_padrao_mms
            
            tempo_seg = perimetro_mm / velocidade
            tempo_total_segundos += tempo_seg
            perimetro_total_mm += perimetro_mm

        perimetro_final = round(perimetro_total_mm, 2)
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
            "perimetro_total_mm": perimetro_final,
            "tempo_total_minutos": tempo_minutos_final
        }

    except Exception as e:
        print(f"Erro no processamento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
