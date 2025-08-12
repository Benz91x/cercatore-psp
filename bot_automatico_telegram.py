"""
Telegram Alert Bot — Guida rapida + Script

Obiettivo:
- Sostituire la notifica WhatsApp con notifiche Telegram (Bot creato tramite BotFather).
- Le notifiche inviate dal bot arriveranno come push sul tuo iPhone se l'app Telegram ha le notifiche abilitate.

Guida step-by-step (sintetica):
1) Installa Telegram sul tuo iPhone e crea/accedi al tuo account.

2) Crea il bot con BotFather:
   - Apri Telegram e cerca @BotFather
   - Invia il comando: /newbot
   - Segui le istruzioni: scegli un nome (es. "AnnunciBot") e uno username (es. "AnnunciBotAle_bot").
   - Alla fine BotFather ti restituirà un token del tipo: 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ
     Salvalo (è il `TELEGRAM_BOT_TOKEN`).

3) Avvia la chat con il bot sul tuo iPhone (cerca l'username e premi "Start").
   Questo è necessario: i bot non possono inviarti messaggi finché non li hai avviati.

4) Recupera il tuo CHAT_ID (numero necessario al bot per scriverti):
   Opzione A (rapida, via browser):
     - Dopo aver inviato /start al bot, apri nel browser:
       https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates
     - Cerca nel JSON restituito la chiave "chat": { "id": ... } e copia quel numero (es. 123456789).
   Opzione B (via script):
     - Usa lo script incluso qui sotto chiamando la funzione `get_chat_id_from_updates()`.

5) Imposta i secrets su GitHub (o come variabili d'ambiente sul server dove esegui il bot):
   - TELEGRAM_BOT_TOKEN = <token di BotFather>
   - TELEGRAM_CHAT_ID = <chat id numerico trovato al punto 4>

6) Verifica notifiche su iPhone:
   - Impostazioni iPhone -> Notifiche -> Telegram -> abilita "Consenti notifiche" (Badge, Suoni, Avvisi).
   - Apri la chat con il bot e assicurati che NON sia silenziata.

7) Posiziona lo script nel repository (es. `bot_automatico_telegram.py`) e aggiorna il workflow GitHub Actions
   (.github/workflows/bot_annunci.yml) per esportare i secrets `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

8) Esegui il workflow / script: quando il bot invia messaggi, il tuo iPhone riceverà la push notification tramite l'app Telegram.

Note tecniche e limiti:
- Il bot non può iniziare la conversazione: devi premere "Start" nella chat del bot almeno una volta.
- Telegram è completamente gratuito e non prevede limiti pratici per questo uso.

--- SCRIPT ---

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
    """Prova a leggere gli aggiornamenti del bot per ottenere un chat_id.
    Funziona se hai già inviato /start al bot dal tuo account Telegram.
    """
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            print(f"Impossibile chiamare getUpdates. Status: {r.status_code}")
            return None
        data = r.json()
        for item in data.get('result', []):
            # controlliamo sia `message` che `channel_post`
            msg = item.get('message') or item.get('channel_post')
            if not msg:
                continue
            chat = msg.get('chat')
            if not chat:
                continue
            chat_id = chat.get('id')
            if chat_id:
                print(f"Trovato chat_id: {chat_id}")
                return str(chat_id)
    except Exception as e:
        print(f"Errore during getUpdates: {e}")
    return None


def invia_notifica_telegram(messaggio):
    """Invia messaggio testuale via Telegram Bot API.
    Assicurati che TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID siano impostati.
    """
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token:
        print("ERRORE: TELEGRAM_BOT_TOKEN non impostato. Crea il bot con BotFather e imposta il token come variabile d'ambiente.")
        return

    if not chat_id:
        print("ATTENZIONE: TELEGRAM_CHAT_ID non impostato. Provo a recuperarne uno tramite getUpdates...")
        chat_id = get_chat_id_from_updates(token)
        if not chat_id:
            print("Non sono riuscito a determinare il chat_id. Imposta manualmente TELEGRAM_CHAT_ID dopo aver inviato /start al bot.")
            return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': messaggio,
        # 'parse_mode': 'HTML'  # opzionale
    }
    try:
        r = requests.post(url, data=payload, timeout=20)
        if r.status_code == 200:
            print("Notifica Telegram inviata con successo.")
        else:
            print(f"Errore invio Telegram. Status: {r.status_code} - {r.text}")
    except requests.exceptions.RequestException as e:
        print(f"Eccezione durante invio Telegram: {e}")


# --- FUNZIONI DI SCRAPING (simili al tuo script originale) ---

def esegui_ricerca(config_ricerca):
    print(f"\n--- Avvio scraping per: {config_ricerca['nome_ricerca']} ---")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    driver = None
    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(config_ricerca['url'])

        try:
            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accetta')]"))).click()
            time.sleep(1)
        except TimeoutException:
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
