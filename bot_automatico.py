import time
import bs4
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import os
import re
import requests # Usiamo requests per inviare il messaggio a Telegram

# --- IMPOSTAZIONI DI RICERCA "SMART" ---
LINK = "https://www.subito.it/annunci-italia/vendita/usato/?q=psp"
BUDGET_MASSIMO = 50
KEYWORD_DA_INCLUDERE = ['psp']
KEYWORD_DA_ESCLUDERE = ['solo giochi', 'solo gioco', 'solo custodia', 'riparazione', 'cerco']
NOME_FILE_ANNUNCI = "report_annunci_psp.txt" # Questo file verrà creato nell'ambiente di GitHub

# --- RECUPERO DEI SEGRETI DI GITHUB ---
# GitHub Actions imposta i segreti come variabili d'ambiente
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- FUNZIONI PER GESTIRE IL FILE LOCALE E TELEGRAM ---

def carica_link_precedenti(nome_file):
    if not os.path.exists(nome_file):
        return set()
    with open(nome_file, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)

def salva_link_attuali(nome_file, link_set):
    with open(nome_file, 'w', encoding='utf-8') as f:
        for link in sorted(list(link_set)):
            f.write(link + '\n')

def invia_messaggio_telegram(messaggio):
    """Invia un messaggio alla chat specificata usando l'API di Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERRORE: Token o Chat ID di Telegram non trovati.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': messaggio,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Messaggio inviato con successo a Telegram!")
        else:
            print(f"Errore nell'invio del messaggio: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Eccezione durante l'invio del messaggio a Telegram: {e}")

def estrai_prezzo(testo_prezzo):
    if not testo_prezzo: return None
    numeri = re.findall(r'\d+\.?\d*', testo_prezzo.replace(',', '.'))
    return float(numeri[0]) if numeri else None

# --- FUNZIONE PRINCIPALE DI SCRAPING ---

def esegui_ricerca():
    print("Avvio del browser per lo scraping...")
    chrome_options = webdriver.ChromeOptions()
    # Opzioni necessarie per l'ambiente di GitHub Actions
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    driver = None
    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(LINK)

        try:
            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accetta')]"))).click()
            print("Banner dei cookie accettato.")
            time.sleep(2)
        except TimeoutException:
            print("Banner dei cookie non trovato o già accettato.")
        
        driver.execute_script("window.scrollTo(0, 1000);")
        time.sleep(2)

        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="SmallCard-module_card__"]')))
        print("Annunci caricati.")
        
        soup = bs4.BeautifulSoup(driver.page_source, 'html.parser')
        ad_cards = soup.select('div[class*="SmallCard-module_card__"]')
        
        annunci_filtrati = []
        for card in ad_cards:
            titolo_tag = card.find('h2')
            prezzo_tag = card.find('p')
            link_tag = card.find('a')
            
            if not (titolo_tag and link_tag): continue

            titolo = titolo_tag.text.strip().lower()
            prezzo_str = prezzo_tag.text.strip() if prezzo_tag else "N/D"
            prezzo_val = estrai_prezzo(prezzo_str)

            if (not any(kw in titolo for kw in KEYWORD_DA_INCLUDERE)) or \
               (any(kw in titolo for kw in KEYWORD_DA_ESCLUDERE)) or \
               (prezzo_val is not None and prezzo_val > BUDGET_MASSIMO) or \
               ('venduto' in prezzo_str.lower()):
                continue
            
            annuncio_obj = { "titolo": titolo_tag.text.strip(), "prezzo": prezzo_str, "link": link_tag.get('href') }
            if annuncio_obj['link'] not in [a['link'] for a in annunci_filtrati]:
                 annunci_filtrati.append(annuncio_obj)
        
        return annunci_filtrati

    finally:
        if driver:
            driver.quit()
            print("Browser chiuso.")

# --- SCRIPT PRINCIPALE ---
if __name__ == "__main__":
    print("Avvio ricerca automatica...")
    link_precedenti = carica_link_precedenti(NOME_FILE_ANNUNCI)
    print(f"Trovati {len(link_precedenti)} link nella cronologia.")
    
    annunci_attuali = esegui_ricerca()
    link_attuali = set(ann['link'] for ann in annunci_attuali)
    
    link_nuovi = link_attuali - link_precedenti
    
    if not link_nuovi:
        print("Nessun nuovo annuncio trovato.")
    else:
        print(f"Trovati {len(link_nuovi)} nuovi annunci!")
        messaggio = f"🎉 Trovati {len(link_nuovi)} nuovi annunci per la tua PSP! 🎉\n\n"
        for annuncio in annunci_attuali:
            if annuncio['link'] in link_nuovi:
                messaggio += f"🆕 *{annuncio['titolo']}*\n"
                messaggio += f"   *Prezzo:* {annuncio['prezzo']}\n"
                messaggio += f"   *Link:* {annuncio['link']}\n\n"
        
        invia_messaggio_telegram(messaggio)
        
    # Aggiorna il file di cronologia con tutti i link trovati in questa esecuzione
    salva_link_attuali(NOME_FILE_ANNUNCI, link_attuali)
    print("Ricerca completata.")
