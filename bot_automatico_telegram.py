# -*- coding: utf-8 -*-
"""
Bot di monitoraggio Subito.it – versione anti-timeout per CI/headless

Strategie adottate per eliminare i timeout:
- Attesa su "document.readyState == complete" e parsing iterativo senza dipendere da classi ofuscate
- Selettori resilienti (href che includono "/annunci/") + fallback multipli per titolo/prezzo
- Scroll progressivo (lazy-load) con cicli di parsing/valutazione
- Gestione banner cookie sia in root sia in iframe (Usercentrics)
- Headless "new", UA e lingua coerenti; disabilitazione flag di automation
- Screenshot automatici su ogni timeout di categoria
- Fallback opzionale a undetected-chromedriver (se installato)

Configurazione: YAML opzionale (bot_annunci.yml in root) oppure default embedded.
Variabili d'ambiente: TELEGRAM_BOT_TOKEN (obbl.), TELEGRAM_CHAT_ID (opz.)
"""

import os
import re
import time
import requests
import bs4
from typing import List, Dict

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchFrameException

# --- YAML opzionale ---
try:
    import yaml  # pyyaml
except Exception:
    yaml = None

# --- undetected-chromedriver opzionale ---
_uc = None
try:
    import undetected_chromedriver as uc  # type: ignore
    _uc = uc
except Exception:
    _uc = None

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

# ----------------- FS UTILS -----------------

def _ensure_abs_cronofile(entry: Dict) -> Dict:
    fname = entry.get("file_cronologia")
    if not fname:
        safe = re.sub(r"[^a-z0-9]+", "_", entry.get("nome_ricerca", "ricerca").lower())
        fname = f"report_annunci_{safe}.txt"
    if not os.path.isabs(fname):
        fname = os.path.join(BASE_DIR, fname)
    entry["file_cronologia"] = fname
    return entry

def carica_configurazione() -> List[Dict]:
    if yaml and os.path.exists(YAML_PATH):
        try:
            with open(YAML_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ricerche = data.get("ricerche")
            if isinstance(ricerche, list) and ricerche:
                return [_ensure_abs_cronofile(dict(e)) for e in ricerche if isinstance(e, dict)]
            print("[CFG] YAML trovato ma 'ricerche' vuoto: uso default.")
        except Exception as ex:
            print(f"[CFG] Errore parsing YAML: {ex}. Uso default.")
    else:
        if not yaml:
            print("[CFG] pyyaml non presente: uso default.")
        else:
            print("[CFG] YAML non trovato: uso default.")
    return [_ensure_abs_cronofile(dict(e)) for e in DEFAULT_RICERCHE]

def carica_link_precedenti(nome_file: str) -> set:
    if not os.path.exists(nome_file):
        return set()
    try:
        with open(nome_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"[FS] Lettura {nome_file} fallita: {e}")
        return set()

def salva_link_attuali(nome_file: str, link_set: set):
    try:
        with open(nome_file, "w", encoding="utf-8") as f:
            for link in sorted(list(link_set)):
                f.write(link + "\n")
    except Exception as e:
        print(f"[FS] Scrittura {nome_file} fallita: {e}")

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

def wait_ready(driver, timeout=20):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def accetta_cookie_se_presente(driver):
    # Tentativo 1: banner principale (fuori iframe)
    try:
        btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accetta') or contains(., 'ACCEPT') or @data-testid='uc-accept-all-button']"))
        )
        btn.click()
        time.sleep(0.4)
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


def scroll_e_parse(driver, max_loops=8, pause=1.0) -> List[Dict]:
    """Esegue scroll incrementali e, ad ogni step, parse degli anchor /annunci/.
    Ritorna una lista di annunci (titolo, prezzo, link)."""
    all_seen = {}
    for i in range(max_loops):
        html = driver.page_source
        soup = bs4.BeautifulSoup(html, "html.parser")
        anchors = soup.select('[data-testid="listing-grid"] a[href*="/annunci/"]') or soup.select('a[href*="/annunci/"]')
        for a in anchors:
            href = a.get("href")
            if not href:
                continue
            if href not in all_seen:
                title_el = a.select_one('[data-testid="ad-title"], h2, h3')
                titolo = (title_el.get_text(strip=True) if title_el else (a.get("aria-label") or a.get("title") or "")).strip() or "(senza titolo)"
                price_el = a.select_one('[data-testid="ad-price"]')
                prezzo = price_el.get_text(strip=True) if price_el else "N/D"
                all_seen[href] = {"link": href, "titolo": titolo, "prezzo": prezzo}
        # euristica: se abbiamo già un numero decente di card, possiamo fermarci
        if len(all_seen) >= 20:
            break
        driver.execute_script("window.scrollBy(0, Math.max(600, window.innerHeight));")
        time.sleep(pause)
    return list(all_seen.values())

# ----------------- CORE -----------------

def esegui_ricerca(driver, cfg: Dict) -> List[Dict]:
    nome = cfg['nome_ricerca']
    print(f"\n--- Avvio ricerca: {nome} ---")
    try:
        driver.get(cfg["url"])  # carica pagina lista
        wait_ready(driver, 25)
        accetta_cookie_se_presente(driver)

        # Primo ciclo di scroll + parse (senza EC aggressivi)
        annunci = scroll_e_parse(driver, max_loops=10, pause=1.0)
        if not annunci:
            # tenta ulteriore attesa breve e un giro extra
            time.sleep(2)
            annunci = scroll_e_parse(driver, max_loops=6, pause=1.1)

        if not annunci:
            print(f"[{nome}] Nessuna card estratta – salvo screenshot e continuo.")
            safe = re.sub(r'[^a-z0-9]+', '_', nome.lower())
            screenshot_path = os.path.join(BASE_DIR, f"errore_{safe}.png")
            try:
                driver.save_screenshot(screenshot_path)
                print(f"[{nome}] Screenshot salvato: {screenshot_path}")
            except Exception as se:
                print(f"[{nome}] Impossibile salvare screenshot: {se}")
            return []

        # Filtri applicati
        link_precedenti = carica_link_precedenti(cfg['file_cronologia'])
        filtrati = []
        for ann in annunci:
            titolo_l = (ann['titolo'] or '').lower()
            prezzo_val = None
            if ann['prezzo'] and '€' in ann['prezzo']:
                nums = re.findall(r"\d+[.,]?\d*", ann['prezzo'].replace(',', '.'))
                prezzo_val = float(nums[0]) if nums else None

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

        print(f"[{nome}] Estratte {len(annunci)} card; pertinenti (nuovi) {len(filtrati)}.")

        # aggiorna cronologia con tutti i link visti in questa run
        tutti_link = {a['link'] for a in annunci}
        salva_link_attuali(cfg['file_cronologia'], link_precedenti | tutti_link)
        return filtrati

    except Exception as e:
        print(f"[{nome}] Errore imprevisto: {e}")
        safe = re.sub(r'[^a-z0-9]+', '_', nome.lower())
        screenshot_path = os.path.join(BASE_DIR, f"errore_{safe}.png")
        try:
            driver.save_screenshot(screenshot_path)
            print(f"[{nome}] Screenshot errore salvato: {screenshot_path}")
        except Exception as se:
            print(f"[{nome}] Impossibile salvare screenshot: {se}")
        return []


def build_driver():
    # Tenta undetected-chromedriver se disponibile; altrimenti Selenium standard
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    if _uc is not None:
        opts = _uc.ChromeOptions()
        opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--window-size=1920,1080')
        opts.add_argument('--lang=it-IT')
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_argument(f'--user-agent={ua}')
        driver = _uc.Chrome(options=opts, headless=True)
        return driver

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=it-IT')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument(f'--user-agent={ua}')
    # velocizza: pageLoadStrategy eager per non bloccare su risorse non critiche
    options.page_load_strategy = 'eager'
    service = Service()
    return webdriver.Chrome(service=service, options=options)

# ----------------- MAIN -----------------

def main():
    print("Avvio bot per monitoraggio annunci Subito.it…")
    configs = carica_configurazione()
    print("Config attive:", [c['nome_ricerca'] for c in configs])

    driver = None
    nuovi = {}
    errori = []

    try:
        driver = build_driver()
        # visita home e accetta cookie
        driver.get("https://www.subito.it")
        try:
            wait_ready(driver, 20)
        except Exception:
            pass
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
