# -*- coding: utf-8 -*-
"""
Bot di monitoraggio Subito.it con:
- Aggiunta ricerca Nintendo 3DS
- Gestione cookie banner Usercentrics in iframe
- Selettori resilienti (basati su href / data-testid)
- Scroll progressivo + attesa lazy-load
- Headless "new", UA e lingua coerenti
- Stealth opzionale se disponibile
- Log più espliciti e screenshot d'errore

Configurazione: via YAML (bot_annunci.yml in root) o default embedded.
Var. ambiente: TELEGRAM_BOT_TOKEN (obbl.), TELEGRAM_CHAT_ID (opzionale)
"""

import os
import re
import time
import requests
import bs4
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchFrameException

# YAML opzionale
try:
    import yaml
except Exception:
    yaml = None

# Stealth opzionale
try:
    from selenium_stealth import stealth
except Exception:
    stealth = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(BASE_DIR, "bot_annunci.yml")

DEFAULT_RICERCHE = [
    {
        "nome_ricerca": "PSP",
        "url": "https://www.subito.it/annunci-italia/vendita/usato/?q=psp",
        "budget_massimo": 50,
        "keyword_da_includere": ["psp"],
        "keyword_da_escludere": ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_psp.txt"),
    },
    {
        "nome_ricerca": "Switch OLED",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled&shp=true",
        "budget_massimo": 150,
        "keyword_da_includere": ["switch", "oled"],
        "keyword_da_escludere": ["riparazione", "cerco", "non funzionante"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_switch.txt"),
    },
    {
        "nome_ricerca": "PlayStation 5",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5&shp=true",
        "budget_massimo": 200,
        "keyword_da_includere": ["ps5", "playstation 5", "playstation5"],
        "keyword_da_escludere": ["riparazione", "cerco", "non funzionante", "controller", "solo pad", "cover", "base"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_ps5.txt"),
    },
    {
        "nome_ricerca": "Nintendo 3DS",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds&shp=true",
        "budget_massimo": 120,
        "keyword_da_includere": ["3ds", "nintendo 3ds"],
        "keyword_da_escludere": ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco", "non funzionante"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_3ds.txt"),
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ----------------- UTILS -----------------

def _ensure_abs_cronofile(entry: dict) -> dict:
    fname = entry.get("file_cronologia")
    if not fname:
        safe = re.sub(r"[^a-z0-9]+", "_", entry.get("nome_ricerca", "ricerca").lower())
        fname = f"report_annunci_{safe}.txt"
    if not os.path.isabs(fname):
        fname = os.path.join(BASE_DIR, fname)
    entry["file_cronologia"] = fname
    return entry

def carica_configurazione():
    if yaml and os.path.exists(YAML_PATH):
        try:
            with open(YAML_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ricerche = data.get("ricerche")
            if isinstance(ricerche, list) and ricerche:
                return [_ensure_abs_cronofile(dict(e)) for e in ricerche if isinstance(e, dict)]
            print("[CFG] YAML trovato ma sezione 'ricerche' mancante/vuota: uso default.")
        except Exception as ex:
            print(f"[CFG] Errore parsing YAML: {ex}. Uso default.")
    else:
        if not yaml:
            print("[CFG] pyyaml non presente: uso default.")
        else:
            print("[CFG] YAML non trovato: uso default.")
    return [_ensure_abs_cronofile(dict(e)) for e in DEFAULT_RICERCHE]

def carica_link_precedenti(nome_file: str):
    if not os.path.exists(nome_file):
        return set()
    try:
        with open(nome_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"[FS] Lettura {nome_file} fallita: {e}")
        return set()

def salva_link_attuali(nome_file: str, link_set):
    try:
        with open(nome_file, "w", encoding="utf-8") as f:
            for link in sorted(list(link_set)):
                f.write(link + "\n")
    except Exception as e:
        print(f"[FS] Scrittura {nome_file} fallita: {e}")

def estrai_prezzo(testo: str):
    if not testo:
        return None
    nums = re.findall(r"\d+[.,]?\d*", testo.replace(",", "."))
    return float(nums[0]) if nums else None

# ----------------- TELEGRAM -----------------

def get_chat_id_from_updates(token, timeout=10):
    print("[TG] TELEGRAM_CHAT_ID non impostato: provo getUpdates…")
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        for item in reversed(data.get("result", [])):
            msg = item.get("message") or item.get("channel_post")
            if msg and msg.get("chat", {}).get("id"):
                cid = str(msg["chat"]["id"])
                print(f"[TG] chat_id trovato: {cid}")
                return cid
    except Exception as e:
        print(f"[TG] getUpdates errore: {e}")
    print("[TG] chat_id non determinato.")
    return None

def invia_notifica_telegram(messaggio: str):
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token:
        print("[TG] ERRORE: manca TELEGRAM_BOT_TOKEN.")
        return
    if not chat_id:
        chat_id = get_chat_id_from_updates(token)
        if not chat_id:
            return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": messaggio,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        r.raise_for_status()
        print("[TG] Notifica inviata.")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

# ----------------- SELENIUM HELPERS -----------------

def accetta_cookie_se_presente(driver):
    # Tentativo 1: banner principale (fuori iframe)
    try:
        btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accetta') or contains(., 'ACCEPT') or @data-testid='uc-accept-all-button']"))
        )
        btn.click()
        time.sleep(0.5)
        print("[COOKIE] Accettato banner (root)")
        return
    except TimeoutException:
        pass

    # Tentativo 2: iframe Usercentrics
    try:
        WebDriverWait(driver, 3).until(EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, "iframe[src*='usercentrics']")))
        for xpath in [
            "//button[@data-testid='uc-accept-all-button']",
            "//button[contains(., 'Accetta tutto')]",
            "//button[contains(., 'Accept all')]",
        ]:
            try:
                WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
                print("[COOKIE] Accettato banner (iframe)")
                break
            except TimeoutException:
                continue
        driver.switch_to.default_content()
    except (TimeoutException, NoSuchFrameException):
        driver.switch_to.default_content()


def scroll_progressivo(driver, steps=6, pause=1.2):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
        time.sleep(pause)


def estrai_annunci_da_html(html: str):
    soup = bs4.BeautifulSoup(html, "html.parser")
    # selettori resilienti: preferisci listing grid e link /annunci/
    cards = soup.select('[data-testid="listing-grid"] a[href*="/annunci/"]')
    if not cards:
        cards = soup.select('a[href*="/annunci/"]')
    risultati = []
    for a in cards:
        href = a.get("href")
        if not href:
            continue
        titolo = None
        # prova data-testid o heading
        titolo_tag = a.select_one('[data-testid="ad-title"], h2, h3')
        if titolo_tag and hasattr(titolo_tag, 'get_text'):
            titolo = titolo_tag.get_text(strip=True)
        prezzo_tag = a.select_one('[data-testid="ad-price"]')
        prezzo = prezzo_tag.get_text(strip=True) if prezzo_tag else "N/D"
        risultati.append({"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo})
    return risultati

# ----------------- CORE -----------------

def esegui_ricerca(driver, cfg):
    nome = cfg['nome_ricerca']
    print(f"\n--- Avvio ricerca: {nome} ---")
    try:
        driver.get(cfg["url"])  # carica pagina lista
        accetta_cookie_se_presente(driver)

        # attesa di una griglia o comunque di link annunci
        WebDriverWait(driver, 25).until(
            EC.any_of(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="listing-grid"] a[href*="/annunci/"]')),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/annunci/"]')),
            )
        )

        scroll_progressivo(driver, steps=6, pause=1.1)
        time.sleep(1.2)  # stabilizzazione

        annunci = estrai_annunci_da_html(driver.page_source)
        if not annunci:
            print(f"[{nome}] Nessuna card estratta (markup cambiato?)")
            return []

        # filtri
        link_precedenti = carica_link_precedenti(cfg['file_cronologia'])
        filtrati = []
        for ann in annunci:
            titolo_l = (ann['titolo'] or '').lower()
            prezzo_val = estrai_prezzo(ann['prezzo'])
            if 'venduto' in (ann['prezzo'] or '').lower():
                continue
            if any(kw in titolo_l for kw in cfg.get('keyword_da_escludere', [])):
                continue
            incl = cfg.get('keyword_da_includere') or []
            if incl and not any(kw in titolo_l for kw in incl):
                continue
            if (prezzo_val is not None) and (prezzo_val > cfg.get('budget_massimo', 9e9)):
                continue
            if ann['link'] in link_precedenti:
                continue
            filtrati.append(ann)

        print(f"[{nome}] Trovati {len(filtrati)} annunci pertinenti (nuovi).")

        # aggiorna cronologia con tutti i link visti in questa run
        tutti_link = {a['link'] for a in annunci}
        salva_link_attuali(cfg['file_cronologia'], link_precedenti | tutti_link)
        return filtrati

    except TimeoutException:
        print(f"[{nome}] Timeout in attesa degli annunci.")
        return []
    except Exception as e:
        print(f"[{nome}] Errore imprevisto: {e}")
        # screenshot
        safe = re.sub(r'[^a-z0-9]+', '_', nome.lower())
        screenshot_path = os.path.join(BASE_DIR, f"errore_{safe}.png")
        try:
            driver.save_screenshot(screenshot_path)
            print(f"[{nome}] Screenshot errore salvato: {screenshot_path}")
        except Exception as se:
            print(f"[{nome}] Impossibile salvare screenshot: {se}")
        return []


def main():
    print("Avvio bot per monitoraggio annunci Subito.it…")
    configs = carica_configurazione()
    print("Config attive:", [c['nome_ricerca'] for c in configs])

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--lang=it-IT')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

    driver = None
    nuovi = {}
    errori = []

    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        if stealth:
            stealth(
                driver,
                languages=["it-IT", "it"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )

        # visita home per (eventuale) cookie
        driver.get("https://www.subito.it")
        accetta_cookie_se_presente(driver)

        for cfg in configs:
            risultati = esegui_ricerca(driver, cfg)
            if risultati:
                nuovi[cfg['nome_ricerca']] = risultati

    except Exception as e:
        print(f"[MAIN] ERRORE CRITICO: {e}")
        errori.append(f"Generale: {type(e).__name__}")
    finally:
        if driver:
            driver.quit()
            print("Browser chiuso.")

    if nuovi:
        msg = "<b>📢 Nuove offerte trovate!</b>\n\n"
        for categoria, lista in nuovi.items():
            msg += f"<b>--- {categoria.upper()} ---</b>\n"
            for a in lista:
                msg += f"{a['titolo']} — <b>{a['prezzo']}</b>\n<a href='{a['link']}'>Vedi annuncio</a>\n\n"
        invia_notifica_telegram(msg)
    else:
        print("Nessun nuovo annuncio trovato in questa esecuzione.")

    if errori:
        invia_notifica_telegram("[BOT] Errori durante l'esecuzione: " + ", ".join(errori))

if __name__ == '__main__':
    main()
