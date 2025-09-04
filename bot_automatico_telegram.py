# -*- coding: utf-8 -*-
"""
PSP finder – Subito.it
Target: https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true
Regole:
  - Solo annunci con spedizione disponibile
  - Prezzo <= 50 €
  - Notifica solo annunci nuovi (vs report_annunci_psp.txt)
"""

import os, re, json, time, random, requests
from typing import Dict, List, Any, Optional
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Response

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TARGET_URL   = "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true"
HISTORY_PATH = os.path.join(BASE_DIR, "report_annunci_psp.txt")
BUDGET_MAX   = 50.0
BASE_HOST    = "https://www.subito.it"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# ---------------- Utils FS / Telegram ----------------
def load_history(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except Exception:
        return set()

def save_history(path: str, links: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            for u in sorted(links): f.write(u + "\n")
    except Exception:
        pass

def send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("[TG] TELEGRAM_BOT_TOKEN mancante; skip."); return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", timeout=10)
            r.raise_for_status()
            for it in reversed(r.json().get("result", [])):
                m = it.get("message") or it.get("channel_post")
                if m and m.get("chat", {}).get("id"):
                    chat_id = str(m["chat"]["id"]); break
        except Exception: pass
        if not chat_id:
            print("[TG] TELEGRAM_CHAT_ID mancante; skip."); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20
        )
        r.raise_for_status(); print("[TG] Notifica inviata.")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

# ---------------- Normalizzazioni ----------------
def normalize_url(href: Optional[str]) -> Optional[str]:
    if not href: return None
    href = href.strip()
    if href.startswith("http"): return href
    if href.startswith("/"):   return urljoin(BASE_HOST, href)
    return href

AD_RE_ABS = re.compile(r"https?://(?:www\.)?subito\.it/(?:vi/\d+|[a-z0-9\-]+/.+?-\d+\.htm)\b", re.I)
AD_RE_REL = re.compile(r"^/(?:vi/\d+|[a-z0-9\-]+/.+?-\d+\.htm)\b", re.I)

def is_ad_href(href: Optional[str]) -> bool:
    if not href: return False
    h = href.strip()
    return bool(AD_RE_ABS.search(h) or AD_RE_REL.search(h))

def euros_to_float(txt: str) -> Optional[float]:
    if not txt: return None
    t = txt.replace(".", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    return float(m.group(1)) if m else None

# ---------------- Cookie / “human” ----------------
def accept_cookies(page: Page):
    # root
    for lab in ("Accetta", "Accetta tutto", "Acconsenti", "Accept all"):
        try:
            b = page.locator(f"button:has-text('{lab}')").first
            if b and b.is_visible(): b.click(timeout=2000); print(f"[COOKIE] Accettato: {lab}"); return
        except Exception: pass
    # iframe IAB
    try:
        for fr in page.frames:
            try:
                b = fr.locator("button[data-testid='uc-accept-all-button']").first
                if b and b.is_visible(): b.click(timeout=2000); print("[COOKIE] Accettato (iframe)"); return
            except Exception: continue
    except Exception: pass

def human_scroll(page: Page, loops=8, pause_ms=350):
    for _ in range(loops):
        try:
            page.evaluate("window.scrollBy(0, Math.max(800, window.innerHeight));")
            page.wait_for_timeout(pause_ms)
        except Exception: break

# ---------------- Estrattori ----------------
def _first_value(values: Any) -> Optional[str]:
    try:
        if isinstance(values, list) and values:
            v = values[0]
            if isinstance(v, dict): return str(v.get("value") or v.get("key") or "").strip()
    except Exception: pass
    return None

def _is_shippable(features: Dict[str, Any]) -> bool:
    try:
        for k in ("/item_shippable", "/item_shipping_allowed"):
            if k in features:
                v = _first_value(features[k].get("values"))
                if str(v).lower() in ("1", "true", "sì", "si"): return True
        for k in features.keys():
            if "item_shipping_type" in k or "shipping_cost" in k: return True
    except Exception: pass
    return False

def _is_sold(features: Dict[str, Any]) -> bool:
    try:
        if "/transaction_status" in features:
            v = _first_value(features["/transaction_status"].get("values"))
            return (v or "").upper() == "SOLD"
    except Exception: pass
    return False

def extract_from_nextdata(raw_json: str) -> List[Dict]:
    try:
        data = json.loads(raw_json)
    except Exception:
        return []
    out: Dict[str, Dict] = {}
    def walk(obj: Any):
        if isinstance(obj, dict):
            if "features" in obj and "urls" in obj and isinstance(obj["urls"], dict):
                if _is_sold(obj["features"]): return
                link  = obj["urls"].get("default") or obj["urls"].get("mobile")
                title = obj.get("subject") or obj.get("title") or obj.get("name") or "(senza titolo)"
                price = None
                if "/price" in obj["features"]:
                    price = _first_value(obj["features"]["/price"].get("values"))
                    if price and "€" not in price: price = f"{price} €"
                ad = {
                    "link": link,
                    "titolo": str(title),
                    "prezzo": price or "N/D",
                    "spedizione": _is_shippable(obj["features"]),
                }
                if is_ad_href(link): out.setdefault(link, ad)
            for v in obj.values(): walk(v)
        elif isinstance(obj, list):
            for it in obj: walk(it)
    walk(data)
    return list(out.values())

def collect_dom(page: Page) -> List[Dict]:
    seen: Dict[str, Dict] = {}
    loc = page.locator("a[href*='.htm'], a[href*='/vi/']")
    try: n = min(loc.count(), 600)
    except Exception: n = 0
    for i in range(n):
        a = loc.nth(i)
        try: href = normalize_url(a.get_attribute("href"))
        except Exception: href = None
        if not is_ad_href(href): continue
        titolo = ""
        for sel in ("[data-testid='ad-title']", "h2", "h3", "span"):
            try:
                el = a.locator(sel).first
                if el and el.is_visible():
                    titolo = (el.text_content() or "").strip()
                    if titolo: break
            except Exception: pass
        if not titolo:
            try: titolo = (a.get_attribute("aria-label") or a.get_attribute("title") or "").strip()
            except Exception: pass
        # best effort per il prezzo
        prezzo = None
        try:
            tx = (a.inner_text() or "")
            m = list(re.finditer(r"\d+(?:[.,]\d{1,2})?\s*€", tx))
            if m: prezzo = m[-1].group(0)
        except Exception: pass
        seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D", "spedizione": True})  # shp=true
    return list(seen.values())

NETWORK_BAG: Dict[str, Dict] = {}
def on_response(resp: Response):
    try:
        ct = (resp.headers or {}).get("content-type", "").lower()
    except Exception:
        ct = ""
    if "json" not in ct: return
    try: body = resp.text()
    except Exception: return
    if not body or len(body) < 100: return
    try: data = json.loads(body)
    except Exception: return
    # riusa la stessa logica del nextdata
    for ad in extract_from_nextdata(json.dumps(data)):
        NETWORK_BAG.setdefault(ad["link"], ad)

# ---------------- MAIN ----------------
def main():
    print("[BOOT] PSP finder…")
    prev = load_history(HISTORY_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,  # su Actions: xvfb-run -a python -u bot_automatico_telegram.py
            args=["--lang=it-IT", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.choice([123,124,125])}.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
        )
        context.on("response", on_response)
        page = context.new_page()

        # Naviga, cookie, scroll per caricare lazy-cards
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=7000)
        except PWTimeout: pass
        accept_cookies(page)
        human_scroll(page)

        # 1) __NEXT_DATA__
        ads_struct = []
        try:
            nd = page.locator("script#__NEXT_DATA__").first
            if nd and nd.count() > 0:
                raw = nd.text_content()
                if raw: ads_struct = extract_from_nextdata(raw)
        except Exception: pass

        # 2) Network tap (se la pagina fetcha altro JSON)
        time.sleep(0.6)
        ads_net = list(NETWORK_BAG.values())

        # 3) DOM (con link .htm)
        ads_dom = collect_dom(page)

        # merge
        merged: Dict[str, Dict] = {}
        for lst in (ads_net, ads_struct, ads_dom):
            for a in lst:
                if a.get("link"): merged.setdefault(a["link"], a)
        ads = list(merged.values())

        print(f"[DBG] NET:{len(ads_net)} STRUCT:{len(ads_struct)} DOM:{len(ads_dom)} → tot unici:{len(ads)}")

        # Filtri finali (spedizione già vera: shp=true/feature), PSP nel titolo, prezzo <= 50, non in storico
        nuovi = []
        for a in ads:
            if not a.get("spedizione", False): continue
            if "psp" not in (a.get("titolo") or "").lower(): continue
            price = euros_to_float(a.get("prezzo") or "")
            if price is None or price > BUDGET_MAX: continue
            if a["link"] in prev: continue
            nuovi.append(a)

        print(f"[DBG] Nuovi pertinenti ≤ 50€: {len(nuovi)}")

        # debug dump utile se qualcosa va storto
        try:
            page.screenshot(path=os.path.join(BASE_DIR, "debug_psp.png"), full_page=True)
            with open(os.path.join(BASE_DIR, "debug_psp.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception: pass

        context.close(); browser.close()

    if nuovi:
        # aggiorno storico PRIMA dell'invio per evitare doppioni su retry
        prev |= {x["link"] for x in nuovi}
        save_history(HISTORY_PATH, prev)

        msg = "<b>📢 Nuove PSP con spedizione ≤ 50€</b>\n\n"
        for a in nuovi:
            msg += f"• {a['titolo']} — <b>{a['prezzo']}</b>\n{a['link']}\n\n"
        send_telegram(msg)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")

if __name__ == "__main__":
    main()
