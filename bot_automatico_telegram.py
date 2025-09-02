# -*- coding: utf-8 -*-
"""
Subito.it monitor – Playwright, locator-based (anti-timeout, anti-shadow DOM)

- Estrazione annunci con Playwright locator (no BeautifulSoup su DOM statico)
- Pattern URL estesi: /annunci/, /vi/, link assoluti
- Scroll progressivo; stop quando si raggiunge una soglia
- Cookie banner: root + iframe Usercentrics
- Dump diagnostici: screenshot + HTML se non trova card
- YAML cercato in: root, .github/workflows/, .github/workfloes/

Dipendenze: playwright, pyyaml, requests
"""

import os
import re
import time
from typing import Dict, List
import requests

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_CANDIDATES = [
    os.path.join(BASE_DIR, "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workflows", "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workfloes", "bot_annunci.yml"),  # tollera il refuso
]

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
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled",
        "budget_massimo": 150,
        "keyword_da_includere": ["switch", "oled"],
        "keyword_da_escludere": ["riparazione", "cerco", "non funzionante"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_switch.txt"),
    },
    {
        "nome_ricerca": "PlayStation 5",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5",
        "budget_massimo": 200,
        "keyword_da_includere": ["ps5", "playstation 5", "playstation5"],
        "keyword_da_escludere": ["riparazione", "cerco", "non funzionante", "controller", "solo pad", "cover", "base"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_ps5.txt"),
    },
    {
        "nome_ricerca": "Nintendo 3DS",
        "url": "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds",
        "budget_massimo": 120,
        "keyword_da_includere": ["3ds", "nintendo 3ds"],
        "keyword_da_escludere": ["solo giochi", "solo gioco", "solo custodia", "riparazione", "cerco", "non funzionante"],
        "file_cronologia": os.path.join(BASE_DIR, "report_annunci_3ds.txt"),
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ---- YAML opzionale ----
try:
    import yaml
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
    if yaml:
        for yp in YAML_CANDIDATES:
            if os.path.exists(yp):
                try:
                    with open(yp, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    ricerche = data.get("ricerche")
                    if isinstance(ricerche, list) and ricerche:
                        print(f"[CFG] YAML caricato: {yp}")
                        return [_ensure_abs_cronofile(dict(e)) for e in ricerche if isinstance(e, dict)]
                    else:
                        print(f"[CFG] YAML vuoto: {yp} → uso default")
                        break
                except Exception as ex:
                    print(f"[CFG] YAML errore: {ex} → uso default")
                    break
    else:
        print("[CFG] pyyaml non presente: uso default")
    return [_ensure_abs_cronofile(dict(e)) for e in DEFAULT_RICERCHE]

# ---- FS ----

def carica_link_precedenti(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()

def salva_link_attuali(path: str, link_set: set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            for link in sorted(list(link_set)):
                f.write(link + "\n")
    except Exception:
        pass

# ---- Telegram ----

def invia_notifica_telegram(msg: str):
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token:
        print("[TG] Manca TELEGRAM_BOT_TOKEN")
        return
    if not chat_id:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
            r.raise_for_status()
            data = r.json()
            for item in reversed(data.get("result", [])):
                m = item.get("message") or item.get("channel_post")
                if m and m.get("chat", {}).get("id"):
                    chat_id = str(m["chat"]["id"])
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
    # root
    try:
        btn = page.locator("button:has-text('Accetta')").first
        if btn and btn.is_visible():
            btn.click(timeout=3000)
            time.sleep(0.2)
            print("[COOKIE] Accettato (root)")
            return
    except Exception:
        pass
    # iframe
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

AD_HREF_PATTERNS = [
    "/annunci/",  # classico
    "/vi/",       # variante
]

# includi anche link assoluti verso subito
ABS_HOSTS = ["https://www.subito.it", "http://www.subito.it"]


def is_ad_href(href: str) -> bool:
    if not href:
        return False
    if any(p in href for p in AD_HREF_PATTERNS):
        return True
    if any(href.startswith(h) for h in ABS_HOSTS) and any(p in href for p in AD_HREF_PATTERNS):
        return True
    return False


def collect_ads(page, min_cards=12, loops=12, pause_ms=900) -> List[Dict]:
    seen = {}
    for i in range(loops):
        # 1) usa locator che bucano lo shadow DOM
        loc = page.locator(
            "a[href*='/annunci/'], a[href*='/vi/'], a:has(h2), a:has(h3), a:has([data-testid='ad-title'])"
        )
        try:
            count = loc.count()
        except Exception:
            count = 0

        for idx in range(min(count, 150)):
            a = loc.nth(idx)
            try:
                href = a.get_attribute("href")
            except Exception:
                href = None
            if not href or not is_ad_href(href):
                continue
            # titolo
            titolo = None
            for sel in ("[data-testid='ad-title']", "h2", "h3"):
                try:
                    t = a.locator(sel).first
                    if t and t.is_visible():
                        titolo = (t.text_content() or "").strip()
                        if titolo:
                            break
                except Exception:
                    continue
            if not titolo:
                try:
                    titolo = (a.get_attribute("aria-label") or a.get_attribute("title") or "").strip()
                except Exception:
                    titolo = ""
            # prezzo
            prezzo = None
            for sel in ("[data-testid='ad-price']", "xpath=.//*[contains(text(),'€')]"):
                try:
                    p = a.locator(sel).first
                    if p and p.is_visible():
                        prezzo = (p.text_content() or "").strip()
                        if prezzo:
                            break
                except Exception:
                    continue
            seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D"})

        if len(seen) >= min_cards:
            break
        page.evaluate("window.scrollBy(0, Math.max(800, window.innerHeight));")
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

        ads = collect_ads(page, min_cards=10, loops=14, pause_ms=1000)
        if not ads:
            # Dump per debug
            sp = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
            hp = os.path.join(BASE_DIR, f"dump_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.html")
            try:
                page.screenshot(path=sp, full_page=True)
            except Exception:
                pass
            try:
                with open(hp, "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            print(f"[{nome}] Nessuna card – screenshot: {sp} – dump: {hp}")
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

        print(f"[{nome}] Card viste: {len(ads)}; nuove pertinenti: {len(out)}")
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

        # cookie upfront
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
            res = run_search(page, cfg)
            if res:
                nuovi[cfg['nome_ricerca']] = res

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
