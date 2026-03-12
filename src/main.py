import flet as ft
import flet_video as ftv
import json
import os
import urllib.request
import urllib.parse
import re
import time
import random
import base64
import threading
import queue
import asyncio
from datetime import datetime
import sys

# --- NUEVAS IMPORTACIONES PARA EL EXTRACTOR Y DESCARGAS ---
import requests
from bs4 import BeautifulSoup
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from Crypto.Cipher import AES

# Desactivar advertencias de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURACIÓN GENERAL ---
ITEMS_PER_PAGE = 24
POSTER_DIR = "posters"

# Carpeta de descargas
if sys.platform.startswith("linux") and "ANDROID_STORAGE" in os.environ:
    DOWNLOAD_DIR = "/sdcard/Download/"
else:
    DOWNLOAD_DIR = "downloads"
    
HISTORIAL_FILE = "historial_descargas.json"
LOG_FILE = "descargas_log.txt"
MAX_CONCURRENT_DOWNLOADS = 2

# Crear carpetas necesarias
if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
if not os.path.exists(POSTER_DIR): os.makedirs(POSTER_DIR)
if not os.path.exists(HISTORIAL_FILE):
    with open(HISTORIAL_FILE, 'w') as f:
        json.dump([], f)

# ==========================================
# CONFIGURACIÓN Y FUNCIONES DEL ACTUALIZADOR (SCRAPER)
# ==========================================

SCRAPER_BASE_URL = "https://pelisplushd.bz"
SCRAPER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
SCRAPER_SECRET_KEY = "Ak7qrvvH4WKYxV2OgaeHAEg2a5eh16vE"
update_session = requests.Session()
update_session.headers.update(SCRAPER_HEADERS)

SCRAPER_CATEGORIES = [
    {"name": "Películas", "file": "peliculas_con_reproductores.json", "url_template": SCRAPER_BASE_URL + "/peliculas?page={}", "type": "pelicula", "max_pages": 20},
    {"name": "Series", "file": "series.json", "url_template": SCRAPER_BASE_URL + "/series?page={}", "type": "serie", "max_pages": 10},
    {"name": "Animes", "file": "animes.json", "url_template": SCRAPER_BASE_URL + "/animes?page={}", "type": "anime", "max_pages": 10},
    {"name": "Doramas", "file": "doramas.json", "url_template": SCRAPER_BASE_URL + "/generos/dorama?page={}", "type": "dorama", "max_pages": 10}
]
SCRAPER_MAX_WORKERS = 3

def scraper_decrypt_link(encrypted_b64: str, secret_key: str) -> str:
    if encrypted_b64.startswith("eyJ") and "." in encrypted_b64:
        try:
            parts = encrypted_b64.split('.')
            if len(parts) == 3:
                payload_b64 = parts[1]
                padding = 4 - len(payload_b64) % 4
                if padding != 4: payload_b64 += '=' * padding
                decoded_bytes = base64.urlsafe_b64decode(payload_b64)
                decoded_str = decoded_bytes.decode('utf-8')
                data = json.loads(decoded_str)
                if 'link' in data: return data['link']
        except: pass
    try:
        data = base64.b64decode(encrypted_b64)
        iv, ciphertext = data[:16], data[16:]
        cipher = AES.new(secret_key.encode("utf-8"), AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(ciphertext)
        return decrypted[:-decrypted[-1]].decode("utf-8")
    except: return "Error: Descifrado fallido"

def scraper_extraer_dataLink(html: str):
    scripts = re.findall(r"(?:const|let|var)?\s*dataLink\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not scripts: return []
    try: data = json.loads(scripts[0])
    except: return []
    resultados = []
    for entry in data:
        idioma = entry.get("video_language")
        for embed in entry.get("sortedEmbeds", []):
            servidor = embed.get("servername")
            tipo = embed.get("type")
            url = scraper_decrypt_link(embed.get("link"), SCRAPER_SECRET_KEY)
            resultados.append({"idioma": idioma, "servidor": servidor, "tipo": tipo, "url": url})
    return resultados

def scraper_obtener_url_embed(html: str):
    match_js = re.search(r"video\[\d+\]\s*=\s*['\"](https?://[^'\"]+)['\"]", html)
    if match_js: return match_js.group(1)
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if iframe:
        src = iframe.get("src")
        return src if src and src.startswith("http") else urllib.parse.urljoin(SCRAPER_BASE_URL, src)
    return None

def scraper_extraer_detalles_generales(html: str):
    soup = BeautifulSoup(html, "html.parser")
    detalles = {}
    h1 = soup.select_one("h1.m-b-5")
    if h1:
        detalles["titulo"] = h1.get_text(strip=True)
        m = re.search(r'\((\d{4})\)', detalles["titulo"])
        if m: detalles["anio"] = m.group(1)
    poster_url = None
    poster_img = soup.select_one(".col-sm-3 img")
    if poster_img:
        poster_url = poster_img.get("data-src") or poster_img.get("src")
    if not poster_url:
        meta_img = soup.select_one("meta[property='og:image']")
        if meta_img: poster_url = meta_img.get("content")
    if poster_url:
        if not poster_url.startswith("http"): poster_url = urllib.parse.urljoin(SCRAPER_BASE_URL, poster_url)
        poster_url = poster_url.replace("/w154/", "/w1280/").replace("/w92/", "/w1280/")
        detalles["poster"] = poster_url
    else: detalles["poster"] = None
    sinopsis = soup.select_one(".text-large")
    if sinopsis: detalles["sinopsis"] = sinopsis.get_text(strip=True)
    generos = [l.get_text(strip=True) for l in soup.select("a") if "Genero" in l.get("title", "")]
    if generos: detalles["genero"] = generos
    return detalles

def scraper_extraer_estructura_series(html: str):
    soup = BeautifulSoup(html, "html.parser")
    temporadas = {}
    nav_items = soup.select(".TbVideoNv .nav-link")
    for tab in nav_items:
        raw_href = tab.get("href", "")
        target_id = raw_href.strip()
        if not target_id.startswith("#"): continue
        texto_temp = tab.get_text(strip=True)
        match = re.search(r'(\d+)', texto_temp)
        num_temp = match.group(1) if match else "1"
        content_div = soup.select_one(target_id)
        if content_div:
            episodios = [{"titulo": l.get_text(strip=True), "url": urllib.parse.urljoin(SCRAPER_BASE_URL, l.get("href"))} for l in content_div.select("a.btn-primary") if l.get("href")]
            if episodios: temporadas[num_temp] = episodios
    return temporadas

def scraper_procesar_pelicula(url):
    try:
        r = update_session.get(url, verify=False, timeout=15)
        r.raise_for_status()
        data = scraper_extraer_detalles_generales(r.text)
        data["url"] = url
        data["tipo"] = "pelicula"
        embed_url = scraper_obtener_url_embed(r.text)
        data["reproductores"] = []
        if embed_url:
            try:
                r_emb = update_session.get(embed_url, verify=False, timeout=15)
                data["reproductores"] = scraper_extraer_dataLink(r_emb.text)
            except: pass
        return data
    except Exception as e:
        return {"url": url, "tipo": "pelicula", "titulo": f"ERROR: {e}", "reproductores": []}

def scraper_procesar_serie_o_dorama(url, tipo_str):
    try:
        r = update_session.get(url, verify=False, timeout=15)
        r.raise_for_status()
        data = scraper_extraer_detalles_generales(r.text)
        data["url"] = url
        data["tipo"] = tipo_str
        mapa_temp = scraper_extraer_estructura_series(r.text)
        data["temporadas"] = {}
        for num_temp, episodios in mapa_temp.items():
            lista_procesada = []
            for ep in episodios:
                try:
                    r_ep = update_session.get(ep['url'], verify=False, timeout=15)
                    embed_url = scraper_obtener_url_embed(r_ep.text)
                    reps = []
                    if embed_url:
                        try:
                            r_emb = update_session.get(embed_url, verify=False, timeout=15)
                            reps = scraper_extraer_dataLink(r_emb.text)
                        except: pass
                    lista_procesada.append({"titulo": ep["titulo"], "url": ep["url"], "reproductores": reps})
                    time.sleep(0.3)
                except: 
                    lista_procesada.append({"titulo": ep["titulo"], "url": ep["url"], "reproductores": []})
            data["temporadas"][num_temp] = lista_procesada
        return data
    except Exception as e:
        return {"url": url, "tipo": tipo_str, "titulo": f"ERROR: {e}", "temporadas": {}}

def scraper_procesar_anime(url):
    try:
        r = update_session.get(url, verify=False, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        es_serie = bool(soup.select_one(".TbVideoNv .nav-link"))
        data = scraper_extraer_detalles_generales(r.text)
        data["url"] = url
        if es_serie:
            data["tipo"] = "anime_serie"
            mapa_temp = scraper_extraer_estructura_series(r.text)
            data["temporadas"] = {}
            for num_temp, episodios in mapa_temp.items():
                lista_procesada = []
                for ep in episodios:
                    try:
                        r_ep = update_session.get(ep['url'], verify=False, timeout=15)
                        embed_url = scraper_obtener_url_embed(r_ep.text)
                        reps = []
                        if embed_url:
                            try:
                                r_emb = update_session.get(embed_url, verify=False, timeout=15)
                                reps = scraper_extraer_dataLink(r_emb.text)
                            except: pass
                        lista_procesada.append({"titulo": ep["titulo"], "url": ep["url"], "reproductores": reps})
                        time.sleep(0.3)
                    except: lista_procesada.append({"titulo": ep["titulo"], "url": ep["url"], "reproductores": []})
                data["temporadas"][num_temp] = lista_procesada
        else:
            data["tipo"] = "anime_pelicula"
            embed_url = scraper_obtener_url_embed(r.text)
            data["reproductores"] = []
            if embed_url:
                try:
                    r_emb = update_session.get(embed_url, verify=False, timeout=15)
                    data["reproductores"] = scraper_extraer_dataLink(r_emb.text)
                except: pass
        return data
    except Exception as e:
        return {"url": url, "tipo": "anime", "titulo": f"ERROR: {e}"}

def scraper_cargar_json(filepath):
    if not os.path.exists(filepath): return [], set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        urls = {item["url"] for item in data}
        return data, urls
    except: return [], set()

def scraper_guardar_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def scraper_obtener_urls_pagina(url_template, page):
    try:
        r = update_session.get(url_template.format(page), verify=False, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        return [urllib.parse.urljoin(SCRAPER_BASE_URL, a['href']) for a in soup.select("a.Posters-link")]
    except: return []

def scraper_actualizar_categoria(cat_config, log_callback=None):
    def log(msg): 
        if log_callback: log_callback(msg)
        else: print(msg)

    nombre = cat_config['name']
    archivo = cat_config['file']
    url_tmpl = cat_config['url_template']
    tipo = cat_config['type']
    max_p = cat_config['max_pages']
    
    log(f"\n{'='*10} ACTUALIZANDO {nombre.upper()} {'='*10}")
    
    datos_existentes, urls_existentes = scraper_cargar_json(archivo)
    log(f"📁 Cargados {len(datos_existentes)} registros existentes.")
    
    nuevos_items = []
    detener = False
    
    for pagina in range(1, max_p + 1):
        if detener: break
        
        log(f"🔍 Verificando página {pagina}...")
        urls_pagina = scraper_obtener_urls_pagina(url_tmpl, pagina)
        
        if not urls_pagina:
            log("  ⚠️ Página vacía o error. Siguiente...")
            continue
            
        urls_nuevas = []
        todas_existentes = True
        
        for url in urls_pagina:
            if url in urls_existentes:
                continue
            else:
                todas_existentes = False
                urls_nuevas.append(url)
        
        if todas_existentes:
            log(f"  🛑 Página {pagina} completa. Base de datos al día.")
            detener = True
            continue
        
        if urls_nuevas:
            log(f"  ⚡ Procesando {len(urls_nuevas)} nuevos items...")
            
            if tipo == "pelicula":
                worker_func = scraper_procesar_pelicula
            elif tipo == "anime":
                worker_func = scraper_procesar_anime
            else: 
                worker_func = lambda u: scraper_procesar_serie_o_dorama(u, tipo)
            
            with ThreadPoolExecutor(max_workers=SCRAPER_MAX_WORKERS) as executor:
                future_to_url = {executor.submit(worker_func, u): u for u in urls_nuevas}
                for future in as_completed(future_to_url):
                    u = future_to_url[future]
                    try:
                        item = future.result()
                        nuevos_items.append(item)
                        urls_existentes.add(u) 
                        log(f"    ✅ Nuevo: {item.get('titulo', u)[:50]}...")
                    except Exception as e:
                        log(f"    ❌ Error procesando {u}: {e}")
        
        time.sleep(0.5)
    
    if nuevos_items:
        lista_final = nuevos_items + datos_existentes
        scraper_guardar_json(archivo, lista_final)
        log(f"💾 Guardado: {len(nuevos_items)} items nuevos agregados a {archivo}")
    else:
        log("ℹ️ No hay novedades en esta categoría.")

def run_full_updater(log_callback):
    start_time = time.time()
    log_callback("🚀 INICIANDO ACTUALIZACIÓN COMPLETA DE BASE DE DATOS...")
    
    for cat in SCRAPER_CATEGORIES:
        scraper_actualizar_categoria(cat, log_callback)
    
    elapsed = time.time() - start_time
    log_callback(f"\n🏁 ¡TODO ACTUALIZADO! Tiempo total: {elapsed:.2f} segundos.")


# ==========================================
# LÓGICA DE EXTRACCIÓN DE ENLACES (VOE - ROBUSTA)
# ==========================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_browser_headers(url=None):
    parsed_url = urllib.parse.urlparse(url) if url else None
    referer = f"{parsed_url.scheme}://{parsed_url.netloc}/" if parsed_url else ""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none" if not referer else "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
        "Priority": "u=1"
    }
    if referer: headers["Referer"] = referer
    return headers

def _rot13(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90: out.append(chr(((o - 65 + 13) % 26) + 65))
        elif 97 <= o <= 122: out.append(chr(((o - 97 + 13) % 26) + 97))
        else: out.append(ch)
    return ''.join(out)

def _replace_patterns(txt: str) -> str:
    for pat in ['@$', '^^', '~@', '%?', '*~', '!!', '#&']: txt = txt.replace(pat, '')
    return txt

def _shift_chars(text: str, shift: int) -> str:
    return ''.join(chr(ord(c) - shift) for c in text)

def _safe_b64_decode(s: str) -> str:
    pad = len(s) % 4
    if pad: s += '=' * (4 - pad)
    return base64.b64decode(s).decode('utf-8', errors='replace')

def deobfuscate_embedded_json(raw_json: str):
    try:
        arr = json.loads(raw_json)
        if not (isinstance(arr, list) and arr and isinstance(arr[0], str)): return None
        obf = arr[0]
    except json.JSONDecodeError: return None
    try:
        step1 = _rot13(obf)
        step2 = _replace_patterns(step1)
        step3 = _safe_b64_decode(step2)
        step4 = _shift_chars(step3, 3)
        step5 = step4[::-1]
        step6 = _safe_b64_decode(step5)
        try: return json.loads(step6)
        except json.JSONDecodeError: return step6
    except Exception: return None

def is_bait_source(source: str) -> bool:
    bait_filenames = ["BigBuckBunny", "Big_Buck_Bunny_1080_10s_5MB", "bbb.mp4"]
    bait_domains = ["test-videos.co.uk", "sample-videos.com", "commondatastorage.googleapis.com"]
    if any(fn.lower() in source.lower() for fn in bait_filenames): return True
    parsed = urllib.parse.urlparse(source)
    if any(dom in parsed.netloc for dom in bait_domains): return True
    return False

def clean_base64(s):
    try:
        s = s.replace('\\', '')
        missing_padding = len(s) % 4
        if missing_padding: s += '=' * (4 - missing_padding)
        base64.b64decode(s, validate=True)
        return s
    except (base64.binascii.Error, ValueError): return None

def extract_link_voe(URL):
    """Extract direct link from VOE video URL without downloading (Robust Version)"""
    URL = str(URL)
    time.sleep(random.uniform(1, 3))
    
    session = requests.Session()
    headers = get_browser_headers(URL)
    try:
        html_page = session.get(URL, headers=headers, timeout=30, verify=False)
        html_page.raise_for_status()
        
        if html_page.status_code == 403 or "captcha" in html_page.text.lower():
            print(f"[!] Access denied or captcha detected for {URL}. Trying with different headers...")
            time.sleep(random.uniform(3, 5))
            headers = get_browser_headers(URL)
            headers["User-Agent"] = random.choice(USER_AGENTS)
            html_page = session.get(URL, headers=headers, timeout=30, verify=False)
            html_page.raise_for_status()
        
        soup = BeautifulSoup(html_page.content, 'html.parser')
        
        redirect_patterns = [
            "window.location.href = '", "window.location = '", "location.href = '",
            "window.location.replace('", "window.location.assign('", "window.location=\"", "window.location.href=\""
        ]
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                for pattern in redirect_patterns:
                    if pattern in script.string:
                        L = len(pattern)
                        i0 = script.string.find(pattern)
                        closing_quote = "'" if pattern.endswith("'") else "\""
                        i1 = script.string.find(closing_quote, i0 + L)
                        if i1 > i0:
                            url = script.string[i0 + L:i1]
                            print(f"[*] Detected redirect to: {url}")
                            return extract_link_voe(url)
        
        source_json = None
        
        sources_find = soup.find_all(string=re.compile("var sources"))
        if sources_find:
            sources_find = str(sources_find)
            try:
                slice_start = sources_find.index("var sources")
                source = sources_find[slice_start:]
                slice_end = source.index(";")
                source = source[:slice_end]
                source = source.replace("var sources = ", "").replace("\'", "\"").replace("\\n", "").replace("\\", "")
                if not is_bait_source(source):
                    strToReplace = ","
                    replacementStr = ""
                    source = replacementStr.join(source.rsplit(strToReplace, 1))
                    source_json = json.loads(source)
                    print("[+] Found sources using var sources pattern")
            except (ValueError, json.JSONDecodeError): pass
        
        if not source_json:
            scripts = soup.find_all("script")
            for script in scripts:
                if not script.string: continue
                patterns = ["var sources", "sources =", "sources:", "\"sources\":", "'sources':"]
                for pattern in patterns:
                    if pattern in script.string:
                        try:
                            script_text = script.string
                            start_idx = script_text.find(pattern)
                            if start_idx == -1: continue
                            brace_idx = script_text.find("{", start_idx)
                            if brace_idx == -1: continue
                            brace_count = 1
                            end_idx = brace_idx + 1
                            while brace_count > 0 and end_idx < len(script_text):
                                if script_text[end_idx] == "{": brace_count += 1
                                elif script_text[end_idx] == "}": brace_count -= 1
                                end_idx += 1
                            if brace_count == 0:
                                json_str = script_text[brace_idx:end_idx].replace("'", "\"")
                                source_json = json.loads(json_str)
                                print(f"[+] Found sources using pattern: {pattern}")
                                break
                        except Exception: pass
                        if source_json: break
        
        if not source_json:
            video_tags = soup.find_all("video")
            for video in video_tags:
                src = video.get("src")
                if src and not is_bait_source(src):
                    source_json = {"mp4": src}
                    break
                source_tags = video.find_all("source")
                for source_tag in source_tags:
                    src = source_tag.get("src")
                    if src and not is_bait_source(src):
                        type_attr = source_tag.get("type", "")
                        if "mp4" in type_attr: source_json = {"mp4": src}
                        elif "m3u8" in type_attr or "hls" in type_attr: source_json = {"hls": src}
                        else: source_json = {"mp4": src}
                        print(f"[+] Found video source from source tag: {src}")
                        break
                if source_json: break
        
        if not source_json:
            m3u8_pattern = r'(https?://[^"\']+\.m3u8[^"\'\s]*)'
            m3u8_matches = re.findall(m3u8_pattern, html_page.text)
            if m3u8_matches and not is_bait_source(m3u8_matches[0]):
                source_json = {"hls": m3u8_matches[0]}
            if not source_json:
                mp4_pattern = r'(https?://[^"\']+\.mp4[^"\'\s]*)'
                mp4_matches = re.findall(mp4_pattern, html_page.text)
                if mp4_matches and not is_bait_source(mp4_matches[0]):
                    source_json = {"mp4": mp4_matches[0]}
        
        if not source_json:
            base64_pattern = r'base64[,:]([A-Za-z0-9+/=]+)'
            base64_matches = re.findall(base64_pattern, html_page.text)
            for match in base64_matches:
                try:
                    decoded = base64.b64decode(match).decode('utf-8')
                    if '.mp4' in decoded: source_json = {"mp4": decoded}; break
                    elif '.m3u8' in decoded: source_json = {"hls": decoded}; break
                except: continue
        
        if not source_json:
            a168c_script_pattern = r"a168c\s*=\s*'([^']+)'"
            match = re.search(a168c_script_pattern, html_page.text, re.DOTALL)
            if match:
                raw_base64 = match.group(1)
                try:
                    cleaned = clean_base64(raw_base64)
                    if cleaned:
                        decoded = base64.b64decode(cleaned).decode('utf-8')[::-1]
                        try:
                            parsed = json.loads(decoded)
                            if 'direct_access_url' in parsed: source_json = {"mp4": parsed['direct_access_url']}
                            elif 'source' in parsed: source_json = {"hls": parsed['source']}
                        except json.JSONDecodeError:
                            mp4_match = re.search(r'(https?://[^\s"]+\.mp4[^\s"]*)', decoded)
                            m3u8_match = re.search(r'(https?://[^\s"]+\.m3u8[^\s"]*)', decoded)
                            if mp4_match: source_json = {"mp4": mp4_match.group(1)}
                            elif m3u8_match: source_json = {"hls": m3u8_match.group(1)}
                except: pass

        if not source_json:
            MKGMa_pattern = r'MKGMa="(.*?)"'
            match = re.search(MKGMa_pattern, html_page.text, re.DOTALL)
            if match:
                raw_MKGMa = match.group(1)
                def rot13_decode(s: str) -> str:
                    return ''.join(chr((ord(c) - ord('A') + 13) % 26 + ord('A')) if 'A' <= c <= 'Z' else 
                                   chr((ord(c) - ord('a') + 13) % 26 + ord('a')) if 'a' <= c <= 'z' else c for c in s)
                def shift_characters(s: str, offset: int) -> str:
                    return ''.join(chr(ord(c) - offset) for c in s)
                try:
                    step1 = rot13_decode(raw_MKGMa).replace('_', '')
                    step3 = base64.b64decode(step1).decode('utf-8')
                    step4 = shift_characters(step3, 3)
                    decoded = base64.b64decode(step4[::-1]).decode('utf-8')
                    parsed_json = json.loads(decoded)
                    if 'direct_access_url' in parsed_json: source_json = {"mp4": parsed_json['direct_access_url']}
                    elif 'source' in parsed_json: source_json = {"hls": parsed_json['source']}
                except: pass

        if not source_json:
            app_json_scripts = soup.find_all("script", attrs={"type": "application/json"})
            for js in app_json_scripts:
                if not js.string: continue
                result = deobfuscate_embedded_json(js.string.strip())
                if result:
                    try:
                        if isinstance(result, dict):
                            if 'direct_access_url' in result: source_json = {"mp4": result['direct_access_url']}
                            elif 'source' in result: source_json = {"hls": result['source']}
                            elif any(k in result for k in ("mp4", "hls")): source_json = result
                        elif isinstance(result, str):
                            mp4_m = re.search(r'(https?://[^\s"]+\.mp4[^\s"]*)', result)
                            m3u8_m = re.search(r'(https?://[^\s"]+\.m3u8[^\s"]*)', result)
                            if mp4_m: source_json = {"mp4": mp4_m.group(0)}
                            elif m3u8_m: source_json = {"hls": m3u8_m.group(0)}
                    except: pass
                    if source_json: break

        if not source_json:
            iframes = soup.find_all("iframe")
            for iframe in iframes:
                iframe_src = iframe.get("src")
                if iframe_src:
                    if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
                    elif not iframe_src.startswith(("http://", "https://")):
                        parsed_url = urllib.parse.urlparse(URL)
                        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                        iframe_src = base_url + iframe_src if iframe_src.startswith("/") else base_url + "/" + iframe_src
                    print(f"[*] Found iframe, following to: {iframe_src}")
                    return extract_link_voe(iframe_src)

        if not source_json: return None

        if isinstance(source_json, str): source_json = {"mp4": source_json}
        if not isinstance(source_json, dict): return None

        if "mp4" in source_json:
            link = source_json["mp4"]
            if isinstance(link, str) and (link.startswith("eyJ") or re.match(r'^[A-Za-z0-9+/=]+$', link)):
                try: link = base64.b64decode(link).decode("utf-8")
                except: pass
            if link.startswith("//"): link = "https:" + link
            return link
        elif "hls" in source_json:
            link = source_json["hls"]
            if isinstance(link, str) and (link.startswith("eyJ") or re.match(r'^[A-Za-z0-9+/=]+$', link)):
                try: link = base64.b64decode(link).decode("utf-8")
                except: pass
            if link.startswith("//"): link = "https:" + link
            return link
        return None
    except Exception as e:
        print(f"[!] Unexpected error: {e}")
        return None

# ==========================================
# SISTEMA DE DESCARGAS
# ==========================================

class VideoDownloader:
    def __init__(self, url, filename, progress_callback, log_callback, finished_callback):
        self.url = url
        self.filename = filename
        self.temp_filename = filename + ".tmp"
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.finished_callback = finished_callback
        self._cancel = False
        self.downloaded = 0
        self.total_size = 0

    def cancel(self):
        self._cancel = True
        self.log_callback(f"❌ Cancelando: {self.filename}")

    def run(self):
        try:
            self.log_callback(f"🟡 Iniciando: {self.filename}")
            
            # Streaming download con requests
            headers = {'User-Agent': random.choice(USER_AGENTS)}
            with requests.get(self.url, headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                self.total_size = int(r.headers.get('content-length', 0))
                
                with open(self.temp_filename, 'wb') as f:
                    start_time = time.time()
                    for chunk in r.iter_content(chunk_size=8192):
                        if self._cancel:
                            raise Exception("Cancelado por usuario")
                        if chunk:
                            f.write(chunk)
                            self.downloaded += len(chunk)
                            
                            # Cálculo de progreso y velocidad
                            percent = (self.downloaded / self.total_size) if self.total_size > 0 else 0
                            elapsed = time.time() - start_time
                            speed = self.downloaded / (1024 * 1024) / elapsed if elapsed > 0 else 0
                            
                            status = f"{self.downloaded/1024/1024:.1f}MB / {self.total_size/1024/1024:.1f}MB @ {speed:.2f}MB/s"
                            self.progress_callback(percent, status)
                
                # Renombrar archivo temporal
                if os.path.exists(self.temp_filename):
                    if os.path.exists(self.filename):
                        os.remove(self.filename)
                    os.rename(self.temp_filename, self.filename)
                    self.log_callback(f"✅ Completado: {self.filename}")
                else:
                    raise Exception("Archivo no encontrado")

        except Exception as e:
            if self._cancel:
                self.log_callback(f"❌ Cancelado: {self.filename}")
            else:
                self.log_callback(f"❌ Error {self.filename}: {e}")
            # Limpiar temp si existe
            if os.path.exists(self.temp_filename):
                try: os.remove(self.temp_filename)
                except: pass
        finally:
            self.finished_callback(self)

class DownloadManager:
    def __init__(self, page, log_callback):
        self.page = page
        self.queue = queue.Queue()
        self.active_downloads = {} 
        self.log_callback = log_callback
        self.lock = threading.Lock()

    def add_to_queue(self, custom_filename, url):
        filename = os.path.join(DOWNLOAD_DIR, f"{custom_filename}.mp4")
        
        # --- DISEÑO UI (CARDS) ---
        card_bg = "#1E1E1E" 
        progress_fill_color = "#00E676" 
        progress_bg_color = "#2C2C2C" 
        
        progress_track = ft.Container(width=300, height=8, bgcolor=progress_bg_color, border_radius=4)
        progress_fill = ft.Container(width=0, height=8, bgcolor=progress_fill_color, border_radius=4)
        progress_stack = ft.Stack([progress_track, progress_fill], width=300, height=8)
        
        status_text = ft.Text("En cola...", size=11, color="#B0B0B0")
        
        card_content = ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.DOWNLOAD, color="#4CAF50", size=30),
                ft.Column([
                    ft.Text(custom_filename, color="white", size=14, weight="bold", max_lines=1, overflow="ellipsis", width=220),
                    status_text
                ], spacing=2, expand=True),
                ft.IconButton(ft.Icons.CANCEL, icon_color="#FF5252", icon_size=20, tooltip="Cancelar", on_click=lambda e: self.cancel_download(custom_filename))
            ], alignment="spaceBetween", expand=True),
            ft.Container(height=5), 
            progress_stack
        ], spacing=0)
        
        card = ft.Container(
            content=card_content,
            bgcolor=card_bg,
            padding=12,
            border_radius=12,
        )
        
        with self.lock:
            self.active_downloads[custom_filename] = {
                'card': card, 
                'fill': progress_fill, 
                'status': status_text
            }
        
        self.queue.put((custom_filename, url, filename, custom_filename))
        self.log_callback(f"🔵 En cola: {custom_filename}")
        self.try_start_next()

    def try_start_next(self):
        with self.lock:
            while len([k for k,v in self.active_downloads.items() if 'thread' in v]) < MAX_CONCURRENT_DOWNLOADS and not self.queue.empty():
                task = self.queue.get()
                base_name, url, filename, unique_id = task 
                
                downloader = VideoDownloader(
                    url, filename, 
                    progress_callback=lambda p, t, n=unique_id: self.update_progress(n, p, t),
                    log_callback=self.log_callback,
                    finished_callback=self.on_finished
                )
                
                thread = threading.Thread(target=downloader.run, daemon=True)
                self.active_downloads[unique_id]['thread'] = thread
                self.active_downloads[unique_id]['downloader'] = downloader
                
                self.active_downloads[unique_id]['status'].value = "Conectando..."
                self.page.update()
                
                thread.start()

    def update_progress(self, unique_id, percent, text):
        with self.lock:
            if unique_id in self.active_downloads:
                self.active_downloads[unique_id]['status'].value = f"{text} ({int(percent*100)}%)"
                self.active_downloads[unique_id]['fill'].width = int(300 * percent)

    def on_finished(self, downloader):
        full_path = downloader.filename
        base_name = os.path.splitext(os.path.basename(full_path))[0]
        
        entry = {
            "titulo": base_name, 
            "fecha": datetime.now().isoformat(),
            "ruta": downloader.filename
        }
        try:
            with open(HISTORIAL_FILE, 'r+', encoding='utf-8') as f:
                try: data = json.load(f)
                except: data = []
                data.append(entry)
                f.seek(0)
                json.dump(data, f, indent=2)
        except: pass

        with self.lock:
            if base_name in self.active_downloads:
                del self.active_downloads[base_name]
        
        self.try_start_next()
        
    def cancel_download(self, unique_id):
        with self.lock:
            if unique_id in self.active_downloads:
                if 'downloader' in self.active_downloads[unique_id]:
                    self.active_downloads[unique_id]['downloader'].cancel()
                elif 'thread' not in self.active_downloads[unique_id]:
                     pass

    def get_active_ui_list(self):
        controls = []
        with self.lock:
            for k, v in self.active_downloads.items():
                controls.append(v['card'])
        return controls

# ==========================================
# APLICACIÓN FLET (MEJORADA CON SERIES, ANIMES Y DORAMAS)
# ==========================================

class MovieApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.download_manager = DownloadManager(page, self.log_message)
        
        # --- HEARTBEAT ---
        self.page.run_task(self.heartbeat_loop)

        # --- ESTADO DE LA APP ---
        self.page_num = 1
        self.total_pages = 1
        self.current_filter = "Todas"
        self.search_text = ""
        self.filter_chips = []
        
        # 0 = Películas, 1 = Series, 2 = Animes, 3 = Doramas, 4 = Update (Virtual)
        self.current_tab_index = 0 
        
        self.movies = []
        self.series = []
        self.animes = []
        self.doramas = [] 

        # --- CARGA DE DATOS ---
        self.reload_all_data()

        # --- CONFIGURACIÓN DE PÁGINA ---
        self.page.scroll = ft.ScrollMode.AUTO
        self.page.theme = ft.Theme(
            scrollbar_theme=ft.ScrollbarTheme(
                thumb_color=ft.Colors.WHITE, track_color="#1a1a1a",
                thickness=15, radius=10, cross_axis_margin=2
            )
        )
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = "#000000"
        self.page.title = "FletStream Pro"
        self.page.padding = 0
        self.page.window.maximized = True 

        # --- UI COMPONENTS ---
        self.movies_grid = ft.Row(wrap=True, spacing=10, run_spacing=10, expand=True)
        self.grid_container = ft.Container(content=self.movies_grid, alignment="center", expand=True)
        self.pagination_controls = ft.Row(spacing=10, alignment="center")
        
        self.log_box = ft.TextField(label="Log de Descargas", multiline=True, read_only=True, height=150, text_size=10, bgcolor="#141414", color="white")

        # --- CONFIGURACIÓN DEL DRAWER (NAVEGACIÓN LATERAL) ---
        self.page.drawer = ft.NavigationDrawer(
            bgcolor="#141414",
            selected_index=0,
            on_change=self.handle_navigation_change,
            controls=[
                ft.Container(height=20),
                ft.NavigationDrawerDestination(
                    label="Películas",
                    icon=ft.Icons.MOVIE_FILTER,
                    selected_icon=ft.Icons.MOVIE_FILTER
                ),
                ft.Divider(height=1, color="#333"),
                ft.NavigationDrawerDestination(
                    label="Series",
                    icon=ft.Icons.TV,
                    selected_icon=ft.Icons.TV
                ),
                ft.Divider(height=1, color="#333"),
                ft.NavigationDrawerDestination(
                    label="Animes",
                    icon=ft.Icons.ANIMATION,
                    selected_icon=ft.Icons.ANIMATION
                ),
                ft.Divider(height=1, color="#333"),
                ft.NavigationDrawerDestination(
                    label="Doramas",
                    icon=ft.Icons.THEATER_COMEDY,
                    selected_icon=ft.Icons.THEATER_COMEDY
                ),
                # ============================================================
                # BOTÓN ACTUALIZAR
                # ============================================================
                ft.Divider(height=1, color="#333"),
                ft.NavigationDrawerDestination(
                    label="Actualizar DB",
                    icon=ft.Icons.REFRESH,
                    selected_icon=ft.Icons.REFRESH
                ),
                
                # ============================================================
                # DEDICATORIA Y CRÉDITOS
                # ============================================================
                ft.Divider(height=1, color="#333"),
                ft.Container(height=30), 
                
                ft.Row(
                    [
                ft.Text(
                    "Dedicado  a", 
                    size=11, 
                    text_align=ft.TextAlign.CENTER
                ),
                ft.Text("⭐", color=ft.Colors.YELLOW, size=15),
                ft.Text(
                    "Fernan", 
                    color=ft.Colors.AMBER, 
                    size=14, 
                    italic=True,
                    text_align=ft.TextAlign.CENTER
                ),
                ft.Text("⭐", color=ft.Colors.YELLOW, size=15),
                ft.Text("❤️", color=ft.Colors.RED, size=12)

                   ],
                    alignment="center"
                    ),
                
                ft.Container(height=5),
                
                ft.Text(
                    "Ing. YaeCk", 
                    color="#E50914", 
                    size=13, 
                    weight=ft.FontWeight.BOLD,
                    text_align=ft.TextAlign.CENTER
                ),
                
                ft.Container(height=20) 
            ]
        )
        
        self.show_home()

    def reload_all_data(self):
        """Recarga los archivos JSON en memoria"""
        file_movies = "peliculas_con_reproductores.json"
        if os.path.exists(file_movies):
            try:
                with open(file_movies, 'r', encoding='utf-8') as f: self.movies = json.load(f)
                print(f"[*] Películas cargadas: {len(self.movies)}")
            except Exception as e: print(f"[!] Error JSON Pelis: {e}")
            
        file_series = "series.json"
        if os.path.exists(file_series):
            try:
                with open(file_series, 'r', encoding='utf-8') as f: self.series = json.load(f)
                print(f"[*] Series cargadas: {len(self.series)}")
            except Exception as e: print(f"[!] Error JSON Series: {e}")
        else:
            print("[!] No se encontró series.json")

        file_animes = "animes.json"
        if os.path.exists(file_animes):
            try:
                with open(file_animes, 'r', encoding='utf-8') as f: self.animes = json.load(f)
                print(f"[*] Animes cargados: {len(self.animes)}")
            except Exception as e: print(f"[!] Error JSON Animes: {e}")
        else:
            print("[!] No se encontró animes.json")
            
        file_doramas = "doramas.json"
        if os.path.exists(file_doramas):
            try:
                with open(file_doramas, 'r', encoding='utf-8') as f: self.doramas = json.load(f)
                print(f"[*] Doramas cargados: {len(self.doramas)}")
            except Exception as e: print(f"[!] Error JSON Doramas: {e}")
        else:
            print("[!] No se encontró doramas.json")

    async def open_drawer(self, e):
        await self.page.show_drawer()

    async def handle_navigation_change(self, e):
        """Maneja el cambio entre Películas, Series, Animes, Doramas y Actualizar"""
        idx = e.control.selected_index
        
        # Si es el botón de Actualizar (Índice 4)
        if idx == 4:
            await self.page.close_drawer()
            self.start_update_process()
            return

        self.current_tab_index = idx
        self.page_num = 1
        self.search_text = ""
        self.current_filter = "Todas"
        if hasattr(self, 'search_field'): self.search_field.value = ""
        
        await self.page.close_drawer()
        self.show_home()

    # ==========================================
    # LÓGICA DE ACTUALIZACIÓN
    # ==========================================
    def start_update_process(self):
        self.page.clean()
        
        # UI de Actualización
        self.update_logs = ft.ListView(expand=True, spacing=2, padding=10, auto_scroll=True)
        
        content = ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.ProgressRing(color="#E50914", width=30, height=30),
                    ft.Text("Actualizando Base de Datos...", size=20, weight="bold", color="white"),
                ], alignment="center"),
                padding=20, bgcolor="#141414"
            ),
            ft.Divider(height=1, color="#333"),
            ft.Container(content=self.update_logs, expand=True, bgcolor="#000000", border=ft.border.all(1, "#333"))
        ], expand=True)
        
        self.page.add(ft.SafeArea(content))
        self.page.update()
        
        # Iniciar proceso en hilo separado
        threading.Thread(target=self._run_updater_thread, daemon=True).start()

    def _run_updater_thread(self):
        # Wrapper para llamar al actualizador y pasar logs a la UI
        def log_to_ui(msg):
            self.update_logs.controls.append(ft.Text(msg, color="white", size=12, font_family="Consolas"))
            try:
                self.update_logs.update()
            except: pass # Si la página se cierra durante la actualización

        try:
            run_full_updater(log_to_ui)
            
            # Mensaje final en UI
            self.update_logs.controls.append(ft.Text("✅ ACTUALIZACIÓN COMPLETADA. RECARGANDO INTERFAZ...", color="#00E676", size=14, weight="bold"))
            self.update_logs.update()
            
            time.sleep(2) # Pausa para que el usuario vea el mensaje final
            
            # Recargar datos en memoria
            self.reload_all_data()
            
            # Volver al home (en el hilo principal de Flet se prefiere llamar update, pero show_home limpia y redibuja)
            self.show_home()
            
        except Exception as e:
            self.update_logs.controls.append(ft.Text(f"❌ ERROR CRÍTICO: {str(e)}", color="red", size=14, weight="bold"))
            self.update_logs.update()

    def get_current_data(self):
        """Devuelve la lista activa (movies, series, animes o doramas)"""
        if self.current_tab_index == 0:
            return self.movies
        elif self.current_tab_index == 1:
            return self.series
        elif self.current_tab_index == 2:
            return self.animes
        elif self.current_tab_index == 3:
            return self.doramas
        return []

    def get_current_type_name(self):
        if self.current_tab_index == 0:
            return "Películas"
        elif self.current_tab_index == 1:
            return "Series"
        elif self.current_tab_index == 2:
            return "Animes"
        elif self.current_tab_index == 3:
            return "Doramas"
        return ""

    async def heartbeat_loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                self.page.update()
            except Exception:
                break

    def log_message(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = f"[{timestamp}] {msg}"
        self.log_box.value += text + "\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def _download_and_replace(self, url, final_path, container_widget):
        temp_path = final_path + ".tmp"
        try:
            opener = urllib.request.build_opener()
            opener.addheaders = [('User-agent', 'Mozilla/5.0')]
            urllib.request.install_opener(opener)
            urllib.request.urlretrieve(url, temp_path)
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                if os.path.exists(final_path): os.remove(final_path)
                os.rename(temp_path, final_path)
                if container_widget.page is None: return
                container_widget.content = ft.Image(src=final_path, width=160, height=240, fit="cover", border_radius=ft.border_radius.all(8))
                try: container_widget.update(); self.page.update()
                except: pass
        except: pass

    def create_card(self, item):
        """Crea tarjeta genérica para película, serie, anime o dorama"""
        card_width = 160
        poster_url = item.get("poster", "")
        safe_title = re.sub(r'[\\/*?:"<>|]', "", item.get("titulo", "Sin Titulo"))
        filename = f"{safe_title}.jpg"
        final_path = os.path.join(POSTER_DIR, filename)
        content_container = ft.Container(width=card_width, height=240, border_radius=ft.border_radius.all(8))

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            content_container.content = ft.Image(src=final_path, width=card_width, height=240, fit="cover", border_radius=ft.border_radius.all(8))
        else:
            content_container.content = ft.ProgressRing(width=25, height=25, stroke_width=3, color=ft.Colors.WHITE)
            if poster_url: self.page.run_thread(self._download_and_replace, poster_url, final_path, content_container)
            else: content_container.content = ft.Icon(ft.Icons.BROKEN_IMAGE, color="#555", size=30)

        return ft.GestureDetector(content=content_container, on_tap=lambda e: self.open_details(item))

    def get_unique_genres(self):
        genres = set()
        data = self.get_current_data()
        for m in data:
            for g in m.get("genero", []): genres.add(g)
        return sorted(list(genres))

    def filter_data(self):
        filtered = []
        search_lower = self.search_text.lower()
        data = self.get_current_data()
        
        for m in data:
            matches_search = search_lower in m.get("titulo", "").lower()
            matches_genre = self.current_filter == "Todas" or self.current_filter in m.get("genero", [])
            if matches_search and matches_genre: filtered.append(m)
        return filtered

    def update_grid_and_pagination(self):
        try:
            filtered = self.filter_data()
            total_items = len(filtered)
            self.total_pages = (total_items // ITEMS_PER_PAGE) + (1 if total_items % ITEMS_PER_PAGE > 0 else 0)
            if self.page_num > self.total_pages: self.page_num = self.total_pages
            if self.page_num < 1: self.page_num = 1
            
            start_idx = (self.page_num - 1) * ITEMS_PER_PAGE
            end_idx = start_idx + ITEMS_PER_PAGE
            page_items = filtered[start_idx:end_idx]
            
            self.movies_grid.controls.clear()
            if not page_items: 
                self.movies_grid.controls.append(ft.Text("No hay contenidos.", color="grey", size=16))
            else: 
                for m in page_items: self.movies_grid.controls.append(self.create_card(m))
                
            self.pagination_controls.controls.clear()
            prev_btn = ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, on_click=self.prev_page, disabled=self.page_num == 1)
            page_text = ft.Text(f"Pág {self.page_num} / {self.total_pages}", color="white")
            next_btn = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, on_click=self.next_page, disabled=self.page_num == self.total_pages)
            self.pagination_controls.controls.extend([prev_btn, page_text, next_btn])
            self.page.update()
        except Exception as e:
            print(f"[!] Error actualizando grid: {e}")

    def show_home(self):
        self.page.clean()
        
        menu_btn = ft.IconButton(ft.Icons.MENU, on_click=self.open_drawer)
        
        self.search_field = ft.TextField(hint_text=f"Buscar {self.get_current_type_name().lower()}...", border_color="#E50914", color="white", bgcolor="#141414", expand=True, height=40, text_size=14, on_change=self.on_search_change)
        
        download_btn = ft.IconButton(icon=ft.Icons.DOWNLOAD, tooltip="Descargas", on_click=lambda e: self.show_downloads())

        app_bar = ft.Container(
            content=ft.Row([
                menu_btn, 
                ft.Text("FletStream", size=20, weight="bold", color="#E50914"),
                ft.Container(width=10),
                self.search_field,
                download_btn
            ], alignment="spaceBetween"), 
            padding=10, bgcolor="#141414"
        )
        
        genres = ["Todas"] + self.get_unique_genres()
        self.filter_chips = []
        for g in genres:
            chip = ft.Chip(label=ft.Text(g, size=12), selected_color="#E50914", check_color="white", bgcolor="#141414", selected=self.current_filter == g, on_click=lambda e, genre=g: self.on_genre_click(genre))
            self.filter_chips.append(chip)

        filters_list = ft.ListView(controls=self.filter_chips, horizontal=True, spacing=5, padding=ft.Padding(left=10, right=10, top=10, bottom=10), height=50)
        self.update_grid_and_pagination() 
        
        grid_container = ft.Container(content=self.movies_grid, expand=True, margin=10, padding=10, alignment=ft.Alignment.CENTER)
        pagination_container = ft.Container(content=self.pagination_controls, padding=ft.Padding(left=10, right=10, top=10, bottom=10), bgcolor="#141414")

        main_column = ft.Column([app_bar, filters_list, ft.Divider(height=1, color="transparent"), grid_container, pagination_container], expand=True, alignment="center")
        self.page.add(ft.SafeArea(main_column))

    def open_details(self, item):
        tipo = item.get("tipo", "")
        if tipo == "dorama" or tipo == "anime_serie" or tipo == "serie":
            self.show_series_details(item)
        elif tipo == "anime_pelicula" or tipo == "pelicula":
            self.show_movie_details(item)
        else:
            if self.current_tab_index == 1 or self.current_tab_index == 2 or self.current_tab_index == 3:
                self.show_series_details(item)
            else:
                self.show_movie_details(item)

    def show_movie_details(self, movie):
        self.page.clean()
        self.current_movie_detail = movie
        poster_url = movie.get("poster", "")
        safe_title = re.sub(r'[\\/*?:"<>|]', "", movie.get("titulo", "Sin Titulo"))
        local_path = os.path.join(POSTER_DIR, f"{safe_title}.jpg")
        final_src = local_path if (os.path.exists(local_path) and os.path.getsize(local_path) > 0) else (poster_url if poster_url else "https://via.placeholder.com/200x300")

        players = [p for p in movie.get("reproductores", []) if p.get("servidor", "").lower() == "voe"]

        back_btn = ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="white", icon_size=30, on_click=lambda e: self.show_home())
        download_btn = ft.IconButton(icon=ft.Icons.DOWNLOAD, icon_color=ft.Colors.BLUE, tooltip="Descargar (Latino)", on_click=lambda e: self.start_download_flow(movie, players))
        
        header_title = "Detalles Película"
        if movie.get("tipo") == "anime_pelicula":
            header_title = "Detalles Anime Película"
            
        top_bar = ft.Container(content=ft.Row([back_btn, ft.Text(header_title, color="white", size=18), ft.Container(expand=True), download_btn]), padding=10, bgcolor="#141414")
        
        poster_container = ft.Container(content=ft.Image(src=final_src, width=200, height=300, fit="cover", border_radius=ft.border_radius.all(12)), margin=ft.margin.only(top=20))
        title_text = ft.Text(movie.get("titulo", "Sin Titulo"), size=24, weight="bold", color="white", text_align="center")
        year_text = ft.Text(movie.get("anio", ""), size=16, color="#E50914", text_align="center")
        genres_row = ft.Row([ft.Chip(label=ft.Text(g, size=11), bgcolor="#333", selected_color="#E50914") for g in movie.get("genero", [])], wrap=True, spacing=5, alignment="center")
        synopsis_text = ft.Container(content=ft.Text(movie.get("sinopsis", "Sin descripción."), size=14, color="grey", text_align="justify"), padding=ft.padding.symmetric(horizontal=20))
        
        servers_title = ft.Text("Reproducir (VOE):", size=16, weight="bold", color="white", margin=ft.margin.only(left=20, top=20))
        
        servers_row = ft.Row(wrap=True, spacing=10, alignment="center")
        if players:
            for p in players:
                idioma = p.get("idioma", "UNK")
                servidor = p.get("servidor", "Server")
                btn_download = ft.IconButton(icon=ft.Icons.DOWNLOAD, icon_color=ft.Colors.GREEN, tooltip=f"Descargar {idioma}", on_click=lambda e, p=p: self.start_download_flow(movie, [p]))
                
                btn_play = ft.ElevatedButton(
                    content=ft.Column([ft.Text(f"{idioma}", size=12, weight="bold", color="white"), ft.Text(f"{servidor}", size=10, color="grey"), btn_download], tight=True, horizontal_alignment="center"),
                    bgcolor="#333", style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)), width=100, height=100,
                    on_click=lambda e, p=p: self.open_player_with_server(movie, p)
                )
                servers_row.controls.append(btn_play)
        else:
            servers_row.controls.append(ft.Text("No hay servidores VOE disponibles.", color="grey"))

        main_column = ft.Column(
            [top_bar, poster_container, ft.Container(padding=10), ft.Column([title_text, year_text, genres_row], spacing=5, horizontal_alignment="center"), ft.Divider(height=20, color="transparent"), synopsis_text, servers_title, servers_row, ft.Container(height=50)], scroll="auto", expand=True, horizontal_alignment="center")
        self.page.add(ft.SafeArea(main_column))

    def show_series_details(self, serie):
        self.page.clean()
        self.current_serie_detail = serie
        
        view_title = "Detalles Serie"
        if serie.get("tipo") == "anime_serie":
            view_title = "Detalles Anime Serie"
        elif serie.get("tipo") == "dorama":
            view_title = "Detalles Dorama"
        
        menu_btn = ft.IconButton(ft.Icons.MENU, on_click=self.open_drawer)
        back_btn = ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="white", icon_size=30, on_click=lambda e: self.show_home())
        
        top_bar = ft.Container(content=ft.Row([menu_btn, back_btn, ft.Text(view_title, color="white", size=18)]), padding=10, bgcolor="#141414")
        
        poster_url = serie.get("poster", "")
        safe_title = re.sub(r'[\\/*?:"<>|]', "", serie.get("titulo", "Sin Titulo"))
        local_path = os.path.join(POSTER_DIR, f"{safe_title}.jpg")
        final_src = local_path if (os.path.exists(local_path) and os.path.getsize(local_path) > 0) else (poster_url if poster_url else "https://via.placeholder.com/200x300")
        
        header_content = ft.Row([
            ft.Image(src=final_src, width=120, height=180, fit="cover", border_radius=ft.border_radius.all(8)),
            ft.Column([
                ft.Text(serie.get("titulo", "Sin Titulo"), size=20, weight="bold", color="white", width=200),
                ft.Text(serie.get("anio", ""), size=14, color="#E50914"),
                ft.Text(serie.get("sinopsis", ""), size=12, color="grey", max_lines=4)
            ], expand=True, spacing=5)
        ], spacing=15)
        
        temporadas_data = serie.get("temporadas", {})
        
        try:
            temp_options = [ft.dropdown.Option(text=f"Temporada {t}", key=t) for t in sorted(temporadas_data.keys(), key=int)]
        except Exception as e:
            print(f"[!] Error creando opciones de temporada: {e}")
            temp_options = [ft.dropdown.Option(text=f"Temporada {t}", key=t) for t in sorted(temporadas_data.keys())]
        
        if not temp_options:
            self.page.add(ft.SafeArea(ft.Column([top_bar, ft.Text("No hay información de temporadas.", color="white")], alignment="center", horizontal_alignment="center", expand=True)))
            return

        season_dropdown = ft.Dropdown(
            options=temp_options,
            label="Seleccionar Temporada",
            text_size=14,
            bgcolor="#333333",
            color="white",
            dense=True,
            content_padding=10,
            expand=True
        )
        
        episodes_column = ft.Column([], spacing=10, scroll="auto", expand=True)
        
        def load_episodes_by_key(season_key):
            if not season_key: return
            
            print(f"[*] Cargando temporada seleccionada: {season_key}")
            
            episodes = temporadas_data.get(season_key, [])
            episodes_column.controls.clear()
            
            for ep in episodes:
                raw_title = ep.get("titulo", "Episodio")
                clean_title = " ".join(raw_title.split())
                
                voe_players = [p for p in ep.get("reproductores", []) if p.get("servidor", "").lower() == "voe"]
                
                ep_card = ft.Container(
                    bgcolor="#1E1E1E",
                    padding=10,
                    border_radius=8,
                    content=ft.Row([
                        ft.Column([
                            ft.Text(clean_title, size=14, weight="bold", color="white"),
                            ft.Text(f"Servidores VOE: {len(voe_players)}", size=11, color="grey")
                        ], expand=True),
                        
                        ft.Row([
                            ft.IconButton(
                                icon=ft.Icons.PLAY_ARROW, 
                                icon_color="#E50914", 
                                tooltip="Reproducir",
                                on_click=lambda e, p=voe_players[0] if voe_players else None, t=clean_title, s=season_key: self.play_episode(serie, p, t, s) if p else None
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DOWNLOAD, 
                                icon_color=ft.Colors.GREEN, 
                                tooltip="Descargar",
                                on_click=lambda e, p=voe_players, t=clean_title, s=season_key: self.download_episode(serie, p, t, s) if p else None
                            )
                        ])
                    ])
                )
                episodes_column.controls.append(ep_card)
            
            self.page.update()

        season_dropdown.on_select = lambda e: load_episodes_by_key(e.control.value)
        
        if temp_options:
            season_dropdown.value = temp_options[0].key
            load_episodes_by_key(temp_options[0].key)

        content = ft.Column([
            top_bar,
            ft.Container(padding=10, content=header_content),
            ft.Row([season_dropdown]),
            ft.Divider(height=1, color="transparent"),
            ft.Container(content=episodes_column, expand=True, padding=ft.padding.symmetric(horizontal=10))
        ], expand=True)
        
        self.page.add(ft.SafeArea(content))

    def play_episode(self, serie, player_data, ep_title, season_key):
        if not player_data: return
        self._show_loading_ui(f"{serie.get('titulo', 'Serie/Anime/Dorama')} - {ep_title}")
        voe_url = player_data.get("url", "")
        self.page.run_thread(self._worker_extract_and_play, serie, voe_url, is_series=True)

    def download_episode(self, serie, players_list, ep_title, season_key):
        player = players_list[0] if players_list else None
        if not player: return
        
        safe_title = re.sub(r'[\\/*?:"<>|]', "", serie.get("titulo", "Serie"))
        safe_ep_title = re.sub(r'[\\/*?:"<>|]', "", ep_title)
        
        prefix = ""
        if serie.get("tipo") == "anime_serie": prefix = "Anime "
        elif serie.get("tipo") == "dorama": prefix = "Dorama "
            
        custom_filename = f"{prefix}{safe_title} - S{season_key.zfill(2)}{safe_ep_title[:15]} - {player.get('idioma', 'LAT')}"
        
        self.start_download_flow_generic(custom_filename, player.get("url"))

    def start_download_flow_generic(self, custom_filename, voe_url):
        self.page.clean()
        col_content = ft.Column([
                ft.ProgressRing(color="#E50914", width=50, height=50),
                ft.Text("Preparando descarga...", color="white", size=20),
                ft.Text("Extrayendo enlace directo...", color="grey", size=12),
                ft.Text(custom_filename, color="grey", size=12, max_lines=1, overflow="ellipsis")
            ], alignment="center", horizontal_alignment="center", expand=True)
        self.page.add(ft.SafeArea(col_content))
        self.page.update()
        self.page.run_thread(self._worker_extract_and_download_generic, voe_url, custom_filename)

    def _worker_extract_and_download_generic(self, voe_url, custom_filename):
        try:
            direct_link = extract_link_voe(voe_url)
            if direct_link:
                self.download_manager.add_to_queue(custom_filename, direct_link)
                self.show_downloads()
            else:
                self.page.show_dialog(ft.SnackBar(ft.Text("Error: No se pudo extraer el enlace.")))
                self.show_home()
        except Exception as e:
            self.page.show_dialog(ft.SnackBar(ft.Text(f"Error: {str(e)}")))
            self.show_home()

    def show_downloads(self):
        self.page.clean()
        back_btn = ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="white", icon_size=30, on_click=lambda e: self.show_home())
        top_bar = ft.Container(content=ft.Row([back_btn, ft.Text("Gestor de Descargas", color="white", size=18)]), padding=10, bgcolor="#141414")

        active_list = ft.Column(self.download_manager.get_active_ui_list(), spacing=10, scroll="auto", expand=True)
        
        history_list = ft.ListView(expand=True, spacing=5)
        try:
            with open(HISTORIAL_FILE, "r", encoding='utf-8') as f:
                data = json.load(f)
            for item in reversed(data[-50:]): 
                date_str = item.get('fecha', '')[:10]
                row = ft.Row([
                    ft.Column([
                        ft.Text(item.get('titulo', 'Desconocido'), color="white", size=13, width=250),
                        ft.Text(date_str, color="grey", size=10)
                    ], expand=True),
                    ft.IconButton(icon=ft.Icons.FOLDER_OPEN, icon_color=ft.Colors.GREEN, tooltip="Abrir carpeta", on_click=lambda e, p=item['ruta']: os.startfile(os.path.dirname(p)))
                ])
                history_list.controls.append(row)
        except: pass

        tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            length=3, 
            expand=True,
            content=ft.Column(
                expand=True,
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label=ft.Text("Descargando")),
                            ft.Tab(label=ft.Text("Historial")),
                            ft.Tab(label=ft.Text("Logs")),
                        ]
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            ft.Container(content=active_list, padding=10),
                            ft.Container(content=history_list, padding=10),
                            ft.Container(content=self.log_box, padding=10)
                        ]
                    )
                ]
            )
        )

        main_column = ft.Column([top_bar, ft.Divider(height=1, color="transparent"), tabs], expand=True)
        self.page.add(ft.SafeArea(main_column))

    def start_download_flow(self, movie, players_list):
        player = players_list[0] if players_list else None
        if not player:
            self.page.show_dialog(ft.SnackBar(ft.Text("No hay servidor VOE para descargar.")))
            return
        
        voe_url = player.get("url", "")
        language = player.get("idioma", "VOE")
        
        title = movie.get("titulo", "Pelicula")
        safe_title_clean = re.sub(r'[\\/*?:"<>|]', "", title)
        
        if language and language.lower() not in ['voe', 'unk', 'server']:
            custom_filename = f"{safe_title_clean} - {language}"
        else:
            custom_filename = safe_title_clean
            
        self.page.clean()
        col_content = ft.Column([
                ft.ProgressRing(color="#E50914", width=50, height=50),
                ft.Text("Preparando descarga...", color="white", size=20),
                ft.Text(f"Versión: {language}", color="#E50914", size=14),
                ft.Text("Extrayendo enlace directo...", color="grey", size=12),
                ft.Text(movie.get("titulo", ""), color="grey", size=12, max_lines=1, overflow="ellipsis")
            ], alignment="center", horizontal_alignment="center", expand=True)
        self.page.add(ft.SafeArea(col_content))
        self.page.update()

        self.page.run_thread(self._worker_extract_and_download, movie, voe_url, custom_filename)

    def _worker_extract_and_download(self, movie, voe_url, custom_filename):
        try:
            print(f"[*] Extrayendo enlace para descarga: {voe_url}")
            direct_link = extract_link_voe(voe_url)
            if direct_link:
                print(f"[+] Enlace obtenido. Añadiendo a cola...")
                self.download_manager.add_to_queue(custom_filename, direct_link)
                self.show_downloads()
            else:
                self.page.show_dialog(ft.SnackBar(ft.Text("Error: No se pudo extraer el enlace para descargar.")))
                self.show_home() 
        except Exception as e:
            print(f"[!] Error descarga: {e}")
            self.page.show_dialog(ft.SnackBar(ft.Text(f"Error: {str(e)}")))
            self.show_home()

    def open_player_with_server(self, movie, player_data):
        if player_data.get("servidor", "").lower() != "voe":
            self.show_details(movie)
            return
        self._show_loading_ui(movie.get("titulo", "Pelicula"))
        voe_url = player_data.get("url", "")
        self.page.run_thread(self._worker_extract_and_play, movie, voe_url)

    def _show_loading_ui(self, movie_title):
        self.page.clean()
        col_content = ft.Column([
                ft.ProgressRing(color="#E50914", width=50, height=50),
                ft.Text("Conectando y extrayendo...", color="white", size=20),
                ft.Text("Por favor espere unos segundos.", color="grey", size=14),
                ft.Text(f"Reproduciendo: {movie_title}", color="grey", size=12, max_lines=1, overflow="ellipsis")
            ], alignment="center", horizontal_alignment="center", expand=True)
        self.page.add(ft.SafeArea(col_content))
        self.page.update()

    def _worker_extract_and_play(self, content, voe_url, is_series=False):
        try:
            print(f"[*] Iniciando extracción para: {voe_url}")
            direct_link = extract_link_voe(voe_url)
            if direct_link:
                print(f"[+] Enlace obtenido: {direct_link}")
                self._show_video_player_ui(content, direct_link, is_series)
            else:
                print("[!] Falló la extracción.")
                self._show_error_ui("No se pudo extraer el enlace del video.")
        except Exception as e:
            print(f"[!] Error en hilo de reproducción: {e}")
            self._show_error_ui(f"Error: {str(e)}")

    def _show_video_player_ui(self, content, video_url, is_series=False):
        self.page.clean()
        title = content.get("titulo", "Sin Titulo")
        
        header_title = "Reproduciendo Película"
        if is_series:
            if content.get("tipo") == "dorama":
                header_title = "Reproduciendo Dorama"
            else:
                header_title = "Reproduciendo Episodio"
        
        if is_series:
            back_action = lambda e: self.show_series_details(content)
        else:
            back_action = lambda e: self.show_movie_details(content)

        top_bar = ft.Container(content=ft.Row([ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="white", icon_size=30, on_click=back_action), ft.Text(header_title, color="white", size=16, expand=True)]), padding=10, bgcolor="#141414")
        self.video_player = ftv.Video(playlist=[ftv.VideoMedia(video_url)], width=self.page.width, aspect_ratio=16 / 9, autoplay=True, show_controls=True, fill_color=ft.Colors.BLACK, fit="contain", volume=100, on_error=lambda e: print("Error video:", e.data))
        info_section = ft.Container(content=ft.Column([ft.Text(title, size=18, weight="bold", color="white"), ft.Text(content.get("sinopsis", ""), size=13, color="grey")]), padding=20, bgcolor="#141414")
        page_content = ft.Column([top_bar, self.video_player, info_section], scroll="auto", expand=True)
        self.page.add(ft.SafeArea(page_content))

    def _show_error_ui(self, message):
        self.page.clean()
        col_content = ft.Column([
                ft.Icon(ft.Icons.ERROR_OUTLINE, color="red", size=50),
                ft.Text("Error de Reproducción", size=20, color="white", weight="bold"),
                ft.Text(message, size=14, color="grey"),
                ft.ElevatedButton("Volver", on_click=lambda e: self.show_home())
            ], alignment="center", horizontal_alignment="center", expand=True)
        self.page.add(ft.SafeArea(col_content))

    def on_search_change(self, e):
        self.search_text = e.control.value
        self.page_num = 1 
        self.update_grid_and_pagination()

    def on_genre_click(self, genre):
        self.current_filter = genre
        self.page_num = 1
        for chip in self.filter_chips: chip.selected = (chip.label.value == genre)
        self.update_grid_and_pagination()

    def prev_page(self, e):
        if self.page_num > 1: self.page_num -= 1; self.update_grid_and_pagination()

    def next_page(self, e):
        if self.page_num < self.total_pages: self.page_num += 1; self.update_grid_and_pagination()

def main(page: ft.Page):
    app = MovieApp(page)

ft.run(main)
