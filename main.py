from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import re
import os
from typing import Dict, Optional
import svgelements as svg

app = FastAPI(title="Calculador de SVG para Laser por Cores + AppSheet Integration")

class ColorSpeed(BaseModel):
    file_url: str
    row_id: str
    app_id: str
    access_key: str
    table_name: str = "Formação Preço"
    velocidades_por_cor: Dict[str, float]
    velocidade_padrao_mms: float = 20.0

def normalize_color(color_obj) -> str:
    """Normaliza qualquer objeto de cor do svgelements para Hexadecimal (#RRGGBB)"""
    if not color_obj or color_obj.value is None:
        return "#000000"
    
    hex_str = str(color_obj.hex).upper()
    if hex_str.startswith('#'):
        if len(hex_str) == 4: # #RGB -> #RRGGBB
            return f"#{hex_str[1]*2}{hex_str[2]*2}{hex_str[3]*2}"
        return hex_str[:7] # Remove canal alpha se existir
    return "#000000"

def process_svg_by_color(svg_url: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(svg_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return {}, 0.0
        svg_content = response.content
    except Exception as e:
        print(f"Erro ao baixar SVG: {str(e)}")
        return {}, 0.0

    temp_path = "/tmp/temp_file.svg"
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    perimetros_por_cor: Dict[str, float] = {}
    
    try:
        # Carrega o SVG convertendo todas as matrizes e transformações para milímetros (ppi=96)
        loaded_svg = svg.SVG.parse(temp_path, ppi=96.0)
        
        # 1. Bounding Box do desenho inteiro em Milímetros
        bbox = loaded_svg.bbox()
        area_cm2 = 0.0
        if bbox:
            # bbox = (xmin, ymin, xmax, ymax) em pixels (96 ppi)
            width_mm = (bbox[2] - bbox[0]) * (25.4 / 96.0)
            height_mm = (bbox[3] - bbox[1]) * (25.4 / 96.0)
            area_cm2 = round((width_mm / 10.0) * (height_mm / 10.0), 2)

        # 2. Varre os elementos aplicando as transformações e medindo as linhas em MM
        for element in loaded_svg.elements():
            if isinstance(element, svg.Shape):
                # Extrai a cor da linha (stroke) ou preenchimento (fill)
                cor = normalize_color(element.stroke)
                if cor == "#000000" and element.fill and element.fill.value is not None:
                    cor = normalize_color(element.fill)

                try:
                    # length() do svgelements em mm (1 px = 25.4/96 mm)
                    length_px = element.length()
                    length_mm = length_px * (25.4 / 96.0)
                except Exception:
                    length_mm = 0.0

                perimetros_por_cor[cor] = perimetros_por_cor.get(cor, 0.0) + length_mm

    except Exception as e:
        print(f"Erro na leitura svgelements: {str(e)}")
        area_cm2 = 0.0
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    for cor in perimetros_por_cor:
        perimetros_por_cor[cor] = round(perimetros_por_cor[cor], 2)

    return perimetros_por_cor, area_cm2

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
    print(f"Resposta AppSheet: {res.text}")

@app.get("/")
def health_check():
    return {"status": "online", "message": "API Operante"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    try:
        perimetros_por_cor, area_cm2 = process_svg_by_color(payload.file_url)
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
            "tempo_total_minutos": tempo_minutos_final,
            "area_cm2": area_cm2
        }

    except Exception as e:
        print(f"Erro no processamento: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
