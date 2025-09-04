# -*- coding: utf-8 -*-
"""
PSP finder – Subito.it listing parser focalizzato su:
https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true

Regole:
- Solo annunci con spedizione disponibile
- Prezzo <= 50 €
- Notifica solo annunci nuovi (vs report_annunci_psp.txt)

Funziona in GitHub Actions con Chrome headful (xvfb-run).
"""
import os, re, json, time, random, requests
from typing import Dict, List, Any, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(BASE_DIR, "report_annunci_psp.txt")

TARGET_URL = "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true"
BUDGET_MAX = 50.0

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# ---------------- Utils FS ----------------
def load_history(path: str) -> set:
    if not os.path.exists(path): return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()

def save_history(path: str, links: set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            for link in sorted(links):
                f.write(link + "\n")
    except Exception:
        pass

# ---------------- Telegram ----------------
def send_telegram(msg: str):
    token = TELEGRAM_BOT_TOKEN; chat_id = TELEGRAM_CHAT_ID
    if not token:
        print("[TG] Manca TELEGRAM_BOT_TOKEN"); return
    if not chat_id:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10); r.raise_for_status()
            data = r.json()
            for item in reversed(data.get("result", [])):
                m = item.get("message") or item.get("channel_post")
                if m and m.get("chat",{}).get("id"):
                    chat_id = str(m["chat"]["id"]); break
        except Exception: pass
        if not chat_id:
            print("[TG] chat_id non determinato"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True},
            timeout=20
        )
        r.raise_for_status()
        print("[TG] Notifica inviata")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

# ---------------- Helpers UI ----------------
STEALTH_JS = r"""
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['it-IT','it','en-US','en']});
Object.defineProperty(navigator,'platform',{get:()=> 'Win32'});
window.chrome = { runtime: {} };
"""

def accept_cookies(page: Page):
    # bottone principale
    try:
        btn = page.locator("button:has-text('Accetta')").first
        if btn and btn.is_visible():
            btn.click(timeout=3000)
            print("[COOKIE] Accettato: Accetta")
            return
    except Exception:
        pass
    # fallback iframe (UC)
    try:
        for fr in page.frames:
            try:
                fbtn = fr.locator("button[data-testid='uc-accept-all-button']").first
                if fbtn and fbtn.is_visible():
                    fbtn.click(timeout=3000)
                    print("[COOKIE] Accettato (iframe UC)")
                    return
            except Exception:
                continue
    except Exception:
        pass

# ---------------- Parsing __NEXT_DATA__ ----------------
def _first_value(values: Any) -> Optional[str]:
    """Estrae il .get('value') o .get('key') dal formato features['/price']['values'][0]."""
    try:
        if isinstance(values, list) and values:
            v = values[0]
            if isinstance(v, dict):
                return str(v.get("value") or v.get("key") or "").strip()
    except Exception:
        pass
    return None

def _num_from_price_text(txt: str) -> Optional[float]:
    if not txt: return None
    t = txt.replace(".", "").replace(",", ".")
    m = re.findall(r"\d+(?:\.\d+)?", t)
    return float(m[0]) if m else None

def _is_shippable(features: Dict[str, Any]) -> bool:
    # true se: /item_shippable == 1 OR /item_shipping_allowed == 1 OR presente /item_shipping_type
    try:
        if "/item_shippable" in features:
            v = _first_value(features["/item_shippable"].get("values"))
            if v in ("1", "true", "True", "Sì"): return True
        if "/item_shipping_allowed" in features:
            v = _first_value(features["/item_shipping_allowed"].get("values"))
            if v in ("1", "true", "True", "Sì"): return True
        if "/item_shipping_type" in features:
            return True
    except Exception:
        pass
    return False

def _is_sold(features: Dict[str, Any]) -> bool:
    try:
        if "/transaction_status" in features:
            v = _first_value(features["/transaction_status"].get("values"))
            return (v or "").upper() == "SOLD"
    except Exception:
        pass
    return False

def extract_ads_from_nextdata(raw_json: str) -> List[Dict]:
    """
    Cammina tutto __NEXT_DATA__ e raccoglie oggetti che sembrano AdItem:
    - link: urls.default o urls.mobile
    - titolo: subject
    - prezzo: features['/price']
    - spedizione: da features
    """
    try:
        data = json.loads(raw_json)
    except Exception:
        return []
    collected: Dict[str, Dict] = {}

    def walk(obj: Any):
        if isinstance(obj, dict):
            # pattern AdItem "piatto": ha 'features' e 'urls' e magari 'subject'
            if "features" in obj and "urls" in obj and isinstance(obj["urls"], dict):
                features = obj.get("features", {})
                if _is_sold(features):   # salta annunci marcati SOLD
                    return
                link = obj["urls"].get("default") or obj["urls"].get("mobile")
                if link and "/annunci/" in link:
                    title = obj.get("subject") or obj.get("title") or obj.get("name") or "(senza titolo)"
                    price_txt = None
                    if "/price" in features:
                        price_txt = _first_value(features["/price"].get("values"))
                        # normalizza con € se manca
                        if price_txt and "€" not in price_txt:
                            price_txt = f"{price_txt} €"
                    ad = {
                        "link": link,
                        "titolo": str(title),
                        "prezzo": price_txt or "N/D",
                        "spedizione": _is_shippable(features),
                    }
                    collected.setdefault(link, ad)
            # continua cammino
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for it in obj: walk(it)

    walk(data)
    return list(collected.values())

# ---------------- MAIN ----------------
def main():
    print("[BOOT] PSP finder…")
    prev = load_history(HISTORY_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--lang=it-IT","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.choice([121,122,123])}.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        context.add_init_script(STEALTH_JS)
        page = context.new_page()

        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=25000)
            try: page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout: pass
            accept_cookies(page)
        except Exception:
            print("[ERR] Navigazione iniziale fallita")
            context.close(); browser.close()
            return

        # estrai __NEXT_DATA__
        next_el = page.locator("script#__NEXT_DATA__").first
        ads: List[Dict] = []
        if next_el and next_el.count() > 0:
            raw = next_el.text_content()
            if raw:
                ads = extract_ads_from_nextdata(raw)

        # Fallback molto leggero: anche listing-schema (AggregateOffer) NON contiene i link singoli → inutile.
        tot = len(ads)
        # Filtra: spedizione, PSP nel titolo, prezzo <= 50, non già inviati
        nuovi: List[Dict] = []
        for a in ads:
            if not a.get("spedizione", False):
                continue
            title_l = (a.get("titolo") or "").lower()
            if "psp" not in title_l:
                continue
            price_val = _num_from_price_text(a.get("prezzo") or "")
            if price_val is None or price_val > BUDGET_MAX:
                continue
            if a["link"] in prev:
                continue
            nuovi.append(a)

        print(f"[DBG] NET:0 STRUCT:{tot} DOM:0 → tot unici:{tot}")
        print(f"[DBG] Nuovi pertinenti ≤ 50€: {len(nuovi)}")

        # dump di supporto
        try:
            page.screenshot(path=os.path.join(BASE_DIR, "debug_psp.png"), full_page=True)
            with open(os.path.join(BASE_DIR, "debug_psp.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"[DBG] Dump salvato: {os.path.join(BASE_DIR,'debug_psp.png')} — {os.path.join(BASE_DIR,'debug_psp.html')}")
        except Exception:
            pass

        context.close(); browser.close()

    if nuovi:
        # aggiorna storico PRIMA di inviare (così eviti doppioni in eventuali retry)
        new_links = prev | {x["link"] for x in nuovi}
        save_history(HISTORY_PATH, new_links)

        # notifica
        msg = "<b>📢 Nuove PSP con spedizione ≤ 50€</b>\n\n"
        for a in nuovi:
            msg += f"• {a['titolo']} — <b>{a['prezzo']}</b>\n<a href='{a['link']}'>Apri annuncio</a>\n\n"
        send_telegram(msg)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")

if __name__ == "__main__":
    main()
