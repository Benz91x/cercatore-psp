Di seguito trovi **tutti i file completi** da scaricare e sostituire nella repo, già pronti per includere la ricerca **Nintendo 3DS**, la configurazione via **YAML**, e l’esecuzione schedulata su **GitHub Actions**.

---

## 1) `bot_automatico_telegram.py`

```python
# -*- coding: utf-8 -*-
"""
Bot di monitoraggio Subito.it con:
- Aggiunta ricerca **Nintendo 3DS** (report_annunci_3ds.txt)
- Supporto opzionale a configurazione esterna via YAML (bot_annunci.yml)
- Compatibile con repository esistente: usa BASE_DIR per file di cronologia

Schema YAML atteso (facoltativo):

ricerche:
  - nome_ricerca: "Nintendo 3DS"
    url: "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds&shp=true"
    budget_massimo: 120
    keyword_da_includere: ["3ds", "nintendo 3ds"]
    keyword_da_escludere: ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco", "non funzionante"]
    file_cronologia: "report_annunci_3ds.txt"  # opzionale: se relativo, verrà auto-prepended con BASE_DIR

Variabili d'ambiente richieste:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID (opzionale; se mancante, viene ricavato con getUpdates)
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
from selenium_stealth import stealth

# --- NEW: Supporto YAML ---
try:
    import yaml  # pyyaml
except Exception:
    yaml = None  # se non presente, useremo le configurazioni di default

# --- PERCORSI ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_CANDIDATES = [
    os.path.join(BASE_DIR, "bot_annunci.yml"),
    os.path.join(BASE_DIR, "config", "bot_annunci.yml"),
]

# --- CONFIGURAZIONI DI DEFAULT (inclusa Nintendo 3DS) ---
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
    # --- NEW: Nintendo 3DS ---
    {
        "nome_ricerca": "Nintendo 3DS",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds&shp=true",
        "budget_massimo": 120,
        "keyword_da_includere": ["3ds", "nintendo 3ds"],
        "keyword_da_escludere": ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco", "non funzionante"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_3ds.txt"),
    },
]

# --- VARIABILI D'AMBIENTE TELEGRAM ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- FUNZIONI UTILI ---

def carica_link_precedenti(nome_file: str):
    if not os.path.exists(nome_file):
        return set()
    try:
        with open(nome_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except IOError as e:
        print(f"Errore durante la lettura del file {nome_file}: {e}")
        return set()


def salva_link_attuali(nome_file: str, link_set):
    try:
        with open(nome_file, "w", encoding="utf-8") as f:
            for link in sorted(list(link_set)):
                f.write(link + "\n")
    except IOError as e:
        print(f"Errore durante la scrittura del file {nome_file}: {e}")


def estrai_prezzo(testo_prezzo: str):
    if not testo_prezzo:
        return None
    numeri = re.findall(r"\d+[.,]?\d*", testo_prezzo.replace(",", "."))
    return float(numeri[0]) if numeri else None


def get_chat_id_from_updates(token, timeout=10):
    print("TELEGRAM_CHAT_ID non impostato. Provo a recuperarlo automaticamente...")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        for item in reversed(data.get("result", [])):
            msg = item.get("message") or item.get("channel_post")
            if msg and "chat" in msg and "id" in msg["chat"]:
                chat_id = str(msg["chat"]["id"])
                print(f"Trovato chat_id: {chat_id}")
                return chat_id
    except requests.exceptions.RequestException as e:
        print(f"Errore durante la chiamata a getUpdates: {e}")
    print("Non sono riuscito a determinare il chat_id. Assicurati di aver inviato almeno un messaggio al bot.")
    return None


def invia_notifica_telegram(messaggio: str):
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
    payload = {"chat_id": chat_id, "text": messaggio, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=20)
        r.raise_for_status()
        print("Notifica Telegram inviata con successo.")
    except requests.exceptions.RequestException as e:
        print(f"Errore durante l'invio della notifica Telegram: {e}")


# --- NEW: Loader YAML ---

def _ensure_abs_cronofile(entry: dict) -> dict:
    """Rende assoluto il path del file_cronologia se è relativo."""
    fname = entry.get("file_cronologia")
    if not fname:
        # default: genera nome dal nome_ricerca
        safe = re.sub(r"[^a-z0-9]+", "_", entry.get("nome_ricerca", "ricerca").lower())
        fname = f"report_annunci_{safe}.txt"
    if not os.path.isabs(fname):
        fname = os.path.join(BASE_DIR, fname)
    entry["file_cronologia"] = fname
    return entry


def carica_configurazione():
    # Se esiste un YAML valido con lista "ricerche", lo usa; altrimenti fallback ai DEFAULT_RICERCHE
    if yaml is None:
        print("pyyaml non disponibile: uso configurazioni di default.")
        return [dict(_ensure_abs_cronofile(e)) for e in DEFAULT_RICERCHE]

    for yml_path in YAML_CANDIDATES:
        if os.path.exists(yml_path):
            print(f"Carico configurazione da YAML: {yml_path}")
            try:
                with open(yml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                ricerche = data.get("ricerche")
                if isinstance(ricerche, list) and ricerche:
                    # normalizza i path dei file di cronologia
                    norm = []
                    for e in ricerche:
                        if not isinstance(e, dict):
                            continue
                        # merge con default minimi
                        entry = {
                            "nome_ricerca": e.get("nome_ricerca", "SenzaNome"),
                            "url": e.get("url", ""),
                            "budget_massimo": e.get("budget_massimo", 999999),
                            "keyword_da_includere": e.get("keyword_da_includere", []),
                            "keyword_da_escludere": e.get("keyword_da_escludere", []),
                            "file_cronologia": e.get("file_cronologia"),
                        }
                        norm.append(_ensure_abs_cronofile(entry))
                    return norm
                else:
                    print("Sezione 'ricerche' non trovata nel YAML o vuota. Uso configurazioni di default.")
                    return [dict(_ensure_abs_cronofile(e)) for e in DEFAULT_RICERCHE]
            except Exception as ex:
                print(f"Errore nel parsing YAML: {ex}. Uso configurazioni di default.")
                return [dict(_ensure_abs_cronofile(e)) for e in DEFAULT_RICERCHE]
    # se nessun YAML trovato
    print("YAML non trovato. Uso configurazioni di default.")
    return [dict(_ensure_abs_cronofile(e)) for e in DEFAULT_RICERCHE]


# --- FUNZIONE DI SCRAPING ---

def esegui_ricerca(driver, config_ricerca):
    print(f"\n--- Avvio scraping per: {config_ricerca['nome_ricerca']} ---")
    try:
        driver.get(config_ricerca["url"])
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class*="SmallCard-module_card__"]'))
        )
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        soup = bs4.BeautifulSoup(driver.page_source, "html.parser")
        ad_cards = soup.select('a[class*="SmallCard-module_link__"]')

        if not ad_cards:
            print("Nessun annuncio trovato sulla pagina.")
            return []

        annunci_filtrati = []
        link_visti = set()

        for card in ad_cards:
            titolo_tag = card.find("h2")
            prezzo_tag = card.find("p", class_=lambda x: x and "price" in x.lower())
            if not titolo_tag:
                continue

            link = card.get("href")
            if not link or link in link_visti:
                continue
            link_visti.add(link)

            titolo = titolo_tag.text.strip().lower()
            prezzo_str = prezzo_tag.text.strip() if prezzo_tag else "N/D"
            prezzo_val = estrai_prezzo(prezzo_str)

            if "venduto" in prezzo_str.lower():
                continue
            if any(kw in titolo for kw in config_ricerca.get("keyword_da_escludere", [])):
                continue
            if config_ricerca.get("keyword_da_includere") and not any(kw in titolo for kw in config_ricerca["keyword_da_includere"]):
                continue
            if prezzo_val is not None and prezzo_val > config_ricerca.get("budget_massimo", 9e9):
                continue

            annunci_filtrati.append({
                "titolo": titolo_tag.text.strip(),
                "prezzo": prezzo_str,
                "link": link,
            })

        print(f"Trovati {len(annunci_filtrati)} annunci pertinenti per '{config_ricerca['nome_ricerca']}'.")
        return annunci_filtrati

    except TimeoutException:
        print(f"Timeout durante l'attesa degli annunci per '{config_ricerca['nome_ricerca']}'.")
        return []
    except Exception as e:
        print(f"Errore imprevisto durante lo scraping di '{config_ricerca['nome_ricerca']}': {e}")
        screenshot_path = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', config_ricerca['nome_ricerca'].lower())}.png")
        try:
            driver.save_screenshot(screenshot_path)
            print(f"Screenshot dell'errore salvato in '{screenshot_path}'")
        except Exception as se:
            print(f"Impossibile salvare lo screenshot: {se}")
        return []


# --- SCRIPT PRINCIPALE ---
if __name__ == "__main__":
    print("Avvio bot per monitoraggio annunci Subito.it...")

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = None
    nuovi_annunci_complessivi = {}
    errori = []

    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        stealth(
            driver,
            languages=["it-IT", "it"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )

        print("Accettazione banner cookie (se presente)...")
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
            print("Banner cookie non trovato o già accettato.")

        # --- NEW: Caricamento configurazioni ---
        CONFIGURAZIONE_RICERCHE = carica_configurazione()
        print(f"Configurazioni attive: {[c['nome_ricerca'] for c in CONFIGURAZIONE_RICERCHE]}")

        for cfg in CONFIGURAZIONE_RICERCHE:
            link_precedenti = carica_link_precedenti(cfg["file_cronologia"])
            print(f"[{cfg['nome_ricerca']}] Caricati {len(link_precedenti)} link dalla cronologia.")

            annunci_attuali_obj = esegui_ricerca(driver, cfg)
            if not annunci_attuali_obj:
                continue

            link_attuali = set(ann["link"] for ann in annunci_attuali_obj)
            link_nuovi = link_attuali - link_precedenti

            if link_nuovi:
                annunci_da_notificare = [ann for ann in annunci_attuali_obj if ann["link"] in link_nuovi]
                nuovi_annunci_complessivi[cfg["nome_ricerca"]] = annunci_da_notificare
                print(f"[{cfg['nome_ricerca']}] TROVATI {len(link_nuovi)} NUOVI ANNUNCI!")

            print(f"[{cfg['nome_ricerca']}] Aggiornamento cronologia con {len(link_attuali)} link totali...")
            salva_link_attuali(cfg["file_cronologia"], link_attuali)

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

    print("Esecuzione completata.")
```

---

## 2) `bot_annunci.yml` (consigliato, gestione ricerche via YAML)

> Posizionalo nella **root** della repo (o in `config/bot_annunci.yml`). Se presente, lo script userà questo file invece dei default.

```yaml
ricerche:
  - nome_ricerca: "PSP"
    url: "https://www.subito.it/annunci-italia/vendita/usato/?q=psp"
    budget_massimo: 50
    keyword_da_includere: ["psp"]
    keyword_da_escludere: ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco"]
    file_cronologia: "report_annunci_psp.txt"

  - nome_ricerca: "Switch OLED"
    url: "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled&shp=true"
    budget_massimo: 150
    keyword_da_includere: ["switch", "oled"]
    keyword_da_escludere: ["riparazione", "cerco", "non funzionante"]
    file_cronologia: "report_annunci_switch.txt"

  - nome_ricerca: "PlayStation 5"
    url: "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5&shp=true"
    budget_massimo: 200
    keyword_da_includere: ["ps5", "playstation 5", "playstation5"]
    keyword_da_escludere: ["riparazione", "cerco", "non funzionante", "controller", "solo pad", "cover", "base"]
    file_cronologia: "report_annunci_ps5.txt"

  - nome_ricerca: "Nintendo 3DS"
    url: "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds&shp=true"
    budget_massimo: 120
    keyword_da_includere: ["3ds", "nintendo 3ds"]
    keyword_da_escludere: ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco", "non funzionante"]
    file_cronologia: "report_annunci_3ds.txt"
```

---

## 3) `requirements.txt`

```text
selenium
selenium-stealth
beautifulsoup4
pyyaml
requests
```

> Nota: su GitHub Actions useremo **Selenium Manager** (da Selenium ≥ 4.6) per gestire il driver automaticamente; ci basta installare **Google Chrome**.

---

## 4) Workflow GitHub Actions: `.github/workflows/bot_annunci.yml`

```yaml
name: Bot annunci Subito

on:
  schedule:
    - cron: "*/30 7-21 * * 1-6"  # ogni 30 minuti 07:00-21:59, lun-sab (regola modificabile)
  workflow_dispatch: {}

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Google Chrome (stable)
        run: |
          sudo apt-get update
          sudo apt-get install -y wget gpg
          wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google-linux-signing-keyring.gpg
          echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
          sudo apt-get update
          sudo apt-get install -y google-chrome-stable

      - name: Install Python deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run bot
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}  # opzionale
        run: |
          python bot_automatico_telegram.py
```

---

## 5) `.gitignore` (facoltativo ma consigliato)

```gitignore
# file generati a runtime
report_annunci_*.txt
errore_*.png
__pycache__/
*.log
```

---

### Istruzioni operative (succinte)

1. **Sostituisci** `bot_automatico_telegram.py` con quello sopra.
2. **Aggiungi** `bot_annunci.yml` in root (o `config/bot_annunci.yml`).
3. **Aggiorna**/aggiungi `requirements.txt`.
4. **Crea** la cartella `.github/workflows/` e inserisci `bot_annunci.yml` del workflow.
5. In **Settings → Secrets and variables → Actions**, imposta almeno `TELEGRAM_BOT_TOKEN` (opzionale `TELEGRAM_CHAT_ID`).
6. Avvia manualmente da **Actions → Run workflow** per il primo test; poi penserà al resto il **cron**.

Se desideri, posso personalizzare il cron (fasce orarie/ritmo), i budget per console o le keyword di esclusione per minimizzare falsi positivi.
