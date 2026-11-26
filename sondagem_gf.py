"""Sondagem do Google Flights (só leitura): lista de resultados e grade de datas."""
import re

import gflights
import monitor as m
from playwright.sync_api import sync_playwright

OUT = m.DATA / "sondagem"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    u = gflights.url("2027-03-19", "2027-04-04")
    print("URL:", u)
    with sync_playwright() as p:
        ctx = m.abrir_contexto(p, "chromium")
        page = ctx.pages[0]
        page.goto(u, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(15_000)
        print("url final:", page.url[:150], "| título:", page.title())
        page.screenshot(path=str(OUT / "gf_lista.png"), full_page=True)
        txt = page.inner_text("body")
        (OUT / "gf_lista.txt").write_text(txt, encoding="utf-8")
        labels = page.eval_on_selector_all("[aria-label]", "e => e.map(x => x.getAttribute('aria-label'))")
        voos = [l for l in labels if re.search(r"(R\$|BRL|reais)", l or "")]
        (OUT / "gf_labels.txt").write_text("\n".join(voos), encoding="utf-8")
        print("labels com preço:", len(voos))
        for l in voos[:6]:
            print("  >", l[:400])
        for l in txt.splitlines():
            if re.search(r"passageir|adult|total|parada|escala", l, re.I):
                print("  txt>", l.strip()[:150])
        # grade de datas
        for nome in ("Grade de datas", "Date grid"):
            btn = page.get_by_role("button", name=nome)
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(10_000)
                page.screenshot(path=str(OUT / "gf_grade.png"))
                g = page.eval_on_selector_all("[aria-label]", "e => e.map(x => x.getAttribute('aria-label'))")
                g = [l for l in g if re.search(r"R\$|BRL|reais", l or "") and l not in voos]
                (OUT / "gf_grade.txt").write_text("\n".join(g), encoding="utf-8")
                print("GRADE: células com preço:", len(g))
                for l in g[:6]:
                    print("  g>", l[:300])
                break
        else:
            print("botão grade de datas não encontrado")
        ctx.close()


if __name__ == "__main__":
    main()
