# -*- coding: utf-8 -*-
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
import json
from selenium_stealth import stealth # <-- Importazione di Stealth

# --- CONFIGURAZIONE RICERCHE ---
CONFIGURAZIONE_RICERCHE = [
    {
        "nome_ricerca": "PSP",
        "url": "https://www.subito.it/annunci-italia/vendita/usato/?q=psp",
        "budget_massimo": 50,
        "keyword_da_includere": ['psp'],
        "keyword_da_escludere": ['solo giochi', 'solo gioco', 'solo custodia', 'riparazione', 'cerco'],
        "file_cronologia": "report_annunci_psp.txt"
    },
    {
        "nome_ricerca": "Switch OLED",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled&shp=true",
        "budget_massimo": 150,
        "keyword_da_includere": ['switch', 'oled'],
        "keyword_da_escludere": ['riparazione', 'cerco', 'non funzionante'],
        "file_cronologia": "report_annunci_switch.txt"
    },
    {
        "nome_ricerca": "PlayStation 5",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5&shp=true",
        "budget_massimo": 200,
        "keyword_da_includere": ['ps5', 'playstation 5', 'playstation5'],
        "keyword_da_escludere": ['riparazione', 'cerco', 'non funzionante', 'controller', 'solo pad', 'cover', 'base'],
        "file_cronologia": "report_annunci_ps5.txt"
    }
]

# --- VARIABILI D'AMBIENTE TELEGRAM ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- FUNZIONI UTILI ---

def carica_link_precedenti(nome_file):
    if not os.path.exists(nome_file):
        return set()
    with open(nome_file, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)


def salva_link_attuali(nome_file, link_set):
    with open(nome_file, 'w', encoding='utf-8') as f:
        for link in sorted(list(link_set)):
            f.write(link + '\n')


def estrai_prezzo(testo_prezzo):
    if not testo_prezzo:
        return None
    numeri = re.findall(r'\d+\.?\d*', testo_prezzo.replace(',', '.'))
    return float(numeri[0]) if numeri else None


def get_chat_id_from_updates(token, timeout=10):
    """Prova a leggere gli aggiornamenti del bot per ottenere un chat_id."""
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            print(f"Impossibile chiamare getUpdates. Status: {r.status_code}")
            return None
        data = r.json()
        for item in data.get('result', []):
            msg = item.get('message') or item.get('channel_post')
            if not msg: continue
            chat = msg.get('chat')
            if not chat: continue
            chat_id = chat.get('id')
            if chat_id:
                print(f"Trovato chat_id: {chat_id}")
                return str(chat_id)
    except Exception as e:
        print(f"Errore during getUpdates: {e}")
    return None


def invia_notifica_telegram(messaggio):
    """Invia messaggio testuale via Telegram Bot API."""
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token:
        print("ERRORE: TELEGRAM_BOT_TOKEN non impostato.")
        return

    if not chat_id:
        print("ATTENZIONE: TELEGRAM_CHAT_ID non impostato. Provo a recuperarlo...")
        chat_id = get_chat_id_from_updates(token)
        if not chat_id:
            print("Non sono riuscito a determinare il chat_id.")
            return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': messaggio}
    try:
        r = requests.post(url, data=payload, timeout=20)
        if r.status_code == 200:
            print("Notifica Telegram inviata con successo.")
        else:
            print(f"Errore invio Telegram. Status: {r.status_code} - {r.text}")
    except requests.exceptions.RequestException as e:
        print(f"Eccezione durante invio Telegram: {e}")


# --- FUNZIONE DI SCRAPING MODIFICATA ---

def esegui_ricerca(config_ricerca):
    print(f"\n--- Avvio scraping per: {config_ricerca['nome_ricerca']} ---")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    # Aggiungiamo un User-Agent realistico per sembrare un vero browser
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
    
    driver = None
    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # --- MODIFICA CHIAVE: Applichiamo STEALTH ---
        stealth(driver,
            languages=["it-IT", "it"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )
        # -------------------------------------------

        driver.get(config_ricerca['url'])

        try:
            # Aumentiamo leggermente il tempo di attesa per il banner dei cookie
            WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accetta')]"))).click()
            time.sleep(1)
        except TimeoutException:
            # Se il banner non appare, non è un problema, andiamo avanti
            print("Banner cookie non trovato o già accettato.")
            pass

        driver.execute_script("window.scrollTo(0, 1000);")
        time.sleep(2)

        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="SmallCard-module_card__"]')))
        soup = bs4.BeautifulSoup(driver.page_source, 'html.parser')
        ad_cards = soup.select('div[class*="SmallCard-module_card__"]')

        annunci_filtrati = []
        for card in ad_cards:
            titolo_tag = card.find('h2')
            prezzo_tag = card.find('p')
            link_tag = card.find('a')
            if not (titolo_tag and link_tag):
                continue
            titolo = titolo_tag.text.strip().lower()
            prezzo_str = prezzo_tag.text.strip() if prezzo_tag else 'N/D'
            prezzo_val = estrai_prezzo(prezzo_str)

            if (not any(kw in titolo for kw in config_ricerca['keyword_da_includere'])) or \
               (any(kw in titolo for kw in config_ricerca['keyword_da_escludere'])) or \
               (prezzo_val is not None and prezzo_val > config_ricerca['budget_massimo']) or \
               ('venduto' in prezzo_str.lower()):
                continue

            annuncio_obj = {"titolo": titolo_tag.text.strip(), "prezzo": prezzo_str, "link": link_tag.get('href')}
            if annuncio_obj['link'] not in [a['link'] for a in annunci_filtrati]:
                annunci_filtrati.append(annuncio_obj)

        return annunci_filtrati

    except Exception as e:
        if driver:
            # In caso di errore, proviamo a salvare uno screenshot per il debug
            screenshot_path = 'errore_screenshot.png'
            driver.save_screenshot(screenshot_path)
            print(f"Screenshot dell'errore salvato in '{screenshot_path}'")
        # Rilanciamo l'eccezione per farla gestire dal blocco principale
        raise e
        
    finally:
        if driver:
            driver.quit()
            print(f"--- Browser chiuso per: {config_ricerca['nome_ricerca']} ---")

# --- SCRIPT PRINCIPALE ---
if __name__ == '__main__':
    print('Avvio bot Telegram per monitoraggio annunci...')
    nuovi_annunci_per_categoria = {}
    errori = []

    for cfg in CONFIGURAZIONE_RICERCHE:
        try:
            link_precedenti = carica_link_precedenti(cfg['file_cronologia'])
            annunci_attuali = esegui_ricerca(cfg)
            if annunci_attuali is None:
                continue

            link_attuali = set(ann['link'] for ann in annunci_attuali)
            link_nuovi = link_attuali - link_precedenti
            if link_nuovi:
                nuovi_annunci_per_categoria[cfg['nome_ricerca']] = [ann for ann in annunci_attuali if ann['link'] in link_nuovi]

            salva_link_attuali(cfg['file_cronologia'], link_attuali)

        except Exception as e:
            print(f"Errore su {cfg['nome_ricerca']}: {e}")
            errori.append(f"{cfg['nome_ricerca']}: {type(e).__name__}")

    if nuovi_annunci_per_categoria:
        messaggio = "📢 Nuove offerte trovate!\n\n"
        for cat, lista in nuovi_annunci_per_categoria.items():
            messaggio += f"--- {cat} ---\n"
            for a in lista:
                messaggio += f"{a['titolo']} — {a['prezzo']}\n{a['link']}\n\n"
        invia_notifica_telegram(messaggio)

    if errori:
        invia_notifica_telegram("[BOT] Alcune ricerche sono fallite: " + ", ".join(errori))

    print('Esecuzione completata.')
