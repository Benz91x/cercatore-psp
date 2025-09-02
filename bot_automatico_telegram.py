# -*- coding: utf-8 -*-
"""
Subito.it monitor — Playwright headful + estrazione ibrida:
- DOM (locator che attraversano shadow DOM)
- JSON strutturato (script application/ld+json + __NEXT_DATA__)
- NETWORK SNIFFER: intercetta le risposte XHR/fetch e cerca liste di annunci

In CI usa Chrome stabile headful (xvfb-run nel workflow), headers "umani", referer e stealth JS.
"""
import os, re, time, random, json, requests
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_CANDIDATES = [
    os.path.join(BASE_DIR, "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workflows", "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workfloes", "bot_annunci.yml"),  # tollera refuso
]

DEFAULT_RICERCHE = [
    {"nome_ricerca":"PSP","url":"https://www.subito.it/annunci-italia/vendita/usato/?q=psp","budget_massimo":80,
     "keyword_da_includere":["psp"],"keyword_da_escludere":["solo giochi","solo gioco","solo custodia","riparazione","cerco"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_psp.txt")},
    {"nome_ricerca":"Switch OLED","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled","budget_massimo":250,
     "keyword_da_includere":["switch","oled"],"keyword_da_escludere":["riparazione","cerco","non funzionante"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_switch.txt")},
    {"nome_ricerca":"PlayStation 5","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5","budget_massimo":400,
     "keyword_da_includere":["ps5","playstation 5","playstation5"],
     "keyword_da_escludere":["riparazione","cerco","non funzionante","controller","solo pad","cover","base"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_ps5.txt")},
    {"nome_ricerca":"Nintendo 3DS","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds","budget_massimo":180,
     "keyword_da_includere":["3ds","nintendo 3ds"],"keyword_da_escludere":["solo giochi","solo gioco","solo custodia","riparazione","cerco","non funzionante"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_3ds.txt")},
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

try:
    import yaml
except Exception:
    yaml = None

# ---------------- FS & CFG ----------------
def _ensure_abs_cronofile(entry: Dict) -> Dict:
    fname = entry.get("file_cronologia")
    if not fname:
        safe = re.sub(r"[^a-z0-9]+","_", entry.get("nome_ricerca","ricerca").lower())
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
                    with open(yp,"r",encoding="utf-8") as f:
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

# ---------------- Telegram ----------------
def invia_notifica_telegram(msg: str):
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
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode":"HTML","disable_web_page_preview":True}, timeout=20)
        r.raise_for_status(); print("[TG] Notifica inviata")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

# ---------------- Stealth JS ----------------
STEALTH_JS = r"""
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['it-IT','it','en-US','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'platform',{get:()=> 'Win32'});
Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
window.chrome = { runtime: {} };
const oq = window.navigator.permissions && window.navigator.permissions.query;
if (oq) {
  window.navigator.permissions.query = p =>
    (p && p.name === 'notifications') ? Promise.resolve({state:'granted'}) : oq(p);
}
const gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param){
  if (param===37445) return 'Intel Inc.';
  if (param===37446) return 'Intel Iris OpenGL Engine';
  return gp.apply(this, arguments);
};
"""

AD_HREF_PATTERNS = ["/annunci/","/vi/"]
ABS_HOSTS = ["https://www.subito.it","http://www.subito.it"]

def is_ad_href(href: Optional[str]) -> bool:
    if not href: return False
    if any(p in href for p in AD_HREF_PATTERNS): return True
    if any(href.startswith(h) for h in ABS_HOSTS) and any(p in href for p in AD_HREF_PATTERNS): return True
    return False

# ---------------- Helpers Playwright ----------------
def accept_cookies_if_present(page: Page):
    try:
        btn = page.locator("button:has-text('Accetta')").first
        if btn and btn.is_visible():
            btn.click(timeout=3000); time.sleep(0.2); print("[COOKIE] Accettato (root)"); return
    except Exception: pass
    try:
        for frame in page.frames:
            try:
                fbtn = frame.locator("button[data-testid='uc-accept-all-button']").first
                if fbtn and fbtn.is_visible():
                    fbtn.click(timeout=3000); print("[COOKIE] Accettato (iframe)"); return
            except Exception: continue
    except Exception: pass

def humanize(page: Page):
    w = page.viewport_size["width"]; h = page.viewport_size["height"]
    x = random.randint(50, min(400, w-50)); y = random.randint(50, min(300, h-50))
    try:
        page.mouse.move(x,y); page.wait_for_timeout(random.randint(120,300))
        page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight));")
        page.wait_for_timeout(random.randint(150,350))
    except Exception: pass

def query_from_url(url: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(url).query)
        qv = qs.get("q", [])
        return qv[0] if qv else None
    except Exception:
        return None

# ---------------- Estrazione DOM (locator) ----------------
def collect_ads_dom(page: Page, min_cards=1, loops=14, pause_ms=800) -> List[Dict]:
    seen = {}
    for _ in range(loops):
        loc = page.locator(
            "a[href*='/annunci/'], a[href*='/vi/'], a:has(h2), a:has(h3), a:has([data-testid='ad-title'])"
        )
        try: count = min(loc.count(), 300)
        except Exception: count = 0
        for i in range(count):
            a = loc.nth(i)
            try: href = a.get_attribute("href")
            except Exception: href = None
            if not is_ad_href(href): continue

            titolo = None
            for sel in ("[data-testid='ad-title']","h2","h3"):
                try:
                    t = a.locator(sel).first
                    if t and t.is_visible():
                        titolo = (t.text_content() or "").strip()
                        if titolo: break
                except Exception: continue
            if not titolo:
                try: titolo = (a.get_attribute("aria-label") or a.get_attribute("title") or "").strip()
                except Exception: titolo = ""

            prezzo = None
            for sel in ("[data-testid='ad-price']", "xpath=.//*[contains(text(),'€')]"):
                try:
                    p = a.locator(sel).first
                    if p and p.is_visible():
                        prezzo = (p.text_content() or "").strip()
                        if prezzo: break
                except Exception: continue

            seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D"})

        if len(seen) >= min_cards: break
        humanize(page); page.wait_for_timeout(pause_ms)
    return list(seen.values())

# ---------------- Estrazione STRUTTURATA ----------------
def _maybe_price(obj: Any) -> Optional[str]:
    if isinstance(obj, dict):
        for k in ("price","priceLabel","price_value","priceValue","prezzo"):
            if k in obj and obj[k]: return str(obj[k])
        if obj.get("@type") in ("Offer","AggregateOffer"):
            p = obj.get("price") or obj.get("lowPrice")
            if p: return f"{p} €"
    return None

def _ad_from_dict(d: Dict) -> Optional[Dict]:
    link = d.get("url") or d.get("href") or d.get("canonicalUrl") or d.get("canonical_url") or d.get("webUrl")
    if not link or not is_ad_href(link): return None
    titolo = d.get("title") or d.get("subject") or d.get("name") or d.get("headline")
    prezzo = _maybe_price(d)
    if not prezzo and "offers" in d and isinstance(d["offers"], (dict, list)):
        if isinstance(d["offers"], dict): prezzo = _maybe_price(d["offers"]) or prezzo
        else:
            for off in d["offers"]:
                prezzo = _maybe_price(off)
                if prezzo: break
    return {"link": link, "titolo": str(titolo or "(senza titolo)"), "prezzo": str(prezzo or "N/D")}

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

def collect_ads_structured(page: Page) -> List[Dict]:
    out: Dict[str, Dict] = {}
    # JSON-LD
    try:
        els = page.locator("script[type='application/ld+json']")
        for i in range(min(els.count(), 40)):
            try:
                raw = els.nth(i).text_content()
                if not raw: continue
                data = json.loads(raw)
                _walk_collect(data, out)
            except Exception:
                continue
    except Exception:
        pass
    # __NEXT_DATA__
    try:
        nd = page.locator("script#__NEXT_DATA__")
        if nd and nd.count() > 0:
            raw = nd.first.text_content()
            if raw:
                data = json.loads(raw)
                _walk_collect(data, out)
    except Exception:
        pass
    return list(out.values())

# ---------------- Estrazione via NETWORK ----------------

def collect_ads_network(page: Page, capture_ms: int = 3000) -> List[Dict]:
    """Intercetta risposte JSON e prova a estrarre liste di annunci.
    Salva un dump grezzo per diagnosi se non trova nulla.
    """
    buf: Dict[str, Dict] = {}

    def safe_json(resp: Response):
        try:
            ct = resp.headers.get("content-type", "").lower()
        except Exception:
            ct = ""
        if "json" not in ct:
            return None
        try:
            return resp.json()
        except Exception:
            try:
                txt = resp.text()
                return json.loads(txt)
            except Exception:
                return None

    def on_response(resp: Response):
        url = resp.url
        try:
            data = safe_json(resp)
            if not data:
                return
            # euristica: solo endpoint che sembrano di ricerca/lista
            if not any(k in url for k in ("search", "list", "listing", "ads", "results")):
                # comunque prova: alcuni endpoint hanno nomi generici
                pass
            _walk_collect(data, buf)
        except Exception:
            return

    page.on("response", on_response)
    page.wait_for_timeout(capture_ms)

    # dump se vuoto
    if not buf:
        try:
            path = os.path.join(BASE_DIR, "netdump_empty.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"note":"nessun JSON intercettato"}, f)
        except Exception:
            pass
    return list(buf.values())

# ---------------- Flow di ricerca ----------------

def simulate_search_flow(page: Page, query: str, wait_ms=12000) -> bool:
    try:
        page.goto("https://www.subito.it", wait_until="domcontentloaded", timeout=25000, referer="https://www.subito.it/")
        try: page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout: pass
        accept_cookies_if_present(page); humanize(page)
        selectors = [
            "input[placeholder*='Cosa cerchi']", "input[name='q']",
            "input[type='search']", "input[aria-label*='cerca' i]"
        ]
        box = None
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc and loc.is_visible():
                    box = loc; break
            except Exception: continue
        if not box:
            print("[FLOW] Campo di ricerca non trovato"); return False
        box.click()
        for ch in query:
            box.type(ch, delay=random.randint(30, 70))
        page.keyboard.press("Enter")
        try: page.wait_for_load_state("domcontentloaded", timeout=12000)
        except PWTimeout: pass
        try:
            page.wait_for_selector("a[href*='/annunci/'], a[href*='/vi/'], script[type='application/ld+json']", timeout=wait_ms)
            return True
        except PWTimeout:
            return False
    except Exception:
        return False

# ---------------- Esecuzione singola ricerca ----------------

def run_search(page: Page, cfg: Dict) -> List[Dict]:
    nome = cfg["nome_ricerca"]; target = cfg["url"]
    print(f"\n--- Ricerca: {nome} ---")

    # 1) URL diretto
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=25000, referer="https://www.subito.it/")
        try: page.wait_for_load_state("networkidle", timeout=12000)
        except PWTimeout: pass
        accept_cookies_if_present(page); humanize(page)
    except Exception:
        q = query_from_url(target) or cfg.get("nome_ricerca")
        print(f"[FLOW] Problema accesso diretto → simulo ricerca per '{q}'")
        if not simulate_search_flow(page, q):
            sp = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
            hp = os.path.join(BASE_DIR, f"dump_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.html")
            try: page.screenshot(path=sp, full_page=True)
            except Exception: pass
            try:
                with open(hp,"w",encoding="utf-8") as f: f.write(page.content())
            except Exception: pass
            print(f"[{nome}] Fallito anche search flow – screenshot: {sp} – dump: {hp}")
            return []

    # 2) NETWORK sniff prima di scroll (per catturare la prima risposta JSON)
    net_ads = collect_ads_network(page, capture_ms=2500)

    # 3) DOM
    dom_ads = collect_ads_dom(page, min_cards=1, loops=14, pause_ms=700)

    # 4) Strutturato
    struct_ads = []
    if len(dom_ads) < 3 and len(net_ads) < 3:
        struct_ads = collect_ads_structured(page)

    # Unione con priorità: network > DOM > strutturato
    merged: Dict[str, Dict] = {}
    for lst in (net_ads, dom_ads, struct_ads):
        for a in lst:
            merged.setdefault(a["link"], a)
    ads = list(merged.values())

    if not ads:
        sp = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
        hp = os.path.join(BASE_DIR, f"dump_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.html")
        try: page.screenshot(path=sp, full_page=True)
        except Exception: pass
        try: 
            with open(hp,"w",encoding="utf-8") as f: f.write(page.content())
        except Exception: pass
        print(f"[{nome}] Nessuna card – screenshot: {sp} – dump: {hp}")
        return []

    print(f"[{nome}] NET:{len(net_ads)} DOM:{len(dom_ads)} JSON:{len(struct_ads)} → tot unici: {len(ads)}")

    # Filtri + dedup + salvataggio cronologia
    prev = carica_link_precedenti(cfg["file_cronologia"])
    out = []
    for ann in ads:
        title_l = (ann.get("titolo") or "").lower()
        price_val = None
        price_txt = ann.get("prezzo") or ""
        if "€" in price_txt:
            m = re.findall(r"\d+[.,]?\d*", price_txt.replace(",", "."))
            price_val = float(m[0]) if m else None
        if any(kw in title_l for kw in cfg.get("keyword_da_escludere", [])): continue
        inc = cfg.get("keyword_da_includere") or []
        if inc and not any(kw in title_l for kw in inc): continue
        if (price_val is not None) and (price_val > cfg.get("budget_massimo", 9e9)): continue
        if ann["link"] in prev: continue
        out.append(ann)

    print(f"[{nome}] Nuove pertinenti: {len(out)}")
    salva_link_attuali(cfg["file_cronologia"], prev | {a["link"] for a in ads})
    return out

# ---------------- MAIN ----------------
def main():
    print("[BOOT] Avvio bot (Playwright + Chrome stable headful)…")
    cfgs = carica_configurazione()
    print("[CFG] Attive:", [c["nome_ricerca"] for c in cfgs])

    chrome_major = random.choice([121,122,123])
    UA = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
    extra_headers = {
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language":"it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests":"1",
        "Sec-Fetch-Dest":"document","Sec-Fetch-Mode":"navigate","Sec-Fetch-Site":"none","Sec-Fetch-User":"?1",
        "sec-ch-ua": f"\"Chromium\";v=\"{chrome_major}\", \"Not-A.Brand\";v=\"99\"",
        "sec-ch-ua-mobile":"?0","sec-ch-ua-platform":"\"Windows\"",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--lang=it-IT","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=UA,
            viewport={"width":1920,"height":1080},
            geolocation={"latitude":41.9028, "longitude":12.4964},
        )
        context.grant_permissions(["geolocation"])  # alcune SERP usano geo
        context.set_extra_http_headers(extra_headers)
        context.add_init_script(STEALTH_JS)

        page = context.new_page()

        # Warm-up + cookie
        try:
            page.goto("https://www.subito.it", wait_until="domcontentloaded", timeout=25000, referer="https://www.subito.it/")
            try: page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout: pass
            accept_cookies_if_present(page); humanize(page)
        except Exception: pass

        nuovi = {}
        for cfg in cfgs:
            res = run_search(page, cfg)
            if res: nuovi[cfg["nome_ricerca"]] = res

        context.close(); browser.close()

    if nuovi:
        msg = "<b>📢 Nuove offerte trovate!</b>\n\n"
        for categoria, lista in nuovi.items():
            msg += f"<b>--- {categoria.upper()} ---</b>\n"
            for a in lista:
                msg += f"{a['titolo']} — <b>{a['prezzo']}</b>\n<a href='{a['link']}'>Vedi annuncio</a>\n\n"
        invia_notifica_telegram(msg)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")

if __name__ == "__main__":
    main()
