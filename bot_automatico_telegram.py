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
from selenium_stealth import stealth

# --- OTTIMIZZAZIONE PERCORSO FILE ---
# Ottiene il percorso assoluto della cartella in cui si trova lo script.
# Questo garantisce che i file di cronologia vengano sempre trovati,
# indipendentemente da dove viene eseguito lo script.
# __file__ si riferisce al file corrente (lo script .py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --- CONFIGURAZIONE RICERCHE ---
CONFIGURAZIONE_RICERCHE = [
    {
        "nome_ricerca": "PSP",
        "url": "https://www.subito.it/annunci-italia/vendita/usato/?q=psp",
        "budget_massimo": 50,
        "keyword_da_includere": ['psp'],
        "keyword_da_escludere": ['solo giochi', 'solo gioco', 'solo custodia', 'riparazione', 'cerco'],
        # CORREZIONE: Usa un percorso assoluto per il file di cronologia
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_psp.txt")
    },
    {
        "nome_ricerca": "Switch OLED",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled&shp=true",
        "budget_massimo": 150,
        "keyword_da_includere": ['switch', 'oled'],
        "keyword_da_escludere": ['riparazione', 'cerco', 'non funzionante'],
        # CORREZIONE: Usa un percorso assoluto per il file di cronologia
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_switch.txt")
    },
    {
        "nome_ricerca": "PlayStation 5",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5&shp=true",
        "budget_massimo": 200,
        "keyword_da_includere": ['ps5', 'playstation 5', 'playstation5'],
        "keyword_da_escludere": ['riparazione', 'cerco', 'non funzionante', 'controller', 'solo pad', 'cover', 'base'],
        # CORREZIONE: Usa un percorso assoluto per il file di cronologia
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_ps5.txt")
    }
]

# --- VARIABILI D'AMBIENTE TELEGRAM ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- FUNZIONI UTILI ---

def carica_link_precedenti(nome_file):
    """Carica i link da un file di cronologia in un set per un confronto rapido."""
    if not os.path.exists(nome_file):
        return set()
    try:
        with open(nome_file, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except IOError as e:
        print(f"Errore durante la lettura del file {nome_file}: {e}")
        return set()


def salva_link_attuali(nome_file, link_set):
    """Salva il set di link attuali nel file di cronologia, sovrascrivendolo."""
    try:
        with open(nome_file, 'w', encoding='utf-8') as f:
            for link in sorted(list(link_set)):
                f.write(link + '\n')
    except IOError as e:
        print(f"Errore durante la scrittura del file {nome_file}: {e}")


def estrai_prezzo(testo_prezzo):
    """Estrae il valore numerico del prezzo da una stringa."""
    if not testo_prezzo:
        return None
    numeri = re.findall(r'\d+[.,]?\d*', testo_prezzo.replace(',', '.'))
    return float(numeri[0]) if numeri else None


def get_chat_id_from_updates(token, timeout=10):
    """Tenta di recuperare un chat_id valido dagli ultimi messaggi inviati al bot."""
    print("TELEGRAM_CHAT_ID non impostato. Provo a recuperarlo automaticamente...")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        for item in reversed(data.get('result', [])):
            msg = item.get('message') or item.get('channel_post')
            if msg and 'chat' in msg and 'id' in msg['chat']:
                chat_id = str(msg['chat']['id'])
                print(f"Trovato chat_id: {chat_id}")
                return chat_id
    except requests.exceptions.RequestException as e:
        print(f"Errore durante la chiamata a getUpdates: {e}")
    print("Non sono riuscito a determinare il chat_id. Assicurati di aver inviato almeno un messaggio al bot.")
    return None


def invia_notifica_telegram(messaggio):
    """Invia un messaggio tramite il bot Telegram, gestendo il recupero del chat_id."""
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token:
        print("ERRORE: La variabile d'ambiente TELEGRAM_BOT_TOKEN non è impostata.")
        return

    if not chat_id:
        chat_id = get_chat_id_from_updates(token)
        if not chat_id:
            return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': messaggio, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    try:
        r = requests.post(url, data=payload, timeout=20)
        r.raise_for_status()
        print("Notifica Telegram inviata con successo.")
    except requests.exceptions.RequestException as e:
        print(f"Errore durante l'invio della notifica Telegram: {e}")


# --- FUNZIONE DI SCRAPING OTTIMIZZATA E CORRETTA ---

def esegui_ricerca(driver, config_ricerca):
    """Esegue una singola ricerca utilizzando un'istanza del driver già attiva."""
    print(f"\n--- Avvio scraping per: {config_ricerca['nome_ricerca']} ---")
    try:
        driver.get(config_ricerca['url'])
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="SmallCard-module_card__"]'))
        )
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        soup = bs4.BeautifulSoup(driver.page_source, 'html.parser')
        ad_cards = soup.select('a[class*="SmallCard-module_link__"]')

        if not ad_cards:
            print("Nessun annuncio trovato sulla pagina.")
            return []

        annunci_filtrati = []
        link_visti = set()

        for card in ad_cards:
            titolo_tag = card.find('h2')
            prezzo_tag = card.find('p', class_=lambda x: x and 'price' in x.lower())
            
            if not titolo_tag: continue
            
            link = card.get('href')
            if not link or link in link_visti: continue
            link_visti.add(link)

            titolo = titolo_tag.text.strip().lower()
            prezzo_str = prezzo_tag.text.strip() if prezzo_tag else 'N/D'
            prezzo_val = estrai_prezzo(prezzo_str)

            # --- LOGICA DI FILTRAGGIO CORRETTA E PIÙ CHIARA ---
            if 'venduto' in prezzo_str.lower():
                continue
            if any(kw in titolo for kw in config_ricerca['keyword_da_escludere']):
                continue
            if not any(kw in titolo for kw in config_ricerca['keyword_da_includere']):
                continue
            if prezzo_val is not None and prezzo_val > config_ricerca['budget_massimo']:
                continue

            annuncio_obj = {
                "titolo": titolo_tag.text.strip(), 
                "prezzo": prezzo_str, 
                "link": link
            }
            annunci_filtrati.append(annuncio_obj)

        print(f"Trovati {len(annunci_filtrati)} annunci pertinenti per '{config_ricerca['nome_ricerca']}'.")
        return annunci_filtrati

    except TimeoutException:
        print(f"Timeout durante l'attesa degli annunci per '{config_ricerca['nome_ricerca']}'.")
        return []
    except Exception as e:
        print(f"Errore imprevisto durante lo scraping di '{config_ricerca['nome_ricerca']}': {e}")
        screenshot_path = f'errore_{config_ricerca["nome_ricerca"]}.png'
        try:
            driver.save_screenshot(screenshot_path)
            print(f"Screenshot dell'errore salvato in '{screenshot_path}'")
        except Exception as se:
            print(f"Impossibile salvare lo screenshot: {se}")
        return []


# --- SCRIPT PRINCIPALE OTTIMIZZATO ---
if __name__ == '__main__':
    print('Avvio bot per monitoraggio annunci Subito.it...')
    
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    driver = None
    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        stealth(driver, languages=["it-IT", "it"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)

        print("Accettazione banner cookie (se presente)...")
        driver.get("https://www.subito.it")
        try:
            WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accetta') or contains(., 'ACCEPT')]"))
            ).click()
            print("Banner cookie accettato.")
            time.sleep(1)
        except TimeoutException:
            print("Banner cookie non trovato o già accettato.")
            
        nuovi_annunci_complessivi = {}
        errori = []

        for cfg in CONFIGURAZIONE_RICERCHE:
            link_precedenti = carica_link_precedenti(cfg['file_cronologia'])
            print(f"[{cfg['nome_ricerca']}] Caricati {len(link_precedenti)} link dalla cronologia.")
            
            annunci_attuali_obj = esegui_ricerca(driver, cfg)
            
            if not annunci_attuali_obj:
                continue

            link_attuali = set(ann['link'] for ann in annunci_attuali_obj)
            link_nuovi = link_attuali - link_precedenti

            if link_nuovi:
                annunci_da_notificare = [ann for ann in annunci_attuali_obj if ann['link'] in link_nuovi]
                nuovi_annunci_complessivi[cfg['nome_ricerca']] = annunci_da_notificare
                print(f"[{cfg['nome_ricerca']}] TROVATI {len(link_nuovi)} NUOVI ANNUNCI!")

            print(f"[{cfg['nome_ricerca']}] Aggiornamento cronologia con {len(link_attuali)} link totali...")
            salva_link_attuali(cfg['file_cronologia'], link_attuali)

    except Exception as e:
        print(f"ERRORE CRITICO: Si è verificato un problema grave. {e}")
        errori.append(f"Generale: {type(e).__name__}")
    finally:
        if driver:
            driver.quit()
            print("\nBrowser chiuso.")

    if nuovi_annunci_complessivi:
        messaggio = "<b>📢 Nuove offerte trovate!</b>\n\n"
        for categoria, lista_annunci in nuovi_annunci_complessivi.items():
            messaggio += f"<b>--- {categoria.upper()} ---</b>\n"
            for annuncio in lista_annunci:
                messaggio += f"{annuncio['titolo']} — <b>{annuncio['prezzo']}</b>\n<a href='{annuncio['link']}'>Vedi annuncio</a>\n\n"
        invia_notifica_telegram(messaggio)
    else:
        print("Nessun nuovo annuncio trovato in questa esecuzione.")

    if errori:
        invia_notifica_telegram("[BOT] Si sono verificati errori durante l'esecuzione: " + ", ".join(errori))

    print('Esecuzione completata.')