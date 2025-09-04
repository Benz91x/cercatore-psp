# -*- coding: utf-8 -*-
"""
PSP finder – Subito.it (solo spedizione) – Run standalone per GitHub Actions
Target unico:
  https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true
Criterio: prezzo <= 50 €, titolo pertinente.

Approccio:
- Playwright Chromium headless con UA/locale realistici.
- Estrazione robusta da __NEXT_DATA__ e JSON-LD (walk ricorsivo).
- Fallback DOM (ancore verso /ann/ /vi/ con titolo/prezzo).
- De-dup per run successivi via file locale in repo.
- Notifica Telegram tramite variabili d'ambiente TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.

Nota: il filtro spedizione è già applicato da `shp=true`.
"""

import os, re, json, random, time, requests
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TARGET_URL = "https://www.subito.it/annunci-italia/vendita/videogiochi/?q=psp&shp=true"
HISTORY    = os.path.join(BASE_DIR, "report_annunci_psp.txt")
BUDGET_MAX = 50.0

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

# ---------------- Utilità ----------------
def load_history(path: str) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except Exception:
        return set()

def save_history(path: str, urls: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            for u in sorted(urls):
                f.write(u + "\n")
    except Exception:
        pass

def send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("[TG] TELEGRAM_BOT_TOKEN mancante; salto invio.")
        return
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        # Best-effort: ricava chat_id dall’ultimo update
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", timeout=10)
            r.raise_for_status()
            data = r.json()
            for it in reversed(data.get("result", [])):
                m = it.get("message") or it.get("channel_post")
                if m and m.get("chat", {}).get("id"):
                    chat_id = str(m["chat"]["id"])
                    break
        except Exception:
            pass
        if not chat_id:
            print("[TG] TELEGRAM_CHAT_ID mancante; salto invio.")
            return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        r.raise_for_status()
        print("[TG] Notifica inviata.")
    except Exception as e:
        print(f"[TG] Invio fallito: {e}")

def euros_to_float(txt: str) -> Optional[float]:
    if not txt:
        return None
    # Prende la prima cifra plausibile (es. "49,00 €" -> 49.00)
    m = re.search(r"(\d+(?:[.,]\d{1,2})?)", txt.replace("\xa0", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
    except Exception:
        return None

def is_psp_title(title: str) -> bool:
    t = (title or "").lower()
    return "psp" in t or "playstation portable" in t

def looks_like_ad_url(href: Optional[str]) -> bool:
    if not href:
        return False
    href = href.strip()
    return ("/ann" in href or "/vi/" in href) and "subito.it" in href

# ---------------- Estrattori ----------------
def walk_collect(obj: Any, bag: Dict[str, Dict]) -> None:
    """Cerca oggetti che sembrano annunci in blob JSON annidati."""
    if isinstance(obj, dict):
        link = obj.get("url") or obj.get("href") or obj.get("canonicalUrl") or obj.get("webUrl")
        title = obj.get("title") or obj.get("subject") or obj.get("name") or obj.get("headline")
        price = obj.get("price") or obj.get("priceLabel") or obj.get("price_value") or obj.get("priceValue")
        if isinstance(price, dict):
            price = price.get("value") or price.get("amount") or price.get("price")
        if link and looks_like_ad_url(link) and title:
            bag.setdefault(link, {"link": link, "titolo": str(title), "prezzo": str(price) if price else "N/D"})
        for v in obj.values():
            walk_collect(v, bag)
    elif isinstance(obj, list):
        for it in obj:
            walk_collect(it, bag)

def collect_from_structured(page: Page) -> List[Dict]:
    out: Dict[str, Dict] = {}
    # __NEXT_DATA__
    try:
        nd = page.locator("script#__NEXT_DATA__").first
        if nd and nd.is_visible():
            raw = nd.text_content()
            if raw:
                data = json.loads(raw)
                walk_collect(data, out)
    except Exception:
        pass
    # JSON-LD
    try:
        els = page.locator("script[type='application/ld+json']")
        n = min(els.count(), 60)
        for i in range(n):
            try:
                raw = els.nth(i).text_content()
                if not raw:
                    continue
                data = json.loads(raw)
                walk_collect(data, out)
            except Exception:
                continue
    except Exception:
        pass
    return list(out.values())

def collect_from_dom(page: Page) -> List[Dict]:
    seen: Dict[str, Dict] = {}
    loc = page.locator("a[href*='/ann'], a[href*='/vi/']")
    count = 0
    try:
        count = min(loc.count(), 400)
    except Exception:
        pass
    for i in range(count):
        a = loc.nth(i)
        try:
            href = a.get_attribute("href")
        except Exception:
            href = None
        if not looks_like_ad_url(href):
            continue
        # prova a raccogliere titolo/prezzo
        titolo = ""
        for sel in ("[data-testid='ad-title']", "h2", "h3", "span", "*"):
            try:
                node = a.locator(sel).first
                if node and node.is_visible():
                    titolo = (node.text_content() or "").strip()
                    if titolo:
                        break
            except Exception:
                continue
        if not titolo:
            try:
                titolo = (a.get_attribute("aria-label") or a.get_attribute("title") or "").strip()
            except Exception:
                pass
        prezzo = None
        try:
            txt = (a.inner_text() or "")
            if "€" in txt:
                # prendi ultimo match euro, di solito è il prezzo
                m_all = list(re.finditer(r"\d+(?:[.,]\d{1,2})?\s*€", txt))
                if m_all:
                    prezzo = m_all[-1].group(0)
        except Exception:
            pass
        seen.setdefault(href, {"link": href, "titolo": titolo or "(senza titolo)", "prezzo": prezzo or "N/D"})
    return list(seen.values())

# ---------------- MAIN ----------------
def main():
    print("[BOOT] PSP finder…")
    prev = load_history(HISTORY)

    chrome_major = random.choice([123, 124, 125])
    UA = (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--lang=it-IT",
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
        page = context.new_page()

        # Vai direttamente alla lista con spedizione
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except PWTimeout:
            pass

        # Primo tentativo: strutturati
        ads_struct = collect_from_structured(page)
        # Fallback: DOM
        ads_dom = collect_from_dom(page)

        # Merge per link (priorità strutturati)
        merged: Dict[str, Dict] = {}
        for lst in (ads_struct, ads_dom):
            for a in lst:
                merged.setdefault(a["link"], a)
        ads = list(merged.values())

        print(f"[DBG] Raccolti {len(ads)} annunci totali (struct+dom).")

        # Filtri: titolo pertinente e prezzo ≤ 50. NB: shp=true → spedizione disponibile già a monte.
        out = []
        for a in ads:
            title = a.get("titolo") or ""
            if not is_psp_title(title):
                continue
            price_f = euros_to_float(a.get("prezzo") or "")
            if price_f is None or price_f > BUDGET_MAX:
                continue
            link = a.get("link")
            if not link or link in prev:
                continue
            out.append(a)

        print(f"[DBG] Nuovi pertinenti ≤ 50€: {len(out)}")

        context.close()
        browser.close()

    # Notifica & cronologia
    if out:
        msg = "<b>📣 Nuove PSP ≤ 50€ (spedizione)</b>\n\n"
        for a in out:
            msg += f"• {a['titolo']} — <b>{a['prezzo']}</b>\n{a['link']}\n\n"
        send_telegram(msg)
        prev |= {a["link"] for a in out}
        save_history(HISTORY, prev)
    else:
        print("[DONE] Nessun nuovo annuncio in questa esecuzione.")

if __name__ == "__main__":
    main()
