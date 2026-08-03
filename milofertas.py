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
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# ==================================================
# =================== MODOS ========================
# ==================================================

MODO = "ULTRA"

MODOS_CONFIG = {
    "ULTRA": {
        "min_intervalo": 600,
        "max_intervalo": 600,
        "min_descuento": 15,
        "max_envios_dia": 144
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

HORA_INICIO = 0
HORA_FIN = 24

HISTORIAL_FILE = "enviados_historial.json"
ENVIO_DIARIO_FILE = "envios_diarios.json"
DEBUG_HTML = True
DEBUG_DIR = "debug_html"

# Telegram.
# Recomendado: configurar estos valores como Secrets en GitHub Actions.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "@Milofertazos")

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
            log(f"JSON cargado correctamente desde {path}")
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


def asegurar_debug_dir():
    if DEBUG_HTML and not os.path.exists(DEBUG_DIR):
        os.makedirs(DEBUG_DIR, exist_ok=True)


def guardar_debug_html(nombre, html):
    if not DEBUG_HTML or not html:
        return

    try:
        asegurar_debug_dir()
        safe_nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
        path = os.path.join(DEBUG_DIR, safe_nombre)

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        log(f"DEBUG: HTML guardado en {path}")
    except Exception as e:
        log(f"DEBUG: No se pudo guardar HTML {nombre}: {e}")


def diagnosticar_html(html, contexto=""):
    if not html:
        log(f"DEBUG {contexto}: HTML vacío o None")
        return

    html_lower = html.lower()

    log(f"DEBUG {contexto}: tamaño HTML = {len(html)} caracteres")

    checks = {
        "contiene /dp/": "/dp/" in html,
        "contiene captcha": "captcha" in html_lower,
        "contiene robot": "robot" in html_lower,
        "contiene automated access": "automated access" in html_lower,
        "contiene productTitle": "producttitle" in html_lower,
        "contiene a-price": "a-price" in html_lower,
        "contiene offerDisplay": "offerdisplay" in html_lower,
        "contiene no results": "no results" in html_lower or "no hay resultados" in html_lower,
        "contiene s-result-item": "s-result-item" in html_lower,
    }

    for nombre, valor in checks.items():
        log(f"DEBUG {contexto}: {nombre} = {valor}")

    preview = html[:500].replace("\n", " ").replace("\r", " ")
    log(f"DEBUG {contexto}: primeros 500 chars = {preview}")


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

    patrones = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"dp%2F([A-Z0-9]{10})",
    ]

    for patron in patrones:
        m = re.search(patron, url, re.IGNORECASE)
        if m:
            return m.group(1).upper()

    return None


def crear_url(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG_AFILIADO}"


def get_page_html(url):
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


def telegram_config_valida():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "7711722254:AAFAscovZ44PJpbYuJHKVgFevSNy-himSc4":
        log("ERROR: TELEGRAM_TOKEN no está configurado.")
        return False

    if not CHAT_ID:
        log("ERROR: CHAT_ID no está configurado.")
        return False

    return True


def enviar_texto_telegram(p):
    if not telegram_config_valida():
        return False

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
            log(f"Enviado con éxito a Telegram como texto. ASIN: {p['asin']}")
            return True

        log(f"Telegram rechazó el mensaje de texto. Código: {r.status_code}")
        return False

    except Exception as e:
        log(f"Excepción crítica en enviar_texto_telegram: {e}")
        return False


def enviar_telegram(p):
    if not telegram_config_valida():
        return False

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
            f"Enviando mensaje a Telegram. Chat ID: {CHAT_ID}"
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
            log(f"Enviado con éxito a Telegram. ASIN: {p['asin']}")
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
    palabra_codificada = quote_plus(palabra)
    return f"https://www.amazon.es/s?k={palabra_codificada}&page={page}"


def buscar_productos():
    palabra = random.choice(PALABRAS_CLAVE)

    log(f"Buscando productos usando la palabra clave: '{palabra}'")

    urls = set()

    for page in range(1, 4):
        url_busqueda = construir_url_busqueda(palabra, page)

        html = get_page_html(url_busqueda)

        if not html:
            log(f"No se pudo obtener HTML para la página {page} de la búsqueda.")
            continue

        diagnosticar_html(html, contexto=f"busqueda_pagina_{page}")
        guardar_debug_html(f"busqueda_{palabra}_pagina_{page}.html", html)

        soup = BeautifulSoup(html, "html.parser")

        total_dp_links = len(soup.select("a[href*='/dp/']"))
        total_result_items = len(soup.select("[data-component-type='s-search-result']"))
        total_s_no_outline = len(soup.select("a.a-link-normal.s-no-outline"))

        log(f"DEBUG página {page}: enlaces con /dp/ = {total_dp_links}")
        log(f"DEBUG página {page}: items s-search-result = {total_result_items}")
        log(f"DEBUG página {page}: enlaces s-no-outline = {total_s_no_outline}")

        asins_pagina = set()

        for a in soup.select("a[href*='/dp/'], a[href*='/gp/product/']"):
            href = a.get("href", "")
            asin = extract_asin(href)

            if asin:
                asins_pagina.add(asin)

        for asin in asins_pagina:
            urls.add(f"https://www.amazon.es/dp/{asin}")

        encontrados_pagina = len(asins_pagina)

        if encontrados_pagina == 0:
            log(f"DEBUG página {page}: no se extrajo ningún ASIN.")
        else:
            muestra_asins = list(asins_pagina)[:10]
            log(f"DEBUG página {page}: primeros ASIN encontrados: {muestra_asins}")

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

    diagnosticar_html(html, contexto=f"producto_{asin}")
    guardar_debug_html(f"producto_{asin}.html", html)

    soup = BeautifulSoup(html, "html.parser")

    precio, precio_anterior, desc = extraer_precios(soup)

    log(
        f"ASIN {asin} -> Precio: {precio} € | "
        f"Precio anterior: {precio_anterior} € | "
        f"Descuento detectado: {desc}% "
        f"(Mínimo requerido: {CFG['min_descuento']}%)"
    )

    if not precio:
        log("Producto descartado. Precio no detectado.")
        return None

    if desc < CFG["min_descuento"]:
        log("Producto descartado. Descuento inferior al mínimo.")
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
