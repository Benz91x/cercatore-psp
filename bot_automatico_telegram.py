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
file_cronologia: "report_annunci_3ds.txt" # opzionale: se relativo, verrà auto-prepended con BASE_DIR


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
import yaml # pyyaml
except Exception:
yaml = None # se non presente, useremo le configurazioni di default


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
print("Esecuzione completata.")
