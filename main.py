from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import xml.etree.ElementTree as ET
import os
from svgpathtools import svg2paths

app = FastAPI(title="Calculador de SVG para Laser")

class SVGRequest(BaseModel):
    file_url: str
    velocidade_mms: float = 20.0  # Padrão: 20 mm/s se não informado

def process_svg_geometry(svg_url: str) -> float:
    response = requests.get(svg_url)
    if response.status_code != 200:
        raise Exception("Não foi possível baixar o arquivo SVG da URL fornecida.")
    
    svg_content = response.content
    temp_path = "/tmp/temp_file.svg"
    
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    # svgpathtools extrai todas as trajetórias (paths, rect, circle, lines, etc.)
    paths, attributes = svg2paths(temp_path)
    
    perimetro_total_px = sum(path.length() for path in paths)

    # Trata fator de escala (conversão de pixels/DPI para mm)
    scale_factor_mm = 0.26458333  # Padrão 96 DPI (1px = 0.264583 mm)
    
    try:
        tree = ET.fromstring(svg_content)
        width_attr = tree.attrib.get('width', '')
        # Se o SVG já foi exportado com dimensões nativas em mm
        if 'mm' in width_attr:
            scale_factor_mm = 1.0
    except Exception:
        pass

    # Limpa arquivo temporário
    if os.path.exists(temp_path):
        os.remove(temp_path)

    perimetro_mm = perimetro_total_px * scale_factor_mm
    return round(perimetro_mm, 2)

@app.get("/")
def health_check():
    return {"status": "online", "message": "API de Cálculo de SVG operante!"}

@app.post("/calcular-corte")
def calcular_corte(payload: SVGRequest):
    try:
        perimetro_mm = process_svg_geometry(payload.file_url)
        tempo_segundos = round(perimetro_mm / payload.velocidade_mms, 2)
        tempo_minutos = round(tempo_segundos / 60.0, 2)

        return {
            "status": "success",
            "perimetro_mm": perimetro_mm,
            "perimetro_m": round(perimetro_mm / 1000.0, 3),
            "tempo_segundos": tempo_segundos,
            "tempo_minutos": tempo_minutos
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
