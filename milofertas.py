#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import random
import time
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import os
import json

# ==================================================
# =================== MODOS ========================
# ==================================================

# "ULTRA"  -> canales nuevos / riesgo cero
# "SAFE"   -> crecimiento lento
# "NORMAL" -> crecimiento estable (recomendado tras 2-3 semanas)
MODO = "ULTRA"

MODOS_CONFIG = {
    "ULTRA": {
        "min_intervalo": 3600,     # 1h
        "max_intervalo": 7200,     # 2h
        "min_descuento": 7,
        "max_envios_dia": 10
    },
    "SAFE": {
        "min_intervalo": 2700,     # 45 min
        "max_intervalo": 5400,     # 90 min
        "min_descuento": 5,
        "max_envios_dia": 20
    },
    "NORMAL": {
        "min_intervalo": 1800,     # 30 min
        "max_intervalo": 3600,     # 60 min
        "min_descuento": 3,
        "max_envios_dia": 35
    }
}

CFG = MODOS_CONFIG[MODO]

# ==================================================
# ================= CONFIG =========================
# ==================================================

PALABRAS_CLAVE = [
    "hogar", "electronica", "deporte",
    "cocina", "bricolaje", "oficina"
]

HEADERS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/121.0"},
    {"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605.1.15"},
]

TAG_AFILIADO = "crt06f-21"
HORA_INICIO = 9
HORA_FIN = 22

HISTORIAL_FILE = "enviados_historial.json"
ENVIO_DIARIO_FILE = "envios_diarios.json"

# ---------------- TELEGRAM ----------------
TELEGRAM_TOKEN = "7711722254:AAFAscovZ44PJpbYuJHKVgFevSNy-himSc4"
CHAT_ID = "@Milofertazos"


# ==================================================
# ================= UTILIDADES =====================
# ==================================================

def log(msg):
    # flush=True asegura que los prints se muestren al momento en GitHub Actions
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def horario_permitido():
    h = datetime.now().hour
    permitido = HORA_INICIO <= h < HORA_FIN
    if not permitido:
        log(f"Fuera de horario permitido ({h}h). Horario activo: {HORA_INICIO}h - {HORA_FIN}h")
    return permitido

def cargar_json(path, default):
    if not os.path.exists(path):
        log(f"Archivo JSON no encontrado en {path}, usando valor por defecto.")
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            log(f"JSON cargado exitosamente desde {path}")
            return data
    except Exception as e:
        log(f"Error al cargar JSON {path}: {e}. Usando valor por defecto.")
        return default

def guardar_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log(f"JSON guardado correctamente en {path}")
    except Exception as e:
        log(f"Error al guardar JSON en {path}: {e}")

# ==================================================
# ============ CONTROL DE RIESGO ===================
# ==================================================

def evaluar_riesgo():
    envios = cargar_json(ENVIO_DIARIO_FILE, {})
    hoy = datetime.now().strftime("%Y-%m-%d")
    enviados_hoy = envios.get(hoy, 0)
    log(f"Envíos realizados hoy ({hoy}): {enviados_hoy}/{CFG['max_envios_dia']}")

    if enviados_hoy >= CFG["max_envios_dia"]:
        log("RIESGO: límite diario alcanzado → pausa")
        return False

    return True

def registrar_envio():
    envios = cargar_json(ENVIO_DIARIO_FILE, {})
    hoy = datetime.now().strftime("%Y-%m-%d")
    envios[hoy] = envios.get(hoy, 0) + 1
    guardar_json(ENVIO_DIARIO_FILE, envios)
    log(f"Envío registrado. Total hoy: {envios[hoy]}")

# ==================================================
# ============== AMAZON ============================
# ==================================================

def extract_asin(url):
    m = re.search(r"/dp/([A-Z0-9]{10})", url)
    return m.group(1) if m else None

def crear_url(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG_AFILIADO}"

def get_html(url):
    try:
        espera = random.uniform(1.5, 3)
        time.sleep(espera)
        header_elegido = random.choice(HEADERS)
        log(f"Realizando petición HTTP a: {url}")
        r = requests.get(url, headers=header_elegido, timeout=20)
        log(f"Respuesta HTTP recibida con estatus: {r.status_code}")
        return r.text if r.status_code == 200 else None
    except Exception as e:
        log(f"Excepción en get_html para {url}: {e}")
        return None

def parse_precio(txt):
    if not txt:
        return None
    txt = txt.replace("€", "").replace(",", ".")
    try:
        return float(re.findall(r"[\d\.]+", txt)[0])
    except:
        return None

def extraer_precios(soup):
    act = soup.select_one(".aok-offscreen")
    ant = soup.select_one(".a-price.a-text-price .a-offscreen")
    p_act = parse_precio(act.text) if act else None
    p_ant = parse_precio(ant.text) if ant else None
    desc = round((p_ant - p_act) / p_ant * 100) if p_act and p_ant else 0
    return p_act, p_ant, desc

# ==================================================
# ============== TELEGRAM ==========================
# ==================================================

def generar_mensaje(p):
    textos = [
        f"{p['titulo']}\n\nPrecio actual: {p['precio']} €\nMás información:\n{p['url']}",
        f"{p['titulo']}\n\nCoste: {p['precio']} €\nEnlace:\n{p['url']}",
        f"{p['titulo']}\n\nDisponible en Amazon:\n{p['url']}"
    ]
    return random.choice(textos)

def enviar_telegram(p):
    try:
        log(f"Iniciando descarga de imagen para ASIN {p['asin']} desde: {p['imagen']}")
        img_resp = requests.get(p["imagen"], timeout=20)
        if img_resp.status_code != 200:
            log(f"Error al descargar imagen. Código HTTP: {img_resp.status_code}")
            return
        
        img = img_resp.content
        log(f"Imagen descargada con éxito. Enviando mensaje a Telegram (Chat ID: {CHAT_ID})...")
        
        caption_texto = generar_mensaje(p)
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption_texto},
            files={"photo": ("img.jpg", img)},
            timeout=30
        )
        
        log(f"Respuesta de la API de Telegram - Status Code: {r.status_code}")
        log(f"Cuerpo de respuesta Telegram: {r.text}")

        if r.status_code == 200:
            registrar_envio()
            log(f"¡Enviado con éxito a Telegram! ASIN: {p['asin']}")
        else:
            log(f"Telegram rechazó el mensaje. Código: {r.status_code}")
    except Exception as e:
        log(f"Excepción crítica en enviar_telegram: {e}")

# ==================================================
# ============== BUSQUEDA ==========================
# ==================================================

def buscar_productos():
    palabra = random.choice(PALABRAS_CLAVE)
    log(f"Buscando productos en Amazon usando la palabra clave: '{palabra}'")
    urls = set()

    for page in range(1, 4):
        url_busqueda = f"https://www.amazon.es/s?k={palabra}&page={page}"
        html = get_html(url_busqueda)
        if not html:
            log(f"No se pudo obtener HTML para la página {page} de la búsqueda.")
            continue
        soup = BeautifulSoup(html, "html.parser")
        encontrados_pagina = 0
        for a in soup.select("a[href*='/dp/']"):
            asin = extract_asin(a.get("href", ""))
            if asin:
                urls.add(f"https://www.amazon.es/dp/{asin}")
                encontrados_pagina += 1
        log(f"Página {page}: Encontrados {encontrados_pagina} enlaces de productos.")

    log(f"Total de URLs únicas recolectadas en esta búsqueda: {len(urls)}")
    return list(urls)

def obtener_producto(url, historial):
    asin = extract_asin(url)
    if not asin:
        return None
    if asin in historial:
        log(f"ASIN {asin} ya se encuentra en el historial. Saltando.")
        return None

    log(f"Analizando producto con ASIN: {asin}")
    html = get_html(url)
    if not html:
        log(f"No se pudo obtener el HTML del producto ASIN {asin}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    precio, _, desc = extraer_precios(soup)
    log(f"ASIN {asin} -> Precio: {precio} € | Descuento detectado: {desc}% (Mínimo requerido: {CFG['min_descuento']}%)")

    if not precio or desc < CFG["min_descuento"]:
        log(f"Producto descartado (Precio nulo o descuento inferior al mínimo).")
        return None

    titulo = soup.select_one("#productTitle")
    imagen = soup.select_one("#landingImage")

    historial.add(asin)
    guardar_json(HISTORIAL_FILE, list(historial))

    prod_data = {
        "asin": asin,
        "titulo": titulo.text.strip() if titulo else "Producto Amazon",
        "precio": precio,
        "imagen": imagen["src"] if imagen else None,
        "url": crear_url(asin)
    }
    log(f"Producto válido encontrado: {prod_data['titulo'][:40]}... (ASIN: {asin})")
    return prod_data

# ==================================================
# ================= MAIN ===========================
# ==================================================

def main():
    historial = set(cargar_json(HISTORIAL_FILE, []))
    log(f"Sistema iniciado | MODO={MODO} | Historial cargado con {len(historial)} elementos.")

    # Validación de horario comentada temporalmente para pruebas
    # if not horario_permitido():
    #     log("Fuera de horario permitido. Finalizando ejecución.")
    #     return

    if not evaluar_riesgo():
        log("Límite de riesgo diario alcanzado. Finalizando ejecución.")
        return

    urls = buscar_productos()
    enviado = False

    for url in urls:
        p = obtener_producto(url, historial)
        if p:
            enviar_telegram(p)
            enviado = True
            break

    if not enviado:
        log("No se encontró ningún producto válido para enviar en este ciclo.")

    log("Ejecución finalizada con éxito.")

if __name__ == "__main__":
    main()
