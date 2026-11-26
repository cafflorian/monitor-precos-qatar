"""Sondagem do calendário de preços do Google Flights (só leitura)."""
import re

import gflights
import monitor as m
from playwright.sync_api import sync_playwright

OUT = m.DATA / "sondagem"
OUT.mkdir(parents=True, exist_ok=True)


def dump_cal(page, nome):
    cells = page.eval_on_selector_all(
        "[role=gridcell]", "e => e.map(x => (x.getAttribute('aria-label')||'') + ' || ' + x.innerText.replace(/\\n/g,' '))")
    cells = [c for c in cells if re.search(r"\d", c)]
    (OUT / f"{nome}.txt").write_text("\n".join(cells), encoding="utf-8")
    print(f"{nome}: {len(cells)} células")
    for c in cells:
        if re.search(r"(15|19|20|21|22) de mar", c):
            print("  >", c[:200])
    page.screenshot(path=str(OUT / f"{nome}.png"))


def main():
    with sync_playwright() as p:
        ctx = m.abrir_contexto(p, "chromium")
        page = ctx.pages[0]
        for paradas, nome in ((1, "cal_1parada"), (None, "cal_qualquer")):
            u = gflights.url("2027-03-19", "2027-04-04", max_paradas=paradas)
            page.goto(u, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(12_000)
            campo = page.get_by_role("textbox", name=re.compile("Partida|Departure"))
            print(nome, "campos partida:", campo.count())
            campo.first.click()
            page.wait_for_timeout(10_000)
            dump_cal(page, nome)
            page.wait_for_timeout(8_000)
        ctx.close()


if __name__ == "__main__":
    main()
