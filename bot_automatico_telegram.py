# -*- coding: utf-8 -*-
"""
Bot di monitoraggio Subito.it con:
- Aggiunta ricerca **Nintendo 3DS** (report_annunci_3ds.txt)
- Supporto opzionale a configurazione esterna via YAML (bot_annunci.yml in root)

Variabili d'ambiente richieste:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID (opzionale; se mancante, viene ricavato automaticamente con getUpdates)
"""

import time
import bs4
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import os
import re
import requests

try:
    import yaml  # per leggere bot_annunci.yml
except ImportError:
    yaml = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(BASE_DIR, "bot_annunci.yml")

# Configurazioni di default (usate se manca il file YAML)
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

# --- Funzioni di utilità ---

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
    if yaml is None:
        print("pyyaml non disponibile, uso configurazioni di default.")
        return [dict(_ensure_abs_cronofile(e)) for e in DEFAULT_RICERCHE]

    if os.path.exists(YAML_PATH):
        print(f"Carico configurazione da {YAML_PATH}")
        try:
            with open(YAML_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ricerche = data.get("ricerche")
            if isinstance(ricerche, list) and ricerche:
                return [dict(_ensure_abs_cronofile(e)) for e in ricerche]
        except Exception as ex:
            print(f"Errore parsing YAML: {ex}, uso default.")
    return [dict(_ensure_abs_cronofile(e)) for e in DEFAULT_RICERCHE]


def estrai_prezzo(testo_prezzo: str):
    if not testo_prezzo:
        return None
    numeri = re.findall(r"\d+[.,]?\d*", testo_prezzo.replace(",", "."))
    return float(numeri[0]) if numeri else None


def invia_notifica_telegram(messaggio: str):
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token:
        print("ERRORE: manca TELEGRAM_BOT_TOKEN.")
        return
    if not chat_id:
        chat_id = get_chat_id_from_updates(token)
        if not chat_id:
            return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": messaggio, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=20)
        r.raise_for_status()
        print("Notifica Telegram inviata.")
    except requests.exceptions.RequestException as e:
        print(f"Errore invio notifica: {e}")

def esegui_ricerca(driver, cfg):
    print(f"\n--- Avvio ricerca: {cfg['nome_ricerca']} ---")
    try:
        driver.get(cfg["url"])
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="SmallCard-module_card__"]'))
        )
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        soup = bs4.BeautifulSoup(driver.page_source, "html.parser")
        ad_cards = soup.select('a[class*="SmallCard-module_link__"]')

        if not ad_cards:
            print("Nessun annuncio trovato.")
            return []

        annunci_filtrati = []
        link_visti = carica_link_precedenti(cfg["file_cronologia"])

        for card in ad_cards:
            titolo_tag = card.find("h2")
            prezzo_tag = card.find("p", class_=lambda x: x and "price" in x.lower())
            if not titolo_tag:
                continue

            link = card.get("href")
            if not link or link in link_visti:
                continue

            titolo = titolo_tag.text.strip().lower()
            prezzo_str = prezzo_tag.text.strip() if prezzo_tag else "N/D"
            prezzo_val = estrai_prezzo(prezzo_str)

            if "venduto" in prezzo_str.lower():
                continue
            if any(kw in titolo for kw in cfg.get("keyword_da_escludere", [])):
                continue
            if cfg.get("keyword_da_includere") and not any(kw in titolo for kw in cfg["keyword_da_includere"]):
                continue
            if prezzo_val is not None and prezzo_val > cfg.get("budget_massimo", 9e9):
                continue

            annunci_filtrati.append({
                "titolo": titolo_tag.text.strip(),
                "prezzo": prezzo_str,
                "link": link,
            })

        salva_link_attuali(cfg["file_cronologia"], link_visti | link_attuali)
        return annunci_filtrati

    except TimeoutException:
        print(f"Timeout per {cfg['nome_ricerca']}")
        return []
    except Exception as e:
        print(f"Errore imprevisto in {cfg['nome_ricerca']}: {e}")
        screenshot_path = os.path.join(
            BASE_DIR,
            f"errore_{re.sub(r'[^a-z0-9]+','_', cfg['nome_ricerca'].lower())}.png"
        )
        try:
            driver.save_screenshot(screenshot_path)
            print(f"Screenshot errore salvato: {screenshot_path}")
        except Exception as se:
            print(f"Impossibile salvare screenshot: {se}")
        return []


if __name__ == "__main__":
    print("Avvio bot Subito.it ...")
    configs = carica_configurazione()
    print("Config attive:", [c["nome_ricerca"] for c in configs])

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = None
    nuovi_annunci_complessivi = {}
    errori = []

    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # Accetta cookies se presente
        driver.get("https://www.subito.it")
        try:
            WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Accetta') or contains(., 'ACCEPT')]")
                )
            ).click()
            print("Banner cookie accettato.")
            time.sleep(1)
        except TimeoutException:
            print("Nessun banner cookie trovato.")

        # Esegui ricerche
        for cfg in configs:
            risultati = esegui_ricerca(driver, cfg)
            if risultati:
                nuovi = [ann for ann in risultati if ann["link"] not in carica_link_precedenti(cfg["file_cronologia"])]
                if nuovi:
                    nuovi_annunci_complessivi[cfg["nome_ricerca"]] = nuovi
                    salva_link_attuali(cfg["file_cronologia"], set(ann["link"] for ann in risultati))

    except Exception as e:
        print(f"ERRORE CRITICO: {e}")
        errori.append(f"Generale: {type(e).__name__}")
    finally:
        if driver:
            driver.quit()
            print("Browser chiuso.")

    if nuovi_annunci_complessivi:
        messaggio = "<b>📢 Nuove offerte trovate!</b>\n\n"
        for categoria, lista_annunci in nuovi_annunci_complessivi.items():
            messaggio += f"<b>--- {categoria.upper()} ---</b>\n"
            for annuncio in lista_annunci:
                messaggio += f"{annuncio['titolo']} — <b>{annuncio['prezzo']}</b>\n<a href='{annuncio['link']}'>Vedi annuncio</a>\n\n"
        invia_notifica_telegram(messaggio)
    else:
        print("Nessun nuovo annuncio trovato.")

    if errori:
        invia_notifica_telegram("[BOT] Errori durante l'esecuzione: " + ", ".join(errori))
