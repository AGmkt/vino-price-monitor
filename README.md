# 🍷 Vino.com Price Monitor

Monitoraggio automatico quotidiano dei prezzi su [vino.com](https://www.vino.com) per i brand del portfolio Botter.

**👉 [Apri la Dashboard](https://agmkt.github.io/vino-price-monitor/)**

## Brand monitorati

Lupo Meraviglia · Gran Passione · Tor del Colle · Wrap Around · Brilla! · Borgo del Mandorlo · Velarino · Ombre · Botter · La di Motte · Lapilli · Zaccagnini · Barone Montalto · Ricossa · Cuvage

## Uso manuale

```bash
pip3 install playwright pandas openpyxl
python3 -m playwright install chromium
python3 vino_scraper.py --output both --append
```
