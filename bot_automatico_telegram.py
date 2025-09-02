# -*- coding: utf-8 -*-
"""
Subito.it monitor – Playwright + Stealth (anti-WAF Akamai)

- Playwright (Chromium) + header “umani” e referer coerente
- Stealth: navigator.webdriver, languages, plugins, chrome.runtime, WebGL vendor/renderer, permissions
- Interazioni minime (scroll/mouse) per ridurre fingerprinting
- Fallback config: se bot_annunci.yml è assente/vuoto usa DEFAULT_RICERCHE
- Proxy opzionale via env PROXY_URL (es. http://user:pass@host:port)

Dipendenze: playwright, pyyaml, requests, (opzionale) playwright-stealth
"""
import os, re, time, random, requests
from typing import Dict, List
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_CANDIDATES = [
    os.path.join(BASE_DIR, "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workflows", "bot_annunci.yml"),
    os.path.join(BASE_DIR, ".github", "workfloes", "bot_annunci.yml"),  # tollera refuso
]

# ---------- DEFAULT CONFIG ----------
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

# ---------- ENV ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PROXY_URL = os.environ.get("PROXY_URL")  # es. http://user:pass@host:port

# ---------- YAML opzionale ----------
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

# ---------- FS ----------
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

# ---------- Telegram ----------
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
            data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        print("[TG] Notifica inviata")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

# ---------- Stealth ----------
STEALTH_JS = r"""
// webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// languages
Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it','en-US','en']});

// plugins
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});

// platform & hardware
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});

// chrome runtime
window.chrome = { runtime: {} };

// permissions
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: 'granted' })
      : originalQuery(parameters)
  );
}

// WebGL vendor/renderer
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
  if (parameter === 37445) return 'Intel Inc.'; // UNMASKED_VENDOR_WEBGL
  if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
  return getParameter.apply(this, arguments);
};
"""

AD_HREF_PATTERNS = ["/annunci/", "/vi/"]
ABS_HOSTS = ["https://www.subito.it", "http://www.subito.it"]

def is_ad_href(href: str) -> bool:
    if not href:
        return False
    if any(p in href for p in AD_HREF_PATTERNS):
        return True
    if any(href.startswith(h) for h in ABS_HOSTS) and any(p in href for p in AD_HREF_PATTERNS):
        return True
    return False

# ---------- Playwright helpers ----------
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

def humanize(page):
    # piccoli movimenti/scroll casuali
    w = page.viewport_size["width"]
    h = page.viewport_size["height"]
    x = random.randint(50, min(400, w-50))
    y = random.randint(50, min(300, h-50))
    try:
        page.mouse.move(x, y)
        page.wait_for_timeout(random.randint(150, 350))
        page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight));")
        page.wait_for_timeout(random.randint(200, 400))
    except Exception:
        pass

def collect_ads(page, min_cards=10, loops=12, pause_ms=900):
    seen = {}
    for _ in range(loops):
        # locator-based (attraversa shadow DOM)
        loc = page.locator(
            "a[href*='/annunci/'], a[href*='/vi/'], a:has(h2), a:has(h3), a:has([data-testid='ad-title'])"
        )
        try:
            count = min(loc.count(), 200)
        except Exception:
            count = 0

        for i in range(count):
            a = loc.nth(i)
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

        humanize(page)
        page.wait_for_timeout(pause_ms)
    return list(seen.values())

def run_search(page, cfg: Dict) -> List[Dict]:
    nome = cfg["nome_ricerca"]
    print(f"\n--- Ricerca: {nome} ---")
    try:
        # referer esplicito + headers coerenti
        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30000, referer="https://www.subito.it/")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        accept_cookies_if_present(page)
        humanize(page)

        ads = collect_ads(page, min_cards=12, loops=14, pause_ms=1000)
        if not ads:
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

        prev = carica_link_precedenti(cfg["file_cronologia"])
        out = []
        for ann in ads:
            title_l = (ann["titolo"] or "").lower()
            price_val = None
            if ann["prezzo"] and "€" in ann["prezzo"]:
                m = re.findall(r"\d+[.,]?\d*", ann["prezzo"].replace(",", "."))
                price_val = float(m[0]) if m else None
            if any(kw in title_l for kw in cfg.get("keyword_da_escludere", [])):
                continue
            inc = cfg.get("keyword_da_includere") or []
            if inc and not any(kw in title_l for kw in inc):
                continue
            if (price_val is not None) and (price_val > cfg.get("budget_massimo", 9e9)):
                continue
            if ann["link"] in prev:
                continue
            out.append(ann)

        print(f"[{nome}] Card viste: {len(ads)}; nuove pertinenti: {len(out)}")
        salva_link_attuali(cfg["file_cronologia"], prev | {a["link"] for a in ads})
        return out

    except Exception as e:
        sp = os.path.join(BASE_DIR, f"errore_{re.sub(r'[^a-z0-9]+','_', nome.lower())}.png")
        try:
            page.screenshot(path=sp, full_page=True)
            print(f"[{nome}] Errore: {e} – screenshot: {sp}")
        except Exception:
            print(f"[{nome}] Errore: {e}")
        return []

# ---------- MAIN ----------
def main():
    print("[BOOT] Avvio bot (Playwright + Stealth)…")
    cfgs = carica_configurazione()
    print("[CFG] Attive:", [c["nome_ricerca"] for c in cfgs])

    # UA credibile (Chrome major 121–123 random)
    chrome_major = random.choice([121, 122, 123])
    UA = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"

    extra_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        # I client hints spesso sono gestiti internamente; aggiungerli qui non danneggia:
        "sec-ch-ua": "\"Chromium\";v=\"{}\", \"Not:A-Brand\";v=\"99\"".format(chrome_major),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
    }

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--lang=it-IT", "--disable-blink-features=AutomationControlled"]
        }
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
        )
        context.set_extra_http_headers(extra_headers)
        context.add_init_script(STEALTH_JS)

        # Se disponibile, usa playwright-stealth (non obbligatorio)
        try:
            from playwright_stealth import stealth_sync  # type: ignore
            stealth_enabled = True
        except Exception:
            stealth_enabled = False

        page = context.new_page()
        if stealth_enabled:
            try:
                stealth_sync(page)
                print("[STEALTH] playwright-stealth applicato")
            except Exception:
                print("[STEALTH] playwright-stealth non applicato (fallback JS)")

        # cookie upfront
        try:
            page.goto("https://www.subito.it", wait_until="domcontentloaded", timeout=25000, referer="https://www.subito.it/")
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PWTimeout:
                pass
            accept_cookies_if_present(page)
            humanize(page)
        except Exception:
            pass

        nuovi = {}
        for cfg in cfgs:
            res = run_search(page, cfg)
            if res:
                nuovi[cfg["nome_ricerca"]] = res

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

if __name__ == "__main__":
    main()
