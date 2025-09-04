# -*- coding: utf-8 -*-
"""
PSP finder – Subito.it (spedizione + anti-doppioni + skip VENDUTO)
Target: https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true
Regole:
  - Solo annunci con spedizione disponibile
  - Prezzo <= 50 €
  - Esclude annunci segnati VENDUTO (overlay UI o flag nel payload)
  - Notifica solo annunci NUOVI rispetto a report_annunci_psp.txt
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

# ---------------- Utilità base ----------------
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

def euros_to_float(txt: str) -> Optional[float]:
    if not txt: return None
    t = txt.replace(".", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    return float(m.group(1)) if m else None

def has_venduto_text(s: str) -> bool:
    t = (s or "").lower()
    return any(k in t for k in ("venduto", "venduta", "sold"))

# ---------------- Normalizza URL annuncio ----------------
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

# ---------------- Cookie & scroll ----------------
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

def _is_sold(features: Dict[str, Any]) -> bool:
    # SOLD dal payload
    try:
        if "/transaction_status" in features:
            v = _first_value(features["/transaction_status"].get("values"))
            if (v or "").upper() == "SOLD": return True
        if "/availability" in features:
            v = _first_value(features["/availability"].get("values"))
            if str(v).lower() in ("sold", "venduto"): return True
    except Exception: pass
    return False

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

def extract_from_nextdata(raw_json: str) -> List[Dict]:
    try:
        data = json.loads(raw_json)
    except Exception:
        return []
    out: Dict[str, Dict] = {}

    def walk(obj: Any):
        if isinstance(obj, dict):
            if "features" in obj and "urls" in obj and isinstance(obj["urls"], dict):
                features = obj["features"]
                link  = obj["urls"].get("default") or obj["urls"].get("mobile")
                title = obj.get("subject") or obj.get("title") or obj.get("name") or "(senza titolo)"
                price = None
                if "/price" in features:
                    price = _first_value(features["/price"].get("values"))
                    if price and "€" not in price: price = f"{price} €"
                ad = {
                    "link": link,
                    "titolo": str(title),
                    "prezzo": price or "N/D",
                    "spedizione": _is_shippable(features),
                    "venduto": _is_sold(features) or has_venduto_text(price or "") or has_venduto_text(title),
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

        inner = ""
        try: inner = (a.inner_text() or "")
        except Exception: pass

        # prezzo best-effort
        prezzo = None
        m = list(re.finditer(r"\d+(?:[.,]\d{1,2})?\s*€", inner))
        if m: prezzo = m[-1].group(0)

        seen.setdefault(href, {
            "link": href,
            "titolo": titolo or "(senza titolo)",
            "prezzo": prezzo or "N/D",
            "spedizione": True,               # siamo su shp=true
            "venduto": has_venduto_text(inner) or has_venduto_text(prezzo or "") or has_venduto_text(titolo),
        })
    return list(seen.values())

NETWORK_BAG: Dict[str, Dict] = {}
def on_response(resp: Response):
    # intercetta JSON e riusa il parser di __NEXT_DATA__
    try:
        ct = (resp.headers or {}).get("content-type", "").lower()
    except Exception: ct = ""
    if "json" not in ct: return
    try: body = resp.text()
    except Exception: return
    if not body or len(body) < 80: return
    s = body.lstrip()
    if s[0] not in "[{": return
    try: data = json.loads(s)
    except Exception: return
    for ad in extract_from_nextdata(json.dumps(data)):
        NETWORK_BAG.setdefault(ad["link"], ad)

# ---------------- MAIN ----------------
def main():
    print("[BOOT] PSP finder…")
    prev = load_history(HISTORY_PATH)

    UA = (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.choice([123,124,125])}.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # su Actions: xvfb-run -a python -u bot_automatico_telegram.py
            args=["--lang=it-IT", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
        )
        context.on("response", on_response)
        page = context.new_page()

        # Navigazione, cookie, scroll (lazy cards)
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

        # 2) Network
        time.sleep(0.6)
        ads_net = list(NETWORK_BAG.values())

        # 3) DOM
        ads_dom = collect_dom(page)

        # Merge
        merged: Dict[str, Dict] = {}
        for lst in (ads_net, ads_struct, ads_dom):
            for a in lst:
                if a.get("link"): merged.setdefault(a["link"], a)
        ads = list(merged.values())

        print(f"[DBG] NET:{len(ads_net)} STRUCT:{len(ads_struct)} DOM:{len(ads_dom)} → tot unici:{len(ads)}")

        # Filtri finali
        nuovi: List[Dict] = []
        for a in ads:
            if not a.get("spedizione", False):                 # deve avere spedizione
                continue
            if a.get("venduto", False):                        # flag venduto dai dati
                continue
            if has_venduto_text(a.get("prezzo","")) or has_venduto_text(a.get("titolo","")):
                continue                                       # venduto nel testo UI
            if "psp" not in (a.get("titolo") or "").lower():   # pertinenza
                continue
            price = euros_to_float(a.get("prezzo") or "")
            if price is None or price > BUDGET_MAX:            # budget
                continue
            if a["link"] in prev:                              # solo nuovi
                continue
            nuovi.append(a)

        print(f"[DBG] Nuovi pertinenti ≤ 50€: {len(nuovi)}")

        # Debug dump sempre utile
        try:
            page.screenshot(path=os.path.join(BASE_DIR, "debug_psp.png"), full_page=True)
            with open(os.path.join(BASE_DIR, "debug_psp.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception: pass

        context.close(); browser.close()

    if nuovi:
        # aggiorno lo storico PRIMA di inviare: evita doppioni su retry
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
