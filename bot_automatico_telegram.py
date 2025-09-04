# -*- coding: utf-8 -*-
"""
PSP finder — Subito (spedizione) – robust single-target crawler
Target: https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true
Criterio: titolo compatibile con PSP e prezzo ≤ 50 €
Pipeline: __NEXT_DATA__/JSON-LD  →  Network JSON  →  DOM (con scroll)
Debug: se 0 risultati, salva screenshot + dump HTML in repo.
"""

import os, re, json, time, random, requests
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Response

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TARGET_URL = "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true"
HISTORY    = os.path.join(BASE_DIR, "report_annunci_psp.txt")
BUDGET_MAX = 50.0
BASE_HOST  = "https://www.subito.it"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# ------------------- utils -------------------
def euros_to_float(txt: str) -> Optional[float]:
    if not txt: return None
    # esempi: "49 €", "49,00 €", "€ 49", "49€"
    m = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)", txt.replace("\xa0", " "))
    if not m: return None
    s = m.group(1).replace(".", "").replace(",", ".")
    try: return float(s)
    except Exception: return None

def is_psp_title(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in ("psp", "playstation portable"))

def normalize_url(href: Optional[str]) -> Optional[str]:
    if not href: return None
    href = href.strip()
    if href.startswith("http"):
        return href
    # relative → assoluto
    if href.startswith("/"):
        return urljoin(BASE_HOST, href)
    return href

def load_history(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except Exception:
        return set()

def save_history(path: str, urls: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            for u in sorted(urls): f.write(u + "\n")
    except Exception: pass

def send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("[TG] TELEGRAM_BOT_TOKEN mancante; skip.")
        return
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
            print("[TG] TELEGRAM_CHAT_ID mancante; skip.")
            return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20
        )
        r.raise_for_status(); print("[TG] Notifica inviata.")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

# ------------------- cookie & humanize -------------------
def accept_cookies(page: Page):
    # root
    for lab in ("Accetta", "Accetta tutto", "Accetta e chiudi", "Acconsenti", "Accept all"):
        try:
            btn = page.locator(f"button:has-text('{lab}')").first
            if btn and btn.is_visible():
                btn.click(timeout=2000); time.sleep(0.2); print(f"[COOKIE] Accettato: {lab}"); return
        except Exception: pass
    # iFrame IAB
    try:
        for fr in page.frames:
            try:
                b = fr.locator("button[data-testid='uc-accept-all-button']").first
                if b and b.is_visible():
                    b.click(timeout=2000); print("[COOKIE] Accettato in iframe"); return
            except Exception: continue
    except Exception: pass

def human_scroll(page: Page, loops=10, pause_ms=500):
    for _ in range(loops):
        try:
            page.mouse.move(random.randint(50, 400), random.randint(40, 300))
            page.evaluate("window.scrollBy(0, Math.max(800, window.innerHeight));")
            page.wait_for_timeout(pause_ms)
        except Exception:
            break

# ------------------- collectors -------------------
def walk_collect(obj: Any, bag: Dict[str, Dict]) -> None:
    """Raccoglie (link, titolo, prezzo) da blob JSON annidati (__NEXT_DATA__/API)."""
    if isinstance(obj, dict):
        link  = obj.get("url") or obj.get("href") or obj.get("canonicalUrl") or obj.get("webUrl")
        title = obj.get("title") or obj.get("subject") or obj.get("name") or obj.get("headline")
        price = obj.get("price") or obj.get("priceLabel") or obj.get("price_value") or obj.get("priceValue")
        if isinstance(price, dict):  # normalizza oggetti prezzo
            price = price.get("value") or price.get("amount") or price.get("price")
        link = normalize_url(link)
        if link and title and ("/ann" in link or "/vi/" in link):
            bag.setdefault(link, {"link": link, "titolo": str(title), "prezzo": str(price) if price else "N/D"})
        for v in obj.values(): walk_collect(v, bag)
    elif isinstance(obj, list):
        for it in obj: walk_collect(it, bag)

def collect_structured(page: Page) -> List[Dict]:
    out: Dict[str, Dict] = {}
    # __NEXT_DATA__
    try:
        nd = page.locator("script#__NEXT_DATA__").first
        if nd:
            raw = nd.text_content()
            if raw: walk_collect(json.loads(raw), out)
    except Exception: pass
    # JSON-LD
    try:
        els = page.locator("script[type='application/ld+json']")
        n = min(els.count(), 60)
        for i in range(n):
            try:
                raw = els.nth(i).text_content()
                if raw: walk_collect(json.loads(raw), out)
            except Exception: continue
    except Exception: pass
    return list(out.values())

NETWORK_BAG: Dict[str, Dict] = {}
def on_response(resp: Response):
    # prova a parsare qualsiasi risposta che sembri JSON
    try:
        body = resp.text()
    except Exception:
        return
    if not body or len(body) < 80: return
    s = body.lstrip()
    if s[0] not in "[{": return
    try:
        data = json.loads(s)
    except Exception:
        return
    walk_collect(data, NETWORK_BAG)

def collect_dom(page: Page) -> List[Dict]:
    seen: Dict[str, Dict] = {}
    loc = page.locator("a[href*='/ann'], a[href*='/vi/']")
    try:
        cnt = min(loc.count(), 500)
    except Exception:
        cnt = 0
    for i in range(cnt):
        a = loc.nth(i)
        try:
            href = normalize_url(a.get_attribute("href"))
        except Exception:
            href = None
        if not href or ("/ann" not in href and "/vi/" not in href):
            continue
        # titolo
        titolo = ""
        for sel in ("[data-testid='ad-title']", "h2", "h3", "span"):
            try:
                el = a.locator(sel).first
                if el and el.is_visible():
                    titolo = (el.text_content() or "").strip()
                    if titolo: break
            except Exception: continue
        if not titolo:
            try:
                titolo = (a.get_attribute("aria-label") or a.get_attribute("title") or "").strip()
            except Exception: pass
        # prezzo (best effort)
        prezzo = None
        try:
            tx = (a.inner_text() or "")
            m_all = list(re.finditer(r"\d+(?:[.,]\d{1,2})?\s*€", tx))
            if m_all: prezzo = m_all[-1].group(0)
        except Exception: pass
        seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D"})
    return list(seen.values())

# ------------------- main -------------------
def main():
    print("[BOOT] PSP finder…")
    prev = load_history(HISTORY)

    UA = (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.choice([123,124,125])}.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # headful riduce falsi negativi; su Actions usare xvfb-run
            args=[
                "--lang=it-IT",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        context.on("response", on_response)
        page = context.new_page()

        # Navigazione
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=7000)
        except PWTimeout: pass
        accept_cookies(page)
        human_scroll(page, loops=10, pause_ms=350)  # forza lazy-load

        # 1) strutturati
        ads_struct = collect_structured(page)
        # 2) network
        time.sleep(0.8)
        ads_net = list(NETWORK_BAG.values())
        # 3) dom
        ads_dom = collect_dom(page)

        # merge (priorità: net > struct > dom)
        merged: Dict[str, Dict] = {}
        for lst in (ads_net, ads_struct, ads_dom):
            for a in lst:
                if not a.get("link"): continue
                merged.setdefault(a["link"], a)
        ads = list(merged.values())

        print(f"[DBG] NET:{len(ads_net)} STRUCT:{len(ads_struct)} DOM:{len(ads_dom)} → tot unici:{len(ads)}")

        # filtri: titolo PSP + prezzo ≤ 50 (spedizione è già in URL)
        out = []
        for a in ads:
            if not is_psp_title(a.get("titolo") or ""):
                continue
            price_f = euros_to_float(a.get("prezzo") or "")
            if price_f is None or price_f > BUDGET_MAX:
                continue
            link = a.get("link")
            if not link or link in prev:
                continue
            out.append(a)

        print(f"[DBG] Nuovi pertinenti ≤ 50€: {len(out)}")

        # debug se 0
        if len(ads) == 0 or (len(ads) > 0 and len(out) == 0):
            try:
                sp = os.path.join(BASE_DIR, "debug_psp.png")
                hp = os.path.join(BASE_DIR, "debug_psp.html")
                page.screenshot(path=sp, full_page=True)
                with open(hp, "w", encoding="utf-8") as f: f.write(page.content())
                print(f"[DBG] Dump salvato: {sp} — {hp}")
            except Exception: pass

        context.close(); browser.close()

    # Notifica & storia
    if out:
        msg = "<b>📣 PSP ≤ 50€ (con spedizione)</b>\n\n"
        for a in out:
            msg += f"• {a['titolo']} — <b>{a['prezzo']}</b>\n{a['link']}\n\n"
        send_telegram(msg)
        prev |= {a["link"] for a in out}
        save_history(HISTORY, prev)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")

if __name__ == "__main__":
    main()
