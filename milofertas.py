#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import os
import json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# ==================================================
# =================== MODOS ========================
# ==================================================

MODO = "ULTRA"

MODOS_CONFIG = {
    "ULTRA": {
        "min_intervalo": 600,      # 10 minutos
        "max_intervalo": 600,      # 10 minutos
        "min_descuento": 0,        # acepta cualquier producto con precio
        "max_envios_dia": 144      # 1 envío cada 10 minutos durante 24h
    },
    "SAFE": {
        "min_intervalo": 600,
        "max_intervalo": 600,
        "min_descuento": 0,
        "max_envios_dia": 144
    },
    "NORMAL": {
        "min_intervalo": 600,
        "max_intervalo": 600,
        "min_descuento": 0,
        "max_envios_dia": 144
    }
}

CFG = MODOS_CONFIG[MODO]


# ==================================================
# ================= CONFIG =========================
# ==================================================

PALABRAS_CLAVE = [
    "hogar",
    "electronica",
    "deporte",
    "cocina",
    "bricolaje",
    "oficina",
    "ropa",
    "bolsos",
    "bañador",
    "decoración"
]

TAG_AFILIADO = "crt06f-21"

# 24/7
HORA_INICIO = 0
HORA_FIN = 24

HISTORIAL_FILE = "enviados_historial.json"
ENVIO_DIARIO_FILE = "envios_diarios.json"

# ---------------- TELEGRAM ----------------
# Sustituye este valor por tu token real si quieres dejarlo fijo en el script.
TELEGRAM_TOKEN = "7711722254:AAFAscovZ44PJpbYuJHKVgFevSNy-himSc4"
CHAT_ID = "@Milofertazos"

# -------------- ZONA HORARIA ----------------
TIMEZONE = ZoneInfo("Europe/Madrid")


# ==================================================
# ================= UTILIDADES =====================
# ==================================================

def log(msg):
    ahora_local = datetime.now(TIMEZONE)
    print(f"[{ahora_local.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def horario_permitido():
    h = datetime.now(TIMEZONE).hour
    permitido = HORA_INICIO <= h < HORA_FIN

    if not permitido:
        log(
            f"Fuera de horario permitido ({h}h). "
            f"Horario activo: {HORA_INICIO}h - {HORA_FIN}h"
        )

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
            json.dump(data, f, indent=2, ensure_ascii=False)

        log(f"JSON guardado correctamente en {path}")
    except Exception as e:
        log(f"Error al guardar JSON en {path}: {e}")


# ==================================================
# ============ CONTROL DE RIESGO ===================
# ==================================================

def evaluar_riesgo():
    envios = cargar_json(ENVIO_DIARIO_FILE, {})
    hoy = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    enviados_hoy = envios.get(hoy, 0)

    log(
        f"Envíos realizados hoy ({hoy}): "
        f"{enviados_hoy}/{CFG['max_envios_dia']}"
    )

    if enviados_hoy >= CFG["max_envios_dia"]:
        log("RIESGO: límite diario alcanzado. Finalizando ejecución.")
        return False

    return True


def registrar_envio():
    envios = cargar_json(ENVIO_DIARIO_FILE, {})
    hoy = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    envios[hoy] = envios.get(hoy, 0) + 1

    guardar_json(ENVIO_DIARIO_FILE, envios)
    log(f"Envío registrado. Total hoy: {envios[hoy]}")


# ==================================================
# ============== PLAYWRIGHT SCRAPER ================
# ==================================================

def extract_asin(url):
    if not url:
        return None

    m = re.search(r"/dp/([A-Z0-9]{10})", url)

    if m:
        return m.group(1)

    m = re.search(r"/gp/product/([A-Z0-9]{10})", url)

    if m:
        return m.group(1)

    return None


def crear_url(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG_AFILIADO}"


def get_page_html(url):
    """
    Obtiene el HTML completo usando Playwright.
    Versión corregida para evitar errores de entidades HTML.
    """

    inicio = time.time()

    with sync_playwright() as p:
        browser = None

        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--window-size=1920,1080",
                ]
            )

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="es-ES",
                timezone_id="Europe/Madrid",
                viewport={"width": 1920, "height": 1080},
            )

            page = context.new_page()

            espera = random.uniform(2.0, 4.0)
            time.sleep(espera)

            log(f"Navegando con Playwright a: {url}")

            page.goto(
                url,
                timeout=60000,
                wait_until="domcontentloaded"
            )

            time.sleep(2)

            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3);")
                time.sleep(1)
            except Exception as e:
                log(f"No se pudo hacer scroll en la página: {e}")

            html_content = page.content()

            duracion = time.time() - inicio
            log(f"HTML obtenido correctamente en {duracion:.2f}s")

            return html_content

        except Exception as e:
            log(f"Excepción en Playwright al cargar {url}: {e}")
            return None

        finally:
            if browser:
                try:
                    browser.close()
                except Exception as e:
                    log(f"Error cerrando navegador: {e}")


def parse_precio(txt):
    if not txt:
        return None

    txt = txt.replace("\xa0", " ")
    txt = txt.replace("€", "")
    txt = txt.replace(".", "")
    txt = txt.replace(",", ".")
    txt = txt.strip()

    try:
        encontrados = re.findall(r"\d+(?:\.\d+)?", txt)

        if not encontrados:
            return None

        return float(encontrados[0])

    except Exception:
        return None


def extraer_precios(soup):
    """
    Extrae precio actual, precio anterior y descuento si están disponibles.
    """

    selectores_precio_actual = [
        "#corePrice_feature_div .a-offscreen",
        ".priceToPay .a-offscreen",
        ".apexPriceToPay .a-offscreen",
        ".reinventPricePriceToPayMargin .a-offscreen",
        ".a-price .a-offscreen",
        ".aok-offscreen",
    ]

    selectores_precio_anterior = [
        ".a-price.a-text-price .a-offscreen",
        ".basisPrice .a-offscreen",
        ".a-text-price .a-offscreen",
    ]

    precio_actual = None
    precio_anterior = None

    for selector in selectores_precio_actual:
        nodo = soup.select_one(selector)

        if nodo:
            precio_actual = parse_precio(nodo.get_text(strip=True))

            if precio_actual:
                break

    for selector in selectores_precio_anterior:
        nodo = soup.select_one(selector)

        if nodo:
            precio_anterior = parse_precio(nodo.get_text(strip=True))

            if precio_anterior:
                break

    descuento = 0

    if precio_actual and precio_anterior and precio_anterior > precio_actual:
        try:
            descuento = round(
                ((precio_anterior - precio_actual) / precio_anterior) * 100
            )
        except Exception:
            descuento = 0

    return precio_actual, precio_anterior, descuento


# ==================================================
# ============== TELEGRAM ==========================
# ==================================================

def generar_mensaje(p):
    textos = [
        (
            f"{p['titulo']}\n\n"
            f"Precio actual: {p['precio']} €\n"
            f"Más información:\n{p['url']}"
        ),
        (
            f"{p['titulo']}\n\n"
            f"Coste: {p['precio']} €\n"
            f"Enlace:\n{p['url']}"
        ),
        (
            f"{p['titulo']}\n\n"
            f"Disponible en Amazon:\n{p['url']}"
        )
    ]

    return random.choice(textos)


def enviar_texto_telegram(p):
    try:
        caption_texto = generar_mensaje(p)

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": caption_texto
            },
            timeout=30
        )

        log(f"Respuesta de Telegram texto - Status Code: {r.status_code}")
        log(f"Cuerpo de respuesta Telegram: {r.text}")

        if r.status_code == 200:
            registrar_envio()
            log(f"¡Enviado con éxito a Telegram como texto! ASIN: {p['asin']}")
            return True

        log(f"Telegram rechazó el mensaje de texto. Código: {r.status_code}")
        return False

    except Exception as e:
        log(f"Excepción crítica en enviar_texto_telegram: {e}")
        return False


def enviar_telegram(p):
    try:
        caption_texto = generar_mensaje(p)

        if not p.get("imagen"):
            log("Producto sin imagen. Enviando como texto.")
            return enviar_texto_telegram(p)

        log(
            f"Iniciando descarga de imagen para ASIN {p['asin']} "
            f"desde: {p['imagen']}"
        )

        img_resp = requests.get(p["imagen"], timeout=20)

        if img_resp.status_code != 200:
            log(
                f"Error al descargar imagen. "
                f"Código HTTP: {img_resp.status_code}. Enviando texto."
            )
            return enviar_texto_telegram(p)

        img = img_resp.content

        log(
            f"Imagen descargada con éxito. "
            f"Enviando mensaje a Telegram (Chat ID: {CHAT_ID})..."
        )

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "caption": caption_texto
            },
            files={
                "photo": ("img.jpg", img)
            },
            timeout=30
        )

        log(f"Respuesta de la API de Telegram - Status Code: {r.status_code}")
        log(f"Cuerpo de respuesta Telegram: {r.text}")

        if r.status_code == 200:
            registrar_envio()
            log(f"¡Enviado con éxito a Telegram! ASIN: {p['asin']}")
            return True

        log(f"Telegram rechazó la imagen. Código: {r.status_code}. Enviando texto.")
        return enviar_texto_telegram(p)

    except Exception as e:
        log(f"Excepción crítica en enviar_telegram: {e}")
        return False


# ==================================================
# ============== BUSQUEDA ==========================
# ==================================================

def construir_url_busqueda(palabra, page):
    return f"https://www.amazon.es/s?k={palabra}&page={page}"


def buscar_productos():
    palabra = random.choice(PALABRAS_CLAVE)

    log(f"Buscando productos usando la palabra clave: '{palabra}'")

    urls = set()

    for page in range(1, 21):
        url_busqueda = construir_url_busqueda(palabra, page)

        html = get_page_html(url_busqueda)

        if not html:
            log(f"No se pudo obtener HTML para la página {page} de la búsqueda.")
            continue

        soup = BeautifulSoup(html, "html.parser")
        encontrados_pagina = 0

        for a in soup.select("a.a-link-normal.s-no-outline, a[href*='/dp/']"):
            href = a.get("href", "")

            asin = extract_asin(href)

            if asin:
                urls.add(f"https://www.amazon.es/dp/{asin}")
                encontrados_pagina += 1

        log(f"Página {page}: encontrados {encontrados_pagina} enlaces de productos.")

    log(f"Total de URLs únicas recolectadas en esta búsqueda: {len(urls)}")

    urls_lista = list(urls)
    random.shuffle(urls_lista)

    return urls_lista


def obtener_producto(url, historial):
    asin = extract_asin(url)

    if not asin:
        return None

    if asin in historial:
        log(f"ASIN {asin} ya se encuentra en el historial. Saltando.")
        return None

    log(f"Analizando producto con ASIN: {asin}")

    html = get_page_html(url)

    if not html:
        log(f"No se pudo obtener el HTML del producto ASIN {asin}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    precio, precio_anterior, desc = extraer_precios(soup)

    log(
        f"ASIN {asin} -> Precio: {precio} € | "
        f"Precio anterior: {precio_anterior} € | "
        f"Descuento detectado: {desc}% "
        f"(Mínimo requerido: {CFG['min_descuento']}%)"
    )

    if not precio:
        log("Producto descartado (precio no detectado).")
        return None

    if desc < CFG["min_descuento"]:
        log("Producto descartado (descuento inferior al mínimo).")
        return None

    titulo = (
        soup.select_one("#productTitle")
        or soup.select_one("h1")
    )

    imagen = (
        soup.select_one("#landingImage")
        or soup.select_one("#imgTagWrapperId img")
    )

    imagen_src = None

    if imagen:
        imagen_src = imagen.get("src") or imagen.get("data-old-hires")

    historial.add(asin)
    guardar_json(HISTORIAL_FILE, list(historial))

    prod_data = {
        "asin": asin,
        "titulo": titulo.get_text(strip=True) if titulo else "Producto Amazon",
        "precio": precio,
        "precio_anterior": precio_anterior,
        "descuento": desc,
        "imagen": imagen_src,
        "url": crear_url(asin)
    }

    log(
        f"Producto válido encontrado: "
        f"{prod_data['titulo'][:40]}... "
        f"(ASIN: {asin})"
    )

    return prod_data


# ==================================================
# ================= MAIN ===========================
# ==================================================

def main():
    inicio = time.time()

    log("==================================================")
    log("Sistema iniciado")
    log(f"MODO={MODO}")
    log(f"Configuración activa: {CFG}")
    log("==================================================")

    if not horario_permitido():
        log("Fuera de horario permitido. Finalizando ejecución.")
        return

    historial = set(cargar_json(HISTORIAL_FILE, []))

    log(f"Historial cargado con {len(historial)} elementos.")

    if not evaluar_riesgo():
        log("Límite diario alcanzado. Finalizando ejecución.")
        return

    urls = buscar_productos()

    if not urls:
        log("No se encontraron URLs de productos en este ciclo.")
        log("Ejecución finalizada.")
        return

    enviado = False

    for url in urls:
        p = obtener_producto(url, historial)

        if p:
            enviado = enviar_telegram(p)

            if enviado:
                break

    if not enviado:
        log("No se encontró ningún producto válido para enviar en este ciclo.")

    duracion = time.time() - inicio

    log(f"Ejecución finalizada con éxito. Duración total: {duracion:.2f}s")


if __name__ == "__main__":
    main()


