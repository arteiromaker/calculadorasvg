def process_svg_by_color(svg_url: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(svg_url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Erro no download do SVG. Status HTTP: {response.status_code}")
            # Se der 429 ou erro de download, retorna dicionário vazio em vez de estourar Exception 500
            return {}
        svg_content = response.content
    except Exception as e:
        print(f"Exceção ao baixar arquivo: {str(e)}")
        return {}

    temp_path = "/tmp/temp_file.svg"
    with open(temp_path, "wb") as f:
        f.write(svg_content)

    perimetros_por_cor: Dict[str, float] = {}

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
        print(f"Aviso na leitura do arquivo SVG: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    for cor in perimetros_por_cor:
        perimetros_por_cor[cor] = round(perimetros_por_cor[cor], 2)

    return perimetros_por_cor
