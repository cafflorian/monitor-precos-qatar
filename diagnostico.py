"""Diagnóstico pontual (2 acessos): mapeia a geometria de uma grade e confere o
preço da combinação mais barata na busca de datas exatas. Só leitura, sem cliques."""
import random
import re
import time
from collections import Counter

import monitor as m
from playwright.sync_api import sync_playwright

OUT = m.DATA / "diagnostico"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    m.configurar_log()
    ida, volta = m.PARES[0]
    with sync_playwright() as p:
        ctx = m.abrir_contexto(p, "chromium")
        page = ctx.pages[0]
        page.goto(m.url_busca(ida, volta, True), wait_until="domcontentloaded", timeout=60_000)
        n = m.esperar_grade(page)
        labels = page.eval_on_selector_all(m.SELETOR, "els => els.map(e => e.getAttribute('aria-label'))")
        page.screenshot(path=str(OUT / "grade.png"), full_page=True)
        cel = m.parse_labels(labels)
        idas = sorted({c[0] for c in cel}); voltas = sorted({c[1] for c in cel})
        print(f"GRADE {ida}/{volta}: {n} células | idas {idas[0]}..{idas[-1]} ({len(idas)}) | "
              f"voltas {voltas[0]}..{voltas[-1]} ({len(voltas)})")
        print("células por ida:", dict(sorted(Counter(str(c[0]) for c in cel).items())))
        # outros aria-labels na grade (células sem preço?)
        outros = page.eval_on_selector_all(
            "[aria-label]", "els => els.map(e => e.getAttribute('aria-label'))")
        estranhos = [o for o in outros if re.search(r"(março|abril|maio).*\d", o or "") and "por um voo de volta" not in o]
        print("labels de data SEM preço:", len(estranhos), estranhos[:10])
        (OUT / "labels.txt").write_text("\n".join(labels + ["---"] + estranhos), encoding="utf-8")

        combos = m.filtrar(cel)
        alvo = combos[0]
        print(f"MAIS BARATA NA GRADE: {alvo['ida']} -> {alvo['volta']} R$ {m.brl(alvo['preco'])}")
        time.sleep(random.uniform(5, 15))
        page.goto(m.url_busca(alvo["ida"], alvo["volta"], False), wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(25_000)
        page.screenshot(path=str(OUT / "exata.png"), full_page=True)
        txt = page.inner_text("body")
        (OUT / "exata.txt").write_text(txt, encoding="utf-8")
        print("URL final:", page.url.split("?")[0])
        for linha in txt.splitlines():
            if re.search(r"BRL|R\$|total|adulto|passageiro", linha, re.I):
                print("  >", linha.strip()[:160])
        ctx.close()


if __name__ == "__main__":
    main()
