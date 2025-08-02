import time
import bs4
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
import os
import re
import requests
import urllib.parse
from selenium_stealth import stealth

# --- CONFIGURAZIONE RICERCHE ---
# Aggiungi o modifica i dizionari in questa lista per gestire più ricerche.
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
    }
]

# --- RECUPERO DEI SEGRETI DI GITHUB PER WHATSAPP ---
WHATSAPP_PHONE = os.environ.get("WHATSAPP_PHONE")
WHATSAPP_APIKEY = os.environ.get("WHATSAPP_APIKEY")

# --- FUNZIONI DI SUPPORTO ---

def carica_link_precedenti(nome_file):
    """Carica i link degli annunci già processati da un file di testo."""
    if not os.path.exists(nome_file):
        return set()
    with open(nome_file, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)

def salva_link_attuali(nome_file, link_set):
    """Salva i link degli annunci trovati nel file di testo."""
    with open(nome_file, 'w', encoding='utf-8') as f:
        for link in sorted(list(link_set)):
            f.write(link + '\n')

def invia_notifica_whatsapp(messaggio):
    """Invia una notifica a WhatsApp tramite l'API di CallMeBot."""
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        print("ERRORE: Credenziali WhatsApp non trovate nei segreti di GitHub.")
        return

    messaggio_codificato = urllib.parse.quote_plus(messaggio)
    url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_PHONE}&text={messaggio_codificato}&apikey={WHATSAPP_APIKEY}"
    
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            print("Richiesta di notifica WhatsApp inviata con successo!")
        else:
            print(f"Errore invio notifica WhatsApp. Status: {response.status_code}, Risposta: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"ERRORE: Eccezione durante la richiesta a CallMeBot: {e}")

def estrai_prezzo(testo_prezzo):
    """Estrae il valore numerico del prezzo da una stringa."""
    if not testo_prezzo: return None
    numeri = re.findall(r'\d+\.?\d*', testo_prezzo.replace(',', '.'))
    return float(numeri[0]) if numeri else None

# --- FUNZIONE PRINCIPALE DI SCRAPING ---

def esegui_ricerca(config_ricerca):
    """Esegue lo scraping del sito Subito.it per una specifica ricerca."""
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
        
        # Questa funzione previene il rilevamento da parte di siti come Subito.it
        stealth(driver, languages=["it-IT", "it"], vendor="Google Inc.", platform="Win32")
        
        print(f"Navigazione verso: {config_ricerca['url']}")
        driver.get(config_ricerca['url'])

        try:
            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accetta')]"))).click()
            print("Banner dei cookie accettato.")
            time.sleep(2)
        except TimeoutException:
            print("Banner dei cookie non trovato o già accettato.")
        
        driver.execute_script("window.scrollTo(0, 1000);")
        time.sleep(2)

        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="SmallCard-module_card__"]')))
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

            # Filtri dinamici basati sulla configurazione
            if (not all(kw in titolo for kw in config_ricerca['keyword_da_includere'])) or \
               (any(kw in titolo for kw in config_ricerca['keyword_da_escludere'])) or \
               (prezzo_val is not None and prezzo_val > config_ricerca['budget_massimo']) or \
               ('venduto' in prezzo_str.lower()):
                continue
            
            annuncio_obj = { "titolo": titolo_tag.text.strip(), "prezzo": prezzo_str, "link": link_tag.get('href') }
            if annuncio_obj['link'] not in [a['link'] for a in annunci_filtrati]:
                 annunci_filtrati.append(annuncio_obj)
        
        return annunci_filtrati
    
    finally:
        if driver:
            driver.quit()
            print(f"--- Browser chiuso per: {config_ricerca['nome_ricerca']} ---")

# --- SCRIPT PRINCIPALE ---
if __name__ == "__main__":
    print("========================================")
    print("Avvio ricerca automatica multicanale...")
    print("========================================")
    
    errori_verificati = []
    for ricerca_config in CONFIGURAZIONE_RICERCHE:
        try:
            nome_ricerca = ricerca_config["nome_ricerca"]
            file_cronologia = ricerca_config["file_cronologia"]
            
            link_precedenti = carica_link_precedenti(file_cronologia)
            print(f"[{nome_ricerca}] Trovati {len(link_precedenti)} link nella cronologia.")
            
            annunci_attuali = esegui_ricerca(ricerca_config)
            
            if annunci_attuali is not None:
                link_attuali = set(ann['link'] for ann in annunci_attuali)
                link_nuovi = link_attuali - link_precedenti
                
                if not link_nuovi:
                    print(f"[{nome_ricerca}] Nessun nuovo annuncio trovato.")
                else:
                    print(f"[{nome_ricerca}] Trovati {len(link_nuovi)} nuovi annunci!")
                    messaggio = f"🎮 Trovati {len(link_nuovi)} nuovi annunci per {nome_ricerca}! 🎉\n\n"
                    for annuncio in annunci_attuali:
                        if annuncio['link'] in link_nuovi:
                            messaggio += f"🆕 {annuncio['titolo']}\n"
                            messaggio += f"   Prezzo: {annuncio['prezzo']}\n"
                            messaggio += f"   Link: {annuncio['link']}\n\n"
                    
                    invia_notifica_whatsapp(messaggio)
                
                salva_link_attuali(file_cronologia, link_attuali)
            
            print(f"[{nome_ricerca}] Ricerca completata con successo.")

        except Exception as e:
            nome_ricerca_errore = ricerca_config.get('nome_ricerca', 'N/D')
            tipo_errore = type(e).__name__
            print(f"!!! ERRORE durante la ricerca per '{nome_ricerca_errore}': {tipo_errore} !!!")
            errori_verificati.append(f"'{nome_ricerca_errore}': {tipo_errore}")
            # Continua con la prossima ricerca anche se una fallisce
            continue
    
    # Invia un unico report degli errori alla fine, se ce ne sono stati
    if errori_verificati:
        messaggio_errore = f"🤖 Ciao Alessandro, alcune ricerche sono fallite. 😵\n\nDettagli errori:\n" + "\n".join(errori_verificati)
        invia_notifica_whatsapp(messaggio_errore)

    print("\n========================================")
    print("Tutte le ricerche sono state completate.")
    print("========================================")
