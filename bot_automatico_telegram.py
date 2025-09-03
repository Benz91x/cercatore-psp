# -*- coding: utf-8 -*-
"""
Subito.it monitor — Playwright headful (Chrome) – V4.1
Patch critiche:
- ✅ Impostazione esplicita del flag `spedizione=True` in TUTTE le pipeline (DOM, JSON/Next, Network, Mobile)
- ✅ Network tap più tollerante (prova a parsare qualsiasi risposta che sembri JSON)
- ✅ Pattern URL ampliati: "/ann", "/ad", "/vi", "/annuncio", "/annunci/"
- ✅ Enrichment opzionale: se nessuna card ha `spedizione=True`, apre fino a 8 dettagli e cerca badge/testo
- ✅ Dedup: salva **solo** i link effettivamente notificati, così non si “bruciano” i non conformi

Progettato per GitHub Actions Ubuntu 24.04 con Chrome stabile headful via Xvfb.
"""
import os, re, time, random, json, requests
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs, urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_CANDIDATES = [
    os.path.join(BASE_DIR, "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workflows", "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workfloes", "bot_annunci.yml"),
]

DEFAULT_RICERCHE = [
    {"nome_ricerca":"PSP","url":"https://www.subito.it/annunci-italia/vendita/usato/?q=psp","budget_massimo":120,
     "keyword_da_includere":["psp","playstation portable","psp 1000","psp 2000","psp 3000","psp street"],
     "keyword_da_escludere":["solo giochi","solo gioco","solo custodia","riparazione","cerco"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_psp.txt")},
    {"nome_ricerca":"Switch OLED","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=switch+oled","budget_massimo":300,
     "keyword_da_includere":["switch","oled"],"keyword_da_escludere":["riparazione","cerco","non funzionante"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_switch.txt")},
    {"nome_ricerca":"PlayStation 5","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=ps5","budget_massimo":600,
     "keyword_da_includere":["ps5","playstation 5","playstation5","console ps5"],
     "keyword_da_escludere":["riparazione","cerco","non funzionante","controller","solo pad","cover","base"],
     "file_cronologia":os.path.join(BASE_DIR,"report_annunci_ps5.txt")},
    {"nome_ricerca":"Nintendo 3DS","url":"https://www.subito.it/annunci-italia/vendita/videogiochi/?q=nintendo+3ds","budget_massimo":250,
     "keyword_da_includere":["3ds","nintendo 3ds","new 3ds","new3ds","2ds"],
     "keyword_da_escludere":["solo giochi","solo gioco","solo custodia","riparazione","cerco","non funzionante"],
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

# ---- URL patterns
AD_HREF_PATTERNS = ["/annuci/","/annunci/","/annuncio","/ann/","/vi/","/ad/"]
ABS_HOSTS = ["https://www.subito.it","http://www.subito.it","https://m.subito.it","http://m.subito.it"]

# ---------------- Heuristics ----------------

def is_ad_href(href: Optional[str]) -> bool:
    if not href: return False
    if any(p in href for p in AD_HREF_PATTERNS): return True
    if any(href.startswith(h) for h in ABS_HOSTS) and any(p in href for p in AD_HREF_PATTERNS): return True
    return False

# ---------------- Helpers Playwright ----------------

def accept_cookies_if_present(page: Page):
    try:
        for lab in ("Accetta", "Accetta tutto", "Accetta e chiudi", "Acconsenti", "Accept all"):
            btn = page.locator(f"button:has-text('{lab}')").first
            if btn and btn.is_visible():
                btn.click(timeout=3000); time.sleep(0.2); print(f"[COOKIE] Accettato (root:{lab})"); return
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
    try:
        w = page.viewport_size.get("width", 1280); h = page.viewport_size.get("height", 800)
    except Exception:
        w, h = 1280, 800
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

# ---------------- Shipping helpers ----------------

SHIPPING_TEXT_KWS = [
    "spedizione", "sped.", "acquisto tutelato", "tutelato", "consegna"
]


def dict_has_shipping(d: Dict) -> bool:
    try:
        for k, v in d.items():
            kl = str(k).lower()
            if any(s in kl for s in ["ship","spediz","deliver","tutel"]):
                if isinstance(v, bool) and v:
                    return True
                if isinstance(v, (int, float)) and v == 1:
                    return True
                if isinstance(v, str) and v.strip():
                    if v.strip().lower() in ("true","si","sì","yes","available","disponibile","on","1"):
                        return True
                if isinstance(v, (list, tuple)) and len(v) > 0:
                    return True
                if isinstance(v, dict) and dict_has_shipping(v):
                    return True
    except Exception:
        return False
    return False

# ---------------- Estrattori ----------------

def collect_ads_dom(page: Page, min_cards=1, loops=18, pause_ms=700) -> List[Dict]:
    seen = {}
    for _ in range(loops):
        loc = page.locator(
            "a[href*='/ann'], a[href*='/vi/'], a[href*='/ad/'], a[href*='/annuncio'], a[href*='/annunci/'], "
            "a:has(h2), a:has(h3), a:has([data-testid='ad-title'])"
        )
        try: count = min(loc.count(), 500)
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
            # shipping dal testo visibile
            sped = False
            try:
                txt = (a.inner_text() or "").lower()
                if any(kw in txt for kw in SHIPPING_TEXT_KWS):
                    sped = True
            except Exception: pass
            seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D", "spedizione": sped})
        if len(seen) >= min_cards: break
        page.evaluate("window.scrollBy(0, Math.max(1400, window.innerHeight));")
        page.wait_for_timeout(pause_ms)
    return list(seen.values())


def collect_ads_structured(page: Page) -> List[Dict]:
    def _maybe_price(obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k in ("price","priceLabel","price_value","priceValue","prezzo"):
                if k in obj and obj[k]: return str(obj[k])
            if obj.get("@type") in ("Offer","AggregateOffer"):
                p = obj.get("price") or obj.get("lowPrice");
                if p: return f"{p} €"
        return None
    def _ad_from_dict(d: Dict) -> Optional[Dict]:
        link = d.get("url") or d.get("href") or d.get("canonicalUrl") or d.get("canonical_url") or d.get("webUrl")
        if not link or not is_ad_href(link): return None
        titolo = d.get("title") or d.get("subject") or d.get("name") or d.get("headline")
        prezzo = _maybe_price(d)
        sped = dict_has_shipping(d)
        if not prezzo and "offers" in d and isinstance(d["offers"], (dict, list)):
            if isinstance(d["offers"], dict): prezzo = _maybe_price(d["offers"]) or prezzo
            else:
                for off in d["offers"]:
                    prezzo = _maybe_price(off)
                    if not sped:
                        try:
                            if isinstance(off, dict) and dict_has_shipping(off):
                                sped = True
                        except Exception: pass
                    if prezzo: break
        return {"link": link, "titolo": str(titolo or "(senza titolo)"), "prezzo": str(prezzo or "N/D"), "spedizione": bool(sped)}
    def _walk_collect(obj: Any, out: Dict):
        if isinstance(obj, dict):
            cand = _ad_from_dict(obj)
            if cand and cand["link"] not in out:
                out[cand["link"]] = cand
            for v in obj.values(): _walk_collect(v, out)
        elif isinstance(obj, list):
            for it in obj: _walk_collect(it, out)

    out: Dict[str, Dict] = {}
    # JSON-LD
    try:
        els = page.locator("script[type='application/ld+json']")
        for i in range(min(els.count(), 80)):
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
                data = json.loads(raw); _walk_collect(data, out)
    except Exception:
        pass
    return list(out.values())

# ------------ GLOBAL NETWORK TAP (più tollerante) ------------
NETWORK_BUF: Dict[str, Dict] = {}


def network_tap_on_response(resp: Response):
    try:
        body = resp.text()
    except Exception:
        return
    if not body or len(body) < 80:
        return
    s = body.lstrip()
    if not s or s[0] not in "[{":
        return
    try:
        data = json.loads(s)
    except Exception:
        return
    def _maybe_price(obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            for k in ("price","priceLabel","price_value","priceValue","prezzo"):
                if k in obj and obj[k]: return str(obj[k])
        return None
    def _ad_from_dict(d: Dict) -> Optional[Dict]:
        link = d.get("url") or d.get("href") or d.get("canonicalUrl") or d.get("canonical_url") or d.get("webUrl")
        if not link or not is_ad_href(link): return None
        titolo = d.get("title") or d.get("subject") or d.get("name") or d.get("headline")
        prezzo = _maybe_price(d)
        sped = dict_has_shipping(d)
        return {"link": link, "titolo": str(titolo or "(senza titolo)"), "prezzo": str(prezzo or "N/D"), "spedizione": bool(sped)}
    def _walk_collect(obj: Any, out: Dict):
        if isinstance(obj, dict):
            cand = _ad_from_dict(obj)
            if cand and cand["link"] not in out:
                out[cand["link"]] = cand
            for v in obj.values(): _walk_collect(v, out)
        elif isinstance(obj, list):
            for it in obj: _walk_collect(it, out)
    _walk_collect(data, NETWORK_BUF)

# ------------ Regex fallback su HTML ------------
AD_REGEX = re.compile(r"https?://(?:www\.)?subito\.it/[^\s\"'<>]*/(?:ann|vi|ad)[^\s\"'<>]+", re.IGNORECASE)
REL_REGEX = re.compile(r"['\"](/(?:ann|vi|ad)[^'\"<>]+)['\"]", re.IGNORECASE)


def collect_ads_regex(page: Page) -> List[Dict]:
    html = page.content()
    candidates = set(AD_REGEX.findall(html))
    for m in REL_REGEX.findall(html):
        candidates.add(urljoin("https://www.subito.it", m))
    out = []
    for link in candidates:
        if is_ad_href(link):
            out.append({"link": link, "titolo": "(da regex)", "prezzo": "N/D", "spedizione": False})
    return out

# ------------ Enrichment: visita alcuni dettagli per riconoscere la spedizione ------------

def enrich_shipping_from_detail(page: Page, ads: List[Dict], max_check: int = 8, per_timeout: int = 6000) -> None:
    todo = [a for a in ads if not a.get("spedizione")]
    random.shuffle(todo)
    todo = todo[:max_check]
    for a in todo:
        url = a.get("link");
        if not url: continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=per_timeout)
            try: page.wait_for_load_state("networkidle", timeout=2000)
            except PWTimeout: pass
            txt = (page.content() or "").lower()
            if any(kw in txt for kw in SHIPPING_TEXT_KWS):
                a["spedizione"] = True
        except Exception:
            continue

# ------------ Mobile/RSS fallback ------------

def try_mobile_and_rss(page: Page, query: str) -> List[Dict]:
    results: Dict[str, Dict] = {}
    # Mobile
    try:
        murl = f"https://m.subito.it/annunci-italia/?q={query}"
        page.goto(murl, wait_until="domcontentloaded", timeout=20000, referer="https://m.subito.it/")
        try: page.wait_for_selector("a[href*='/ann']", timeout=5000)
        except PWTimeout: pass
        for a in page.query_selector_all("a[href*='/ann']"):
            href = a.get_attribute("href")
            if not is_ad_href(href): continue
            titolo = (a.text_content() or "").strip() or "(mobile)"
            sped = False
            try:
                if "spedizione" in (a.inner_text() or "").lower():
                    sped = True
            except Exception: pass
            results.setdefault(href, {"link": href, "titolo": titolo, "prezzo": "N/D", "spedizione": sped})
    except Exception:
        pass
    # RSS (best-effort)
    try:
        rss = f"https://www.subito.it/annunci-italia/vendita/?q={query}&format=rss"
        page.goto(rss, wait_until="domcontentloaded", timeout=15000, referer="https://www.subito.it/")
        xml = page.content()
        for link in AD_REGEX.findall(xml):
            results.setdefault(link, {"link": link, "titolo": "(rss)", "prezzo": "N/D", "spedizione": False})
    except Exception:
        pass
    return list(results.values())

# ------------ Flow di ricerca ------------

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
        if not box: print("[FLOW] Campo di ricerca non trovato"); return False
        box.click()
        for ch in query:
            box.type(ch, delay=random.randint(25, 60))
        page.keyboard.press("Enter")
        try: page.wait_for_load_state("domcontentloaded", timeout=12000)
        except PWTimeout: pass
        try:
            page.wait_for_selector("a[href*='/ann'], script[type='application/ld+json']", timeout=wait_ms)
            return True
        except PWTimeout:
            return False
    except Exception:
        return False

# ------------ Esecuzione singola ricerca ------------

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
            print(f"[{nome}] Fallito anche search flow di base → provo mobile/RSS")
            extra = try_mobile_and_rss(page, q)
            return extra

    # 2) Prima raccolta: DOM + Regex + Structured
    dom_ads   = collect_ads_dom(page, min_cards=1, loops=18, pause_ms=600)
    regex_ads = collect_ads_regex(page)
    struct_ads= collect_ads_structured(page)

    # 3) Se ancora pochi risultati, dai tempo al network (TAP globale)
    page.wait_for_timeout(1500)
    net_ads = list(NETWORK_BUF.values())

    # 4) Merge con priorità network > DOM > JSON > regex
    merged: Dict[str, Dict] = {}
    for lst in (net_ads, dom_ads, struct_ads, regex_ads):
        for a in lst:
            merged.setdefault(a["link"], a)
    ads = list(merged.values())

    if not ads:
        q = query_from_url(target) or cfg.get("nome_ricerca") or ""
        print(f"[{nome}] 0 risultati → provo mobile/RSS come fallback finale")
        ads = try_mobile_and_rss(page, q)

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

    print(f"[{nome}] NET:{len(net_ads)} DOM:{len(dom_ads)} JSON:{len(struct_ads)} REGEX:{len(regex_ads)} → tot unici: {len(ads)}")

    # Enrichment: se nessuna card ha spedizione, controlla qualche dettaglio
    before = sum(1 for a in ads if a.get("spedizione"))
    if before == 0:
        enrich_shipping_from_detail(page, ads, max_check=8)
    after = sum(1 for a in ads if a.get("spedizione"))
    print(f"[{nome}] Spedizione True: prima={before} dopo={after}")

    # Filtri + dedup + salvataggio cronologia (solo spedizione)
    prev = carica_link_precedenti(cfg["file_cronologia"])
    out = []
    for ann in ads:
        if not ann.get("spedizione", False):
            continue
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

    print(f"[{nome}] Nuove pertinenti (con spedizione): {len(out)}")
    # segna come visti solo gli annunci inviati (non quelli scartati)
    salva_link_attuali(cfg["file_cronologia"], prev | {a["link"] for a in out})
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
        )
        context.set_extra_http_headers(extra_headers)
        context.add_init_script(STEALTH_JS)

        # **GLOBAL NETWORK TAP** — prima di QUALSIASI navigazione
        context.on("response", network_tap_on_response)

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
        msg = "<b>📢 Nuove offerte trovate (con 🚚 Spedizione disponibile)!</b>\n\n"
        for categoria, lista in nuovi.items():
            msg += f"<b>--- {categoria.upper()} ---</b>\n"
            for a in lista:
                msg += f"{a['titolo']} — <b>{a['prezzo']}</b> 🚚\n<a href='{a['link']}'>Vedi annuncio</a>\n\n"
        invia_notifica_telegram(msg)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")

if __name__ == "__main__":
    main()
