from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import xml.etree.ElementTree as ET
import os
import re
from typing import Dict, Optional
from svgpathtools import svg2paths

app = FastAPI(title="Calculador de SVG para Laser por Cores")

class ColorSpeed(BaseModel):
    # Dicionário de cores em Hex (ex: "#000000", "#FF0000") e suas velocidades em mm/s
    # Exemplo: {"#000000": 20.0, "#FF0000": 100.0}
    velocidades_por_cor: Dict[str, float]
    velocidade_padrao_mms: float = 20.0
    file_url: str

def normalize_color(color_str: Optional[str]) -> Optional[str]:
    """Normaliza cores hexadecimais (ex: #f00 -> #FF0000, red -> #FF0000, #ff0000 -> #FF0000)"""
    if not color_str or color_str.lower() in ['none', 'transparent']:
        return None
    
    color_str = color_str.strip().upper()
    
    # Mapeamento simples de cores primárias comuns
    color_map = {
        'BLACK': '#000000',
        'RED': '#FF0000',
        'BLUE': '#0000FF',
        'GREEN': '#008000',
        'YELLOW': '#FFFF00',
        'CYAN': '#00FFFF',
        'MAGENTA': '#FF00FF'
    }
    
    if color_str in color_map:
        return color_map[color_str]
        
    if color_str.startswith('#'):
        if len(color_str) == 4: # #F00 -> #FF0000
            return f"#{color_str[1]*2}{color_str[2]*2}{color_str[3]*2}"
        return color_str
        
    # Busca por formato rgb(r,g,b)
    rgb_match = re.search(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', color_str.lower())
    if rgb_match:
        r, g, b = map(int, rgb_match.groups())
        return f"#{r:02X}{g:02X}{b:02X}"
        
    return color_str

def get_element_color(attr: dict) -> str:
    """Extrai a cor principal da linha (stroke) ou preenchimento (fill) do elemento"""
    # 1. Procura no stroke direto
    stroke = attr.get('stroke')
    if stroke and stroke.lower() != 'none':
        norm = normalize_color(stroke)
        if norm: return norm
        
    # 2. Procura dentro do atributo style="stroke:#000000;..."
    style = attr.get('style', '')
    if style:
        stroke_match = re.search(r'stroke\s*:\s*([^;]+)', style)
        if stroke_match:
            norm = normalize_color(stroke_match.group(1))
            if norm: return norm
            
    # 3. Se não tiver stroke, tenta pelo fill
    fill = attr.get('fill')
    if fill and fill.lower() != 'none':
        norm = normalize_color(fill)
        if norm: return norm
        
    return "#000000" # Se não definir cor, assume Preto (#000000)

def process_svg_by_color(svg_url: str):
    response = requests.get(svg_url)
    if response.status_code != 200:
        raise Exception("Não foi possível baixar o arquivo SVG.")
    
    svg_content = response.content
    temp_path = "/tmp/temp_file.svg"
    
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    paths, attributes = svg2paths(temp_path)
    
    # Fator de escala (px para mm)
    scale_factor_mm = 0.26458333 # Padrão 96 DPI
    try:
        tree = ET.fromstring(svg_content)
        width_attr = tree.attrib.get('width', '')
        if 'mm' in width_attr:
            scale_factor_mm = 1.0
    except Exception:
        pass

    if os.path.exists(temp_path):
        os.remove(temp_path)

    # Agrupa perímetros por cor Hex
    perimetros_por_cor: Dict[str, float] = {}

    for path, attr in zip(paths, attributes):
        cor = get_element_color(attr)
        comprimento_mm = path.length() * scale_factor_mm
        
        if cor in perimetros_por_cor:
            perimetros_por_cor[cor] += comprimento_mm
        else:
            perimetros_por_cor[cor] = comprimento_mm

    # Arredonda perímetros
    for cor in perimetros_por_cor:
        perimetros_por_cor[cor] = round(perimetros_por_cor[cor], 2)

    return perimetros_por_cor

@app.get("/")
def health_check():
    return {"status": "online", "message": "API de Cálculo de SVG com Suporte a Cores Operante!"}

@app.post("/calcular-corte")
def calcular_corte(payload: ColorSpeed):
    try:
        perimetros_por_cor = process_svg_by_color(payload.file_url)
        
        detalhes_por_cor = {}
        tempo_total_segundos = 0.0
        perimetro_total_mm = 0.0

        # Mapeamento de velocidades recebidas (converte todas as chaves de cor para maiúsculas)
        vel_map = {k.upper(): v for k, v in payload.velocidades_por_cor.items()}

        for cor, perimetro_mm in perimetros_por_cor.items():
            # Pega a velocidade associada à cor, se não houver usa a velocidade padrão
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

        return {
            "status": "success",
            "perimetro_total_mm": round(perimetro_total_mm, 2),
            "tempo_total_segundos": round(tempo_total_segundos, 2),
            "tempo_total_minutos": round(tempo_total_segundos / 60.0, 2),
            "detalhes_por_cor": detalhes_por_cor
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
