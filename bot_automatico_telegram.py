# -*- coding: utf-8 -*-
"""
Subito.it monitor – Playwright headful – V6.0 (Mobile anti-challenge + robust link handling)
- UA/contesto mobile iPhone per ridurre i challenge anti-bot.
- Retry automatico su m.subito.it se rilevata "verifica/captcha".
- Stealth import robusto + fallback manuale (fix "'module' object is not callable").
- Selettori estesi per layout mobile (li[data-testid='result-list-item']).
- Handler rete blindato (try/except) + normalizzazione link (str|dict|list).
- Cookie accept migliorato (root + iframe UC).
- Test Telegram all'avvio per separare scraping vs invio.
"""

import os
import re
import time
import random
import json
import requests
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, urlunparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Response

# =============== CONFIG DI BASE ===============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_CANDIDATES = [
    os.path.join(BASE_DIR, "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workflows", "bot_annunci.yml"),
]

DEFAULT_RICERCHE = [
    {"nome_ricerca":"PSP","url":"https://www.subito.it/annunci-italia/vendita/usato/?q=psp","budget_massimo":120,
     "keyword_da_includere":["psp","playstation portable","psp 1000","psp 2000","psp 3000","psp street"],
     "keyword_da_escludere":["solo giochi","solo gioco","solo custodia","riparazione","cerco"],
     "solo_con_spedizione": True,
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_psp.txt")},
    {"nome_ricerca":"Switch OLED","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled","budget_massimo":300,
     "keyword_da_includere":["switch","oled"],
     "keyword_da_escludere":["riparazione","cerco","non funzionante"],
     "solo_con_spedizione": True,
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_switch.txt")},
    {"nome_ricerca":"PlayStation 5","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5","budget_massimo":600,
     "keyword_da_includere":["ps5","playstation 5","playstation5","console ps5"],
     "keyword_da_escludere":["riparazione","cerco","non funzionante","controller","solo pad","cover","base"],
     "solo_con_spedizione": True,
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_ps5.txt")},
    {"nome_ricerca":"Nintendo 3DS","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds","budget_massimo":250,
     "keyword_da_includere":["3ds","nintendo 3ds","new 3ds","new3ds","2ds"],
     "keyword_da_escludere":["solo giochi","solo gioco","solo custodia","riparazione","cerco","non funzionante"],
     "solo_con_spedizione": True,
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_3ds.txt")},
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# =============== UTILITY ===============
def _ensure_abs_cronofile(entry: Dict) -> Dict:
    fname = entry.get("file_cronologia")
    if not fname:
        safe = re.sub(r"[^a-z0-9]+","_", entry.get("nome_ricerca","ricerca").lower())
        fname = f"report_annunci_{safe}.txt"
    if not os.path.isabs(fname):
        fname = os.path.join(BASE_DIR, fname)
    entry["file_cronologia"] = fname
    entry["solo_con_spedizione"] = bool(entry.get("solo_con_spedizione", True))
    return entry

def carica_configurazione() -> List[Dict]:
    global yaml
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml:
        for yp in YAML_CANDIDATES:
            if os.path.exists(yp):
                try:
                    with open(yp,"r",encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    ricerche = data.get("ricerche")
                    if isinstance(ricerche, list) and ricerche:
                        print(f"[CFG] YAML caricato: {yp}")
                        return [_ensure_abs_cronofile(dict(e)) for e in ricerche if isinstance(e, dict)]
                    else:
                        print(f"[CFG] YAML vuoto: {yp} -> uso default")
                        break
                except Exception as ex:
                    print(f"[CFG] YAML errore: {ex} -> uso default")
                    break
    else:
        print("[CFG] pyyaml non presente: uso default")
    return [_ensure_abs_cronofile(dict(e)) for e in DEFAULT_RICERCHE]

def carica_link_precedenti(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path,"r",encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()

def salva_link_attuali(path: str, link_set: set):
    try:
        with open(path,"w",encoding="utf-8") as f:
            for link in sorted(list(link_set)):
                f.write(link+"\n")
    except Exception:
        pass

# =============== TELEGRAM ===============
def _autodetect_chat_id(token: str) -> Optional[str]:
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
        r.raise_for_status()
        data = r.json()
        for item in reversed(data.get("result", [])):
            m = item.get("message") or item.get("channel_post")
            if m and m.get("chat",{}).get("id"):
                return str(m["chat"]["id"])
    except Exception:
        return None
    return None

def invia_notifica_telegram(msg: str) -> bool:
    token = TELEGRAM_BOT_TOKEN; chat_id = TELEGRAM_CHAT_ID
    if not token:
        print("[TG][ERRORE] Manca TELEGRAM_BOT_TOKEN – impossibile inviare.")
        return False
    if not chat_id:
        chat_id = _autodetect_chat_id(token)
        if not chat_id:
            print("[TG][ERRORE] TELEGRAM_CHAT_ID non impostato e non rilevabile via getUpdates.")
            return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode":"HTML", "disable_web_page_preview":True},
            timeout=20
        )
        r.raise_for_status()
        print("[TG] Notifica inviata")
        return True
    except Exception as e:
        print(f"[TG][ERRORE] Invio fallito: {e}")
        return False

def invia_test_telegram():
    ok = invia_notifica_telegram("🤖 Test bot Subito: canale Telegram raggiungibile.")
    if not ok:
        print("[TG] Test fallito: controlla TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")

# =============== ANTI-BOT / NAVIGAZIONE ===============
AD_HREF_PATTERNS = ["/annunci/", "/annuncio", "/ann/", "/vi/", "/ad/"]
ABS_HOSTS = ["https://www.subito.it","https://m.subito.it","http://www.subito.it","http://m.subito.it"]

def is_ad_href(href) -> bool:
    """Accetta string, dict, list/tuple e normalizza; True solo se è un link annuncio valido."""
    if not href:
        return False

    # Se il backend manda strutture complesse, normalizza:
    if isinstance(href, dict):
        href = (href.get("url") or href.get("href") or href.get("canonicalUrl")
                or href.get("canonical_url") or href.get("webUrl") or href.get("path") or "")
    elif isinstance(href, (list, tuple)):
        # basta che uno degli elementi sia un link valido
        for it in href:
            if is_ad_href(it):
                return True
        return False

    if not isinstance(href, str):
        try:
            href = str(href)
        except Exception:
            return False

    href = href.strip()
    if not href:
        return False

    # link assoluto o relativo a /ann
    try:
        if any(href.startswith(h) for h in ABS_HOSTS) and any(p in href for p in AD_HREF_PATTERNS):
            return True
        if href.startswith("/") and any(p in href for p in AD_HREF_PATTERNS):
            return True
    except Exception:
        return False
    return False

def accept_cookies_if_present(page: Page):
    labels = ("Accetta", "Accetta tutto", "Accetta e chiudi", "Acconsenti", "Accept all")
    for lab in labels:
        try:
            btn = page.locator(f"button:has-text('{lab}')").first
            if btn and btn.count() and btn.is_enabled():
                btn.click(timeout=2000)
                page.wait_for_timeout(300)
                print(f"[COOKIE] Accettato (root:{lab})"); return
        except Exception:
            pass
    try:
        for frame in page.frames:
            try:
                fbtn = frame.locator("button[data-testid='uc-accept-all-button']").first
                if fbtn and fbtn.count():
                    fbtn.click(timeout=2000)
                    print("[COOKIE] Accettato (iframe UC)"); return
            except Exception:
                continue
    except Exception:
        pass

def _goto_with_challenge_retry(page: Page, url: str, timeout: int = 35000):
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    txt = (page.content() or "").lower()
    if any(k in txt for k in ["captcha", "verifica di sicurezza", "verifica che sei un umano", "challenge"]):
        print("[ANTI-BOT] Rilevata pagina di verifica. Riprovo su m.subito.it…")
        u = list(urlparse(url))
        if "subito.it" in u[1] and not u[1].startswith("m."):
            u[1] = "m.subito.it"
            mobile = urlunparse(u)
            page.wait_for_timeout(1200)
            page.goto(mobile, wait_until="domcontentloaded", timeout=timeout)

def humanize(page: Page):
    try:
        w = page.viewport_size.get("width", 390); h = page.viewport_size.get("height", 844)
    except Exception:
        w, h = 390, 844
    x = random.randint(20, min(200, w-20)); y = random.randint(40, min(300, h-20))
    try:
        page.mouse.move(x,y); page.wait_for_timeout(random.randint(200,400))
        page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight));")
        page.wait_for_timeout(random.randint(250,550))
    except Exception:
        pass

SHIPPING_TEXT_KWS = ["spedizione", "sped.", "acquisto tutelato", "tutelato", "consegna", "tuttosubito"]

def dict_has_shipping(d: Dict) -> bool:
    try:
        for k, v in d.items():
            kl = str(k).lower()
            if any(s in kl for s in ["ship","spediz","deliver","tutel"]):
                if isinstance(v, bool) and v: return True
                if isinstance(v, (int, float)) and v == 1: return True
                if isinstance(v, str) and v.strip().lower() in ("true","si","sì","yes","available","disponibile","on","1"): return True
                if isinstance(v, (list, tuple)) and len(v) > 0: return True
                if isinstance(v, dict) and dict_has_shipping(v): return True
    except Exception:
        return False
    return False

# =============== RACCOLTA ANNUNCI ===============
def collect_ads_dom(page: Page, loops=18, pause_ms=700) -> List[Dict]:
    seen = {}
    for _ in range(loops):
        loc = page.locator(
            "div[class*='items-container'] div[class*='item-card'], "
            "div[data-testid*='ad-card'], "
            "li[data-testid='result-list-item']"  # layout mobile
        )
        try:
            count = min(loc.count(), 600)
        except Exception:
            count = 0
        if count == 0:
            page.wait_for_timeout(300)
        for i in range(count):
            card = loc.nth(i)
            link_el = card.locator("a[href*='/ann'], a[data-testid='ad-link']").first
            try:
                href = link_el.get_attribute("href")
            except Exception:
                href = None
            if not is_ad_href(href):
                continue
            titolo = ""
            try:
                titolo = (card.locator("[data-testid='ad-title'], h2, h3").first.text_content() or "").strip()
            except Exception:
                pass
            if not titolo:
                try:
                    titolo = (link_el.get_attribute("aria-label") or link_el.get_attribute("title") or "").strip()
                except Exception:
                    titolo = ""
            prezzo = ""
            try:
                prezzo = (card.locator("[data-testid='ad-price'], p[class*='price']").first.text_content() or "").strip()
            except Exception:
                pass
            sped = False
            try:
                card_text = (card.inner_text() or "").lower()
                if any(kw in card_text for kw in SHIPPING_TEXT_KWS):
                    sped = True
                if not sped and card.locator("[data-testid*='tuttosubito-badge'], [class*='shipping-badge']").count() > 0:
                    sped = True
            except Exception:
                pass
            seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D", "spedizione": sped})
        if len(seen) >= 20:
            break
        page.evaluate("window.scrollBy(0, Math.max(1400, window.innerHeight));")
        page.wait_for_timeout(pause_ms)
    return list(seen.values())

def collect_ads_structured(page: Page) -> List[Dict]:
    def _maybe_price(obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k in ("price","priceLabel","price_value","priceValue","prezzo","amount","lowPrice"):
                if k in obj and obj[k]:
                    return str(obj[k])
            if obj.get("@type") in ("Offer","AggregateOffer"):
                p = obj.get("price") or obj.get("lowPrice")
                if p: return f"{p} EUR"
        return None

    def _ad_from_dict(d: Dict) -> Optional[Dict]:
        link = d.get("url") or d.get("href") or d.get("canonicalUrl") or d.get("canonical_url") or d.get("webUrl")
        # NORMALIZZA link se non è stringa
        if isinstance(link, dict):
            link = (link.get("url") or link.get("href") or link.get("canonicalUrl")
                    or link.get("canonical_url") or link.get("webUrl") or link.get("path"))
        elif isinstance(link, (list, tuple)):
            for it in link:
                if is_ad_href(it):
                    link = it
                    break

        if not link or not is_ad_href(link): 
            return None
        titolo = d.get("title") or d.get("subject") or d.get("name") or d.get("headline")
        prezzo = _maybe_price(d)
        sped = dict_has_shipping(d)
        if not prezzo and "offers" in d and isinstance(d["offers"], (dict, list)):
            if isinstance(d["offers"], dict): 
                prezzo = _maybe_price(d["offers"]) or prezzo
            else:
                for off in d["offers"]:
                    prezzo = _maybe_price(off) or prezzo
                    if not sped and isinstance(off, dict) and dict_has_shipping(off):
                        sped = True
                    if prezzo: break
        return {"link": link, "titolo": str(titolo or "(senza titolo)"), "prezzo": str(prezzo or "N/D"), "spedizione": bool(sped)}

    def _walk_collect(obj: Any, out: Dict):
        if isinstance(obj, dict):
            cand = _ad_from_dict(obj)
            if cand and cand["link"] not in out:
                out[cand["link"]] = cand
            for v in obj.values():
                _walk_collect(v, out)
        elif isinstance(obj, list):
            for it in obj:
                _walk_collect(it, out)

    out: Dict[str, Dict] = {}
    try:
        els = page.locator("script[type='application/ld+json']")
        for i in range(min(els.count(), 80)):
            try:
                raw = els.nth(i).text_content()
                if not raw: continue
                data = json.loads(raw); _walk_collect(data, out)
            except Exception:
                continue
    except Exception:
        pass
    try:
        nd = page.locator("script#__NEXT_DATA__")
        if nd and nd.count() > 0:
            raw = nd.first.text_content()
            if raw:
                data = json.loads(raw); _walk_collect(data, out)
    except Exception:
        pass
    return list(out.values())

NETWORK_BUF: Dict[str, Dict] = {}
def network_tap_on_response(resp: Response):
    try:
        body = resp.text()
        if not body or len(body) < 80:
            return
        s = body.lstrip()
        if not s or s[0] not in "[{":
            return
        data = json.loads(s)

        def _maybe_price(obj: Any) -> Optional[str]:
            if isinstance(obj, dict):
                for k in ("price","priceLabel","price_value","priceValue","prezzo","amount","lowPrice"):
                    if k in obj and obj[k]: return str(obj[k])
            return None

        def _ad_from_dict(d: Dict) -> Optional[Dict]:
            link = (d.get("url") or d.get("href") or d.get("canonicalUrl") or
                    d.get("canonical_url") or d.get("webUrl"))
            # NORMALIZZA link se non è stringa
            if isinstance(link, dict):
                link = (link.get("url") or link.get("href") or link.get("canonicalUrl")
                        or link.get("canonical_url") or link.get("webUrl") or link.get("path"))
            elif isinstance(link, (list, tuple)):
                for it in link:
                    if is_ad_href(it):
                        link = it
                        break

            if not link or not is_ad_href(link): 
                return None
            titolo = d.get("title") or d.get("subject") or d.get("name") or d.get("headline")
            prezzo = _maybe_price(d)
            sped = dict_has_shipping(d)
            return {"link": link, "titolo": str(titolo or "(senza titolo)"), "prezzo": str(prezzo or "N/D"), "spedizione": bool(sped)}

        def _walk_collect(obj: Any, out: Dict):
            if isinstance(obj, dict):
                cand = _ad_from_dict(obj)
                if cand and cand["link"] not in out:
                    out[cand["link"]] = cand
                for v in obj.values():
                    _walk_collect(v, out)
            elif isinstance(obj, list):
                for it in obj:
                    _walk_collect(it, out)

        _walk_collect(data, NETWORK_BUF)
    except Exception as e:
        # Non deve mai far crashare il loop Playwright
        print(f"[NET] Skip response ({type(e).__name__}): {e}")
        return

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()

def _match_keywords(title: str, includes: List[str], excludes: List[str]) -> bool:
    t = _norm_text(title)
    for ex in excludes or []:
        if ex and _norm_text(ex) in t:
            return False
    if includes:
        return any(_norm_text(kw) in t for kw in includes if kw)
    return True

def _parse_price_to_float(price_txt: str) -> Optional[float]:
    if not price_txt:
        return None
    m = re.search(r"(\d{1,3}(?:[\.\s]\d{3})*|\d+)(?:[,\.\s](\d{2}))?", price_txt.replace("€"," ").replace("EUR"," "))
    if not m:
        return None
    intp = m.group(1).replace(".","").replace(" ","")
    decp = m.group(2) or "00"
    try:
        return float(f"{intp}.{decp}")
    except Exception:
        return None

def enrich_shipping_from_detail(page: Page, ads: List[Dict], max_check: int = 6, per_timeout: int = 7000) -> None:
    todo = [a for a in ads if not a.get("spedizione")]
    if not todo: return
    random.shuffle(todo)
    print(f"[ENRICH] Verifico fino a {max_check} annunci sulla loro pagina per la spedizione.")
    for a in todo[:max_check]:
        url = a.get("link")
        if not url: continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=per_timeout)
            try: page.wait_for_load_state("networkidle", timeout=2500)
            except PWTimeout: pass
            buy_button = page.locator("button:has-text('Acquista')").first
            shipping_info = page.locator("div:has-text('Spedizione disponibile'), div[data-testid*='shipping-available']").first
            if (buy_button and buy_button.count()) or (shipping_info and shipping_info.count()):
                a["spedizione"] = True; continue
            txt = _norm_text(page.content())
            if any(kw in txt for kw in SHIPPING_TEXT_KWS):
                a["spedizione"] = True
        except Exception:
            continue

# =============== PIPELINE DI RICERCA ===============
def run_search(page: Page, cfg: Dict) -> List[Dict]:
    nome = cfg["nome_ricerca"]; target = cfg["url"]
    print(f"\n--- Ricerca: {nome} ---")
    NETWORK_BUF.clear()
    try:
        _goto_with_challenge_retry(page, target, timeout=35000)
        accept_cookies_if_present(page)
        page.wait_for_selector(
            "div[class*='items-container'], div[data-testid*='ad-card'], li[data-testid='result-list-item']",
            timeout=25000
        )
        print(f"[{nome}] Pagina caricata, estraggo.")
    except PWTimeout:
        sp = os.path.join(BASE_DIR, f"errore_blocco_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
        hp = os.path.join(BASE_DIR, f"dump_blocco_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.html")
        try: page.screenshot(path=sp, full_page=True)
        except Exception: pass
        try:
            with open(hp,"w",encoding="utf-8") as f: f.write(page.content())
        except Exception: pass
        print(f"[{nome}] ERRORE: Probabile blocco/CAPTCHA. Screenshot: {sp}")
        return []
    except Exception as e:
        print(f"[{nome}] Errore imprevisto: {e}")
        return []

    humanize(page)
    dom_ads    = collect_ads_dom(page)
    struct_ads = collect_ads_structured(page)
    page.wait_for_timeout(1200)
    net_ads = list(NETWORK_BUF.values())

    merged: Dict[str, Dict] = {}
    for lst in (net_ads, dom_ads, struct_ads):
        for a in lst:
            if "link" in a and is_ad_href(a["link"]):
                merged.setdefault(a["link"], a)
    ads = list(merged.values())

    if not ads:
        print(f"[{nome}] Nessun annuncio trovato dopo merge (possibile challenge invisibile).")
        return []

    print(f"[{nome}] NET:{len(net_ads)} DOM:{len(dom_ads)} JSON:{len(struct_ads)} → unici: {len(ads)}")

    before = sum(1 for a in ads if a.get("spedizione"))
    enrich_shipping_from_detail(page, ads, max_check=6)
    after = sum(1 for a in ads if a.get("spedizione"))
    if after > before:
        print(f"[{nome}] Spedizione True: prima={before} dopo={after} (+enrichment)")
    else:
        print(f"[{nome}] Spedizione True: {after} (niente extra)")

    prev = carica_link_precedenti(cfg["file_cronologia"])
    out = []
    scartati_per_filtri = 0

    for ann in ads:
        title = ann.get("titolo") or ""
        prezzo_txt = ann.get("prezzo") or ""
        price_val = _parse_price_to_float(prezzo_txt)

        if cfg.get("solo_con_spedizione", True) and not ann.get("spedizione", False):
            scartati_per_filtri += 1; continue
        if not _match_keywords(title, cfg.get("keyword_da_includere") or [], cfg.get("keyword_da_escludere") or []):
            scartati_per_filtri += 1; continue
        if (price_val is not None) and (price_val > cfg.get("budget_massimo", 9e9)):
            scartati_per_filtri += 1; continue
        if ann["link"] in prev:
            continue
        out.append(ann)

    tot_sped = sum(1 for a in ads if a.get("spedizione"))
    print(f"[{nome}] Con spedizione: {tot_sped}. Scartati per filtri/budget: {scartati_per_filtri}. Già visti: {len(ads) - len(out) - scartati_per_filtri}.")
    print(f"[{nome}] Nuovi pertinenti da notificare: {len(out)}")

    if out:
        salva_link_attuali(cfg["file_cronologia"], prev | {a["link"] for a in out})
    else:
        if tot_sped == 0 and cfg.get("solo_con_spedizione", True):
            print(f"[{nome}] Niente invio perché nessun annuncio con spedizione è passato i filtri.")
        elif scartati_per_filtri > 0:
            print(f"[{nome}] Niente invio: tutti scartati da include/exclude/budget.")
        else:
            print(f"[{nome}] Niente invio: tutti già visti.")
    return out

# =============== MAIN ===============
def main():
    print("[BOOT] Avvio bot (Playwright + Chrome headful)…")
    cfgs = carica_configurazione()
    print("[CFG] Attive:", [c["nome_ricerca"] for c in cfgs])

    # Test Telegram
    invia_test_telegram()

    with sync_playwright() as p:
        # Contesto/UA MOBILE (meno challenge)
        ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")

        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--lang=it-IT",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=ua,
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True
        )

        # Tappa analytics/ads/fonts (meno fingerprint/rumore)
        def _router(route):
            url = route.request.url
            if any(x in url for x in [".ttf",".woff",".woff2","/gtm.js","/tag/js","/analytics","googletagmanager","doubleclick"]):
                return route.abort()
            return route.continue_()
        context.route("**/*", _router)

        context.on("response", network_tap_on_response)
        page = context.new_page()

        # STEALTH robusto + fallback manuale
        use_stealth = False
        try:
            from playwright_stealth import stealth_sync as _stealth
            use_stealth = True
        except Exception:
            use_stealth = False

        if use_stealth:
            try:
                _stealth(page)
                print("[STEALTH] Ok (playwright_stealth)")
            except Exception as e:
                print(f"[STEALTH] Warning: {e} (uso fallback)")
                use_stealth = False

        if not use_stealth:
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                const origQuery = window.navigator.permissions?.query;
                if (origQuery) {
                  window.navigator.permissions.query = (p) =>
                    p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : origQuery(p);
                }
            """)

        nuovi = {}
        for cfg in cfgs:
            res = run_search(page, cfg)
            if res: nuovi[cfg["nome_ricerca"]] = res
            time.sleep(random.randint(4, 8))

        context.close(); browser.close()

    if nuovi:
        msg = "<b>Nuove offerte trovate!</b>\n\n"
        for categoria, lista in nuovi.items():
            msg += f"<b>— {categoria.upper()} —</b>\n"
            for a in sorted(lista, key=lambda x: _norm_text(x.get('titolo',''))):
                sped = " [SPEDIZIONE]" if a.get("spedizione") else ""
                msg += f"{a.get('titolo','(senza titolo)')} - <b>{a.get('prezzo','N/D')}</b>{sped}\n<a href='{a['link']}'>Vedi annuncio</a>\n\n"
        sent = invia_notifica_telegram(msg)
        if not sent:
            print("[DONE] Avevo novità ma l'invio Telegram è fallito. Vedi log [TG].")
    else:
        print("[DONE] Nessun nuovo annuncio da notificare in questa esecuzione.")

if __name__ == "__main__":
    main()
