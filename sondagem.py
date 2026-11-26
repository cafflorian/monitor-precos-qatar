"""Sondagem única: 1 acesso por companhia (só leitura) para ver se o site oficial
abre a partir do servidor e se o link de busca direto mostra preços."""
import random
import re
import time

import monitor as m
from playwright.sync_api import sync_playwright

IDA, VOLTA = "2027-03-19", "2027-04-04"
ALVOS = {
    "airfrance": "https://wwws.airfrance.com.br/search/offers?pax=2:0:0:0:0:0:0:0&cabinClass=ECONOMY"
                 "&activeConnection=0&connections=GRU:A-BKK:A@2027-03-19T00:00:00.000Z%3EBKK:A-GRU:A@2027-04-04T00:00:00.000Z",
    "klm": "https://www.klm.com.br/search/offers?pax=2:0:0:0:0:0:0:0&cabinClass=ECONOMY"
           "&activeConnection=0&connections=GRU:A-BKK:A@2027-03-19T00:00:00.000Z%3EBKK:A-GRU:A@2027-04-04T00:00:00.000Z",
    "emirates": "https://www.emirates.com/br/portuguese/destinations/gru/bkk/flights-from-sao-paulo-to-bangkok/",
    "turkish": "https://www.turkishairlines.com/pt-br/flights/fly-to-bangkok/",
    "ethiopian": "https://www.ethiopianairlines.com/br/book/booking/flight",
    "lufthansa": "https://www.lufthansa.com/br/pt/homepage",
    "swiss": "https://www.swiss.com/br/pt/homepage",
    "ita": "https://www.ita-airways.com/lhg/br/pt/o-d/cy-cy/sao-paulo-bangkok",
    "british": "https://www.britishairways.com/travel/home/public/pt_br/",
}
OUT = m.DATA / "sondagem"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    with sync_playwright() as p:
        ctx = m.abrir_contexto(p, "chromium")
        page = ctx.pages[0]
        for i, (nome, url) in enumerate(ALVOS.items()):
            if i:
                time.sleep(random.uniform(5, 12))
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(20_000)
                txt = page.inner_text("body")
                status = resp.status if resp else "?"
                precos = re.findall(r"(?:BRL|R\$)\s?[\d.,]+", txt)
                bloqueio = bool(re.search(r"access denied|forbidden|captcha|unusual traffic|bot|são humano|verify you are", txt, re.I))
                print(f"[{nome}] HTTP {status} | url final {page.url[:110]} | {len(txt)} chars | "
                      f"preços: {precos[:8]} | sinal de bloqueio: {bloqueio} | título: {page.title()[:70]}")
                page.screenshot(path=str(OUT / f"{nome}.png"))
                (OUT / f"{nome}.txt").write_text(txt, encoding="utf-8")
            except Exception as e:
                print(f"[{nome}] ERRO {e}")
        ctx.close()


if __name__ == "__main__":
    main()
