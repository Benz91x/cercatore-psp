# -*- coding: utf-8 -*-
"""
Subito.it monitor – versione Playwright (anti-timeout CI)

Cosa cambia:
- Usa Playwright (Chromium) invece di Selenium → headless più stabile su GitHub Actions
- Selettori resilienti (href "/annunci/") + parsing con BeautifulSoup ad ogni scroll
- Gestione cookie banner (root + iframe Usercentrics)
- Scroll progressivo con stop anticipato quando si raggiungono abbastanza card
- Screenshot su fallimento per debug

Configurazione: YAML opzionale (bot_annunci.yml in **root**). Se il YAML è vuoto/assente → default embedded.
Variabili d'ambiente: TELEGRAM_BOT_TOKEN (obbl.), TELEGRAM_CHAT_ID (opz.)
"""

import os
import re
import time
import requests
import bs4
from typing import List, Dict

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(BASE_DIR, "bot_annunci.yml")

# ---- DEFAULT CONFIG (se YAML è assente/vuoto) ----
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

# ---- ENV TELEGRAM ----
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ---- YAML opzionale ----
try:
    import yaml  # pyyaml
except Exception:
    yaml = None


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
                print("[CFG] YAML caricato")
                return [_ensure_abs_cronofile(dict(e)) for e in ricerche if isinstance(e, dict)]
            print("[CFG] YAML vuoto: uso default")
        except Exception as ex:
            print(f"[CFG] YAML errore: {ex} – uso default")
    else:
        if not yaml:
            print("[CFG] pyyaml non presente: uso default")
        else:
            print("[CFG] YAML non trovato: uso default")
    return [_ensure_abs_cronofile(dict(e)) for e in DEFAULT_RICERCHE]


def carica_link_precedenti(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"[FS] Lettura {path} fallita: {e}")
        return set()


def salva_link_attuali(path: str, link_set: set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            for link in sorted(list(link_set)):
                f.write(link + "\n")
    except Exception as e:
        print(f"[FS] Scrittura {path} fallita: {e}")


def invia_notifica_telegram(msg: str):
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token:
        print("[TG] Manca TELEGRAM_BOT_TOKEN")
        return
    if not chat_id:
        # tentativo auto-discovery
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
            r.raise_for_status()
            data = r.json()
            for item in reversed(data.get("result", [])):
                msg = item.get("message") or item.get("channel_post")
                if msg and msg.get("chat", {}).get("id"):
                    chat_id = str(msg["chat"]["id"])
                    break
        except Exception:
            pass
        if not chat_id:
            print("[TG] chat_id non determinato")
            return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        print("[TG] Notifica inviata")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")


# ---- Playwright helpers ----

def accept_cookies_if_present(page):
    # 1) tentativo in root
    try:
        btn = page.locator("button:has-text('Accetta')").first
        if btn.is_visible():
            btn.click(timeout=3000)
            time.sleep(0.2)
            print("[COOKIE] Accettato (root)")
            return
    except PWTimeout:
        pass
    except Exception:
        pass
    # 2) tentativo dentro iframe Usercentrics
    try:
        for frame in page.frames:
            try:
                fbtn = frame.locator("button[data-testid='uc-accept-all-button']").first
                if fbtn and fbtn.is_visible():
                    fbtn.click(timeout=3000)
                    print("[COOKIE] Accettato (iframe)")
                    return
            except Exception:
                continue
    except Exception:
        pass


def extract_ads_from_html(html: str) -> List[Dict]:
    soup = bs4.BeautifulSoup(html, "html.parser")
    anchors = soup.select('[data-testid="listing-grid"] a[href*="/annunci/"]') or soup.select('a[href*="/annunci/"]')
    out = []
    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        title_el = a.select_one('[data-testid="ad-title"], h2, h3')
        titolo = (title_el.get_text(strip=True) if title_el else (a.get("aria-label") or a.get("title") or "")).strip() or "(senza titolo)"
        price_el = a.select_one('[data-testid="ad-price"]')
        prezzo = price_el.get_text(strip=True) if price_el else "N/D"
        out.append({"link": href, "titolo": titolo, "prezzo": prezzo})
    return out


def scroll_and_collect(page, max_loops=10, pause_ms=900) -> List[Dict]:
    seen = {}
    for _ in range(max_loops):
        html = page.content()
        for a in extract_ads_from_html(html):
            seen.setdefault(a['link'], a)
        if len(seen) >= 20:
            break
        page.evaluate("window.scrollBy(0, Math.max(700, window.innerHeight));")
        page.wait_for_timeout(pause_ms)
    return list(seen.values())


def run_search(page, cfg: Dict) -> List[Dict]:
    nome = cfg['nome_ricerca']
    print(f"\n--- Ricerca: {nome} ---")
    try:
        page.goto(cfg['url'], wait_until='domcontentloaded', timeout=30000)
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except PWTimeout:
            pass
        accept_cookies_if_present(page)

        ads = scroll_and_collect(page, max_loops=12, pause_ms=1000)
        if not ads:
            # screenshot per debug
            sp = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
            page.screenshot(path=sp, full_page=True)
            print(f"[{nome}] Nessuna card – screenshot: {sp}")
            return []

        prev = carica_link_precedenti(cfg['file_cronologia'])
        out = []
        for ann in ads:
            title_l = (ann['titolo'] or '').lower()
            price_val = None
            if ann['prezzo'] and '€' in ann['prezzo']:
                m = re.findall(r"\d+[.,]?\d*", ann['prezzo'].replace(',', '.'))
                price_val = float(m[0]) if m else None
            if any(kw in title_l for kw in cfg.get('keyword_da_escludere', [])):
                continue
            inc = cfg.get('keyword_da_includere') or []
            if inc and not any(kw in title_l for kw in inc):
                continue
            if (price_val is not None) and (price_val > cfg.get('budget_massimo', 9e9)):
                continue
            if ann['link'] in prev:
                continue
            out.append(ann)

        print(f"[{nome}] Card estratte: {len(ads)}; nuove pertinenti: {len(out)}")
        salva_link_attuali(cfg['file_cronologia'], prev | {a['link'] for a in ads})
        return out

    except Exception as e:
        sp = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
        try:
            page.screenshot(path=sp, full_page=True)
            print(f"[{nome}] Errore: {e} – screenshot: {sp}")
        except Exception:
            print(f"[{nome}] Errore: {e}")
        return []


# ---- MAIN ----

def main():
    print("[BOOT] Avvio bot (Playwright)…")
    cfgs = carica_configurazione()
    print("[CFG] Attive:", [c['nome_ricerca'] for c in cfgs])

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    nuovi = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            locale="it-IT",
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # visita home per cookie, poi ricerche
        try:
            page.goto("https://www.subito.it", wait_until='domcontentloaded', timeout=25000)
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
            except PWTimeout:
                pass
            accept_cookies_if_present(page)
        except Exception:
            pass

        for cfg in cfgs:
            results = run_search(page, cfg)
            if results:
                nuovi[cfg['nome_ricerca']] = results

        context.close()
        browser.close()

    if nuovi:
        msg = "<b>📢 Nuove offerte trovate!</b>\n\n"
        for categoria, lista in nuovi.items():
            msg += f"<b>--- {categoria.upper()} ---</b>\n"
            for a in lista:
                msg += f"{a['titolo']} — <b>{a['prezzo']}</b>\n<a href='{a['link']}'>Vedi annuncio</a>\n\n"
        invia_notifica_telegram(msg)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")


if __name__ == '__main__':
    main()
