#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║           PRICE MONITOR SCRAPER v3.2                           ║
║  Monitoraggio quotidiano prezzi brand                          ║
║                                                                ║
║  Selettori CSS verificati tramite ispezione DOM:               ║
║   .search-result              = container prodotto             ║
║   .search-result-grid-title   = nome prodotto                  ║
║   .watc-sale-price            = prezzo di vendita              ║
║   "Prezzo senza sconto: X €"  = prezzo originale              ║
║   .subtitle                   = formato bottiglia              ║
║   a[href*="dettaglio"]        = link alla pagina prodotto      ║
║                                                                ║
║  NOTE:                                                         ║
║  - Gran Passione e Wrap Around sono sub-brand di Botter        ║
║  - Ombre non è attualmente presente                            ║
║  - L'URL del sito target è letto dalla variabile d'ambiente    ║
║    TARGET_SITE_URL (o passato con --site-url)                  ║
╚══════════════════════════════════════════════════════════════════╝

SETUP:
    pip3 install playwright pandas openpyxl
    python3 -m playwright install chromium

UTILIZZO:
    export TARGET_SITE_URL=https://www.example.com
    python3 vino_scraper.py                    # scraping completo
    python3 vino_scraper.py --brand ricossa    # singolo brand
    python3 vino_scraper.py --output xlsx      # output Excel
    python3 vino_scraper.py --append           # appendi a storico esistente
"""

import argparse
import csv
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("❌ Playwright non installato. Esegui:")
    print("   pip3 install playwright")
    print("   python3 -m playwright install chromium")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    pd = None


# ─── Site URL (da variabile d'ambiente) ─────────────────────────
SITE_URL = os.environ.get("TARGET_SITE_URL", "")


# ─── Brand Config ───────────────────────────────────────────────
BRAND_CONFIG = {
    "LUPO MERAVIGLIA": {
        "slugs": ["lupo-meraviglia"],
    },
    "GRAN PASSIONE": {
        "slugs": [],
        "sub_brand": True,
        "parent": "botter",
        "filter": r"(?i)gran\s*passione",
    },
    "TOR DEL COLLE": {
        "slugs": ["tor-del-colle"],
    },
    "WRAP AROUND": {
        "slugs": [],
        "sub_brand": True,
        "parent": "botter",
        "filter": r"(?i)wrap\s*around",
    },
    "BRILLA!": {
        "slugs": ["brilla"],
    },
    "BORGO DEL MANDORLO": {
        "slugs": ["borgo-del-mandorlo"],
    },
    "VELARINO": {
        "slugs": ["velarino"],
    },
    "OMBRE": {
        "slugs": ["ombre"],
        "note": "Non attualmente presente",
    },
    "BOTTER": {
        "slugs": ["botter"],
        "exclude_filter": r"(?i)(gran\s*passione|wrap\s*around)",
    },
    "LA DI MOTTE": {
        "slugs": ["la-di-motte"],
    },
    "LAPILLI": {
        "slugs": ["lapilli"],
    },
    "ZACCAGNINI": {
        "slugs": ["zaccagnini"],
    },
    "BARONE MONTALTO": {
        "slugs": ["barone-montalto"],
    },
    "RICOSSA": {
        "slugs": ["ricossa"],
    },
    "CUVAGE": {
        "slugs": ["cuvage"],
    },
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

OUTPUT_DIR = Path("output")
STORICO_FILE = OUTPUT_DIR / "vino_prezzi_storico.csv"
CSV_HEADERS = [
    "Brand", "Nome vino", "Annata", "Formato",
    "Prezzo originale €", "Prezzo scontato €", "Sconto %",
    "Data rilevazione", "Link"
]


# ─── Utility ────────────────────────────────────────────────────

def get_base_url():
    if not SITE_URL:
        print("❌ URL del sito non configurato.")
        print("   Imposta la variabile d'ambiente TARGET_SITE_URL:")
        print("   export TARGET_SITE_URL=https://www.example.com")
        print("   oppure usa: python3 vino_scraper.py --site-url https://www.example.com")
        sys.exit(1)
    return SITE_URL.rstrip("/")

def build_url(prefix, slug):
    return f"{get_base_url()}/{prefix}/{slug}"

def parse_price(text):
    if not text:
        return None
    text = text.strip().replace("€", "").replace("\xa0", "").replace(" ", "")
    text = re.sub(r"[^\d.,]", "", text)
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        parts = text.rsplit(".", 1)
        text = parts[0].replace(".", "") + "." + parts[1]
    try:
        return round(float(text), 2)
    except (ValueError, TypeError):
        return None


# ─── JS per estrazione dal DOM ──────────────────────────────────

EXTRACT_JS = """() => {
    const cards = document.querySelectorAll('.search-result');
    const products = [];
    const seen = new Set();
    
    cards.forEach(card => {
        const titleEl = card.querySelector('.search-result-grid-title');
        const name = titleEl ? titleEl.textContent.trim() : '';
        if (!name) return;
        
        const linkEl = card.querySelector('a[href*="dettaglio"]');
        const link = linkEl ? linkEl.href.split('?')[0] : '';
        if (!link || seen.has(link)) return;
        seen.add(link);
        
        const fmtEl = card.querySelector('.subtitle');
        const formato = fmtEl ? fmtEl.textContent.trim() : '';
        
        const saleEl = card.querySelector('.watc-sale-price');
        const saleText = saleEl ? saleEl.textContent.trim() : '';
        
        const cardText = card.innerText || '';
        const origMatch = cardText.match(/Prezzo senza sconto[:\\s]*([\\d]+[,.][\\d]{2})/);
        const origText = origMatch ? origMatch[1] : saleText;
        
        if (!saleText) return;
        
        products.push({
            name: name,
            link: link,
            formato: formato,
            salePrice: saleText.replace(/[^\\d,.]/, ''),
            origPrice: origText.replace(/[^\\d,.]/, ''),
        });
    });
    
    return products;
}"""


# ─── Scraping ───────────────────────────────────────────────────

def accept_cookies(page):
    for sel in ["button:has-text('Accetta')", "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"]:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=2000)
                time.sleep(0.5)
                return
        except:
            continue

def wait_for_products(page, max_wait=25):
    print("    ⏳ Attendo prodotti...", end="", flush=True)
    for i in range(max_wait):
        time.sleep(1)
        count = page.evaluate("() => document.querySelectorAll('.search-result').length")
        if count > 0:
            time.sleep(2)
            print(f" OK ({i+1}s, {count} cards)")
            return True
        if i % 4 == 3:
            page.mouse.wheel(0, 800)
    print(" TIMEOUT")
    return False

def scroll_to_load_all(page):
    prev = 0
    for _ in range(20):
        page.mouse.wheel(0, 1500)
        time.sleep(1.5)
        curr = page.evaluate("() => document.querySelectorAll('.search-result').length")
        if curr == prev:
            break
        prev = curr

def scrape_page(page, url):
    print(f"  🌐 {url}")
    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
    except PWTimeout:
        print(f"    ⚠️  Timeout")
        return []
    except Exception as e:
        print(f"    ❌ {e}")
        return []
    accept_cookies(page)
    if not wait_for_products(page):
        return []
    scroll_to_load_all(page)
    try:
        raw = page.evaluate(EXTRACT_JS)
    except Exception as e:
        print(f"    ❌ JS error: {e}")
        return []
    print(f"    ✅ {len(raw)} prodotti trovati")
    return raw

def raw_to_product(rp, brand):
    name = rp["name"]
    # Filtra multipack/casse (es. "6 bottiglie", "6x0,75")
    if re.search(r'(\d+\s*(bottigli|x\s*0|×\s*0))', name, re.IGNORECASE):
        return None
    sale = parse_price(rp["salePrice"])
    orig = parse_price(rp["origPrice"])
    if not sale:
        return None
    if not orig or orig < sale:
        orig = sale
    annata = "NV"
    ym = re.search(r"20[12]\d", name)
    if ym:
        annata = ym.group()
    formato = rp.get("formato", "")
    if not formato:
        formato = "0,75 L"
    else:
        formato = formato.replace("ℓ", "L").strip()
    sconto = 0
    if orig > sale:
        sconto = round((orig - sale) / orig, 4)
    return {
        "Brand": brand, "Nome vino": name, "Annata": annata,
        "Formato": formato, "Prezzo originale €": orig,
        "Prezzo scontato €": sale, "Sconto %": sconto,
        "Data rilevazione": date.today().isoformat(), "Link": rp["link"],
    }

def run_scraper(brands_filter=None):
    brands = brands_filter or list(BRAND_CONFIG.keys())
    all_products = {}
    print(f"\n🍷 Price Monitor v3.2")
    print(f"📅 Data: {date.today().isoformat()}")
    print(f"🏷️  Brand: {len(brands)}")
    print("=" * 60)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="it-IT", timezone_id="Europe/Rome",
        )
        for pat in ["**/analytics**", "**/googletagmanager**", "**/facebook.com/**"]:
            context.route(pat, lambda route: route.abort())
        page = context.new_page()
        scraped_pages = {}
        
        for brand in brands:
            config = BRAND_CONFIG.get(brand)
            if not config:
                print(f"\n⚠️  '{brand}' non configurato")
                continue
            print(f"\n🏷️  {brand}")
            if config.get("note"):
                print(f"  ℹ️  {config['note']}")
            brand_products = []
            
            if config.get("sub_brand"):
                parent_slug = config["parent"]
                name_filter = config["filter"]
                for prefix in ["produttore", "selezione"]:
                    url = build_url(prefix, parent_slug)
                    if url not in scraped_pages:
                        raw = scrape_page(page, url)
                        scraped_pages[url] = raw
                        time.sleep(random.uniform(2, 4))
                    else:
                        raw = scraped_pages[url]
                        print(f"  🌐 {url} (cache, {len(raw)} prodotti)")
                    for rp in raw:
                        if re.search(name_filter, rp["name"]):
                            link = rp["link"]
                            if link not in all_products:
                                prod = raw_to_product(rp, brand)
                                if prod:
                                    all_products[link] = prod
                                    brand_products.append(prod)
            else:
                exclude = config.get("exclude_filter")
                for slug in config["slugs"]:
                    for prefix in ["produttore", "selezione"]:
                        url = build_url(prefix, slug)
                        if url not in scraped_pages:
                            raw = scrape_page(page, url)
                            scraped_pages[url] = raw
                            time.sleep(random.uniform(2, 4))
                        else:
                            raw = scraped_pages[url]
                            print(f"  🌐 {url} (cache, {len(raw)} prodotti)")
                        for rp in raw:
                            link = rp["link"]
                            if link in all_products: continue
                            if exclude and re.search(exclude, rp["name"]): continue
                            prod = raw_to_product(rp, brand)
                            if prod:
                                all_products[link] = prod
                                brand_products.append(prod)
            
            for prod in brand_products:
                d = prod["Sconto %"]
                ds = f" (-{d*100:.0f}%)" if d > 0 else ""
                print(f"    📦 {prod['Nome vino'][:55]}")
                print(f"       €{prod['Prezzo scontato €']}{ds}  {prod['Formato']}")
            if not brand_products:
                print(f"  ⚠️  Nessun prodotto")
            time.sleep(random.uniform(1, 3))
        
        browser.close()
    products = list(all_products.values())
    n_brands = len(set(p["Brand"] for p in products))
    print(f"\n{'=' * 60}")
    print(f"✅ {len(products)} prodotti da {n_brands} brand")
    return products


# ─── Output ─────────────────────────────────────────────────────

def fmt_price_it(val):
    """Formatta un prezzo con virgola decimale per Excel italiano: 9.9 → 9,90"""
    if isinstance(val, (int, float)):
        return f"{val:.2f}".replace(".", ",")
    return val

def save_csv(products, filepath):
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS, delimiter=";")
        w.writeheader()
        for p in products:
            row = {k: p.get(k, "") for k in CSV_HEADERS}
            row["Prezzo originale €"] = fmt_price_it(row["Prezzo originale €"])
            row["Prezzo scontato €"] = fmt_price_it(row["Prezzo scontato €"])
            if isinstance(row["Sconto %"], (int, float)):
                row["Sconto %"] = f"{row['Sconto %']*100:.1f}%".replace(".", ",")
            w.writerow(row)
    print(f"📄 {filepath}")

def save_xlsx(products, filepath):
    if pd is None:
        print("⚠️  pandas mancante, salvo CSV")
        return save_csv(products, filepath.with_suffix(".csv"))
    df = pd.DataFrame(products)[CSV_HEADERS]
    df["Sconto %"] = df["Sconto %"].apply(
        lambda x: f"{x*100:.1f}%" if isinstance(x, (int, float)) else x)
    with pd.ExcelWriter(filepath, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Prezzi")
        ws = w.sheets["Prezzi"]
        for col in ws.columns:
            ml = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(ml + 3, 50)
    print(f"📊 {filepath}")

def append_storico(products):
    exists = STORICO_FILE.exists()
    with open(STORICO_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS, delimiter=";")
        if not exists: w.writeheader()
        for p in products:
            row = {k: p.get(k, "") for k in CSV_HEADERS}
            row["Prezzo originale €"] = fmt_price_it(row["Prezzo originale €"])
            row["Prezzo scontato €"] = fmt_price_it(row["Prezzo scontato €"])
            if isinstance(row["Sconto %"], (int, float)):
                row["Sconto %"] = f"{row['Sconto %']*100:.1f}%".replace(".", ",")
            w.writerow(row)
    print(f"📚 {STORICO_FILE}")


# ─── CLI ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="🍷 Price Monitor v3.2")
    parser.add_argument("--brand", nargs="+", help="Brand specifici (default: tutti)")
    parser.add_argument("--output", choices=["csv", "xlsx", "both"], default="csv")
    parser.add_argument("--append", action="store_true", help="Appendi allo storico")
    parser.add_argument("--outdir", default="output")
    parser.add_argument("--list-brands", action="store_true")
    parser.add_argument("--site-url", help="URL base del sito target (alternativa a TARGET_SITE_URL)")
    args = parser.parse_args()

    if args.site_url:
        global SITE_URL
        SITE_URL = args.site_url
    
    if args.list_brands:
        print("\n🏷️  Brand configurati:")
        for b, cfg in BRAND_CONFIG.items():
            if cfg.get("sub_brand"):
                print(f"  • {b:<25} → sub-brand di {cfg['parent']} (filtro: {cfg['filter']})")
            else:
                slugs = ", ".join(cfg.get("slugs", []))
                note = f" ⚠️ {cfg['note']}" if cfg.get("note") else ""
                print(f"  • {b:<25} → {slugs}{note}")
        return
    
    brands = None
    if args.brand:
        brands = [b.upper() for b in args.brand]
        bad = [b for b in brands if b not in BRAND_CONFIG]
        if bad:
            print(f"❌ Sconosciuti: {', '.join(bad)}")
            return
    
    global OUTPUT_DIR, STORICO_FILE
    OUTPUT_DIR = Path(args.outdir)
    STORICO_FILE = OUTPUT_DIR / "vino_prezzi_storico.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    products = run_scraper(brands)
    if not products:
        print("\n⚠️  Nessun prodotto trovato.")
        return
    
    d = date.today().isoformat()
    if args.output in ("csv", "both"):
        save_csv(products, OUTPUT_DIR / f"vino_prezzi_{d}.csv")
    if args.output in ("xlsx", "both"):
        save_xlsx(products, OUTPUT_DIR / f"vino_prezzi_{d}.xlsx")
    if args.append:
        append_storico(products)
    
    print(f"\n{'=' * 60}")
    print("📊 RIEPILOGO")
    bf = {}
    for p in products:
        b = p["Brand"]
        bf.setdefault(b, {"n": 0, "promo": 0})
        bf[b]["n"] += 1
        if p.get("Sconto %", 0) > 0: bf[b]["promo"] += 1
    for b, i in sorted(bf.items()):
        ps = f" ({i['promo']} in promo)" if i["promo"] else ""
        print(f"  {b:<25} {i['n']:>3} prodotti{ps}")
    tot = len(products)
    promo = sum(1 for p in products if p.get("Sconto %", 0) > 0)
    print(f"\n  {'TOTALE':<25} {tot:>3} prodotti ({promo} in promo)")

if __name__ == "__main__":
    main()
