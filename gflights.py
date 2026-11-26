"""Google Flights: triagem por calendário de preços + confirmação na lista de voos.

Só leitura. O único clique é no campo de data (para abrir o calendário) e na aba
"Menores preços". Os links enviados por e-mail NUNCA vêm da página: são sempre
os domínios oficiais fixos em OFICIAIS.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("monitor")
DEBUG_DIR = Path(__file__).resolve().parent / "data" / "debug"

# Sites oficiais (lista fixa, conferida). Chave = início do nome exibido no Google.
OFICIAIS = {
    "qatar": ("Qatar Airways", None),  # link exato montado em monitor.py
    "emirates": ("Emirates", "https://www.emirates.com/br/portuguese/"),
    "turkish": ("Turkish Airlines", "https://www.turkishairlines.com/pt-br/"),
    "ethiopian": ("Ethiopian", "https://www.ethiopianairlines.com/br"),
    "air france": ("Air France", "https://wwws.airfrance.com.br/"),
    "klm": ("KLM", "https://www.klm.com.br/"),
    "ita": ("ITA Airways", "https://www.ita-airways.com/"),
    "swiss": ("SWISS", "https://www.swiss.com/"),
    "lufthansa": ("Lufthansa", "https://www.lufthansa.com/"),
    "british": ("British Airways", "https://www.britishairways.com/"),
    "thai": ("THAI", "https://www.thaiairways.com/"),
    "etihad": ("Etihad", "https://www.etihad.com/"),
    "saudia": ("Saudia", "https://www.saudia.com/"),
    "singapore": ("Singapore Airlines", "https://www.singaporeair.com/"),
    "air china": ("Air China", "https://www.airchina.com/"),
}
EXCLUIDAS = ("latam",)


# ------------------------------------------------------------ link (tfs)
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _campo(num, tipo, valor):
    return _varint(num << 3 | tipo) + valor


def _str(num, s):
    b = s.encode()
    return _campo(num, 2, _varint(len(b)) + b)


def _msg(num, corpo):
    return _campo(num, 2, _varint(len(corpo)) + corpo)


def _int(num, v):
    return _campo(num, 0, _varint(v))


def _trecho(data, origem, destino, max_paradas):
    corpo = _str(2, data)
    if max_paradas is not None:
        corpo += _int(5, max_paradas)
    return corpo + _msg(13, _str(2, origem)) + _msg(14, _str(2, destino))


def tfs(ida, volta, origem="GRU", destino="BKK", adultos=2, max_paradas: int | None = 1) -> str:
    corpo = _msg(3, _trecho(ida, origem, destino, max_paradas))
    corpo += _msg(3, _trecho(volta, destino, origem, max_paradas))
    corpo += _msg(8, b"".join(_varint(1) for _ in range(adultos)))  # ADULT=1
    corpo += _int(9, 1)   # ECONOMY
    corpo += _int(19, 1)  # ROUND_TRIP
    return base64.urlsafe_b64encode(corpo).decode().rstrip("=")


def url(ida, volta, **kw) -> str:
    return f"https://www.google.com/travel/flights/search?tfs={tfs(str(ida), str(volta), **kw)}&hl=pt-BR&gl=BR&curr=BRL"


def cia_oficial(nome_google: str) -> tuple[str, str | None]:
    n = nome_google.lower()
    for chave, (nome, link) in OFICIAIS.items():
        if n.startswith(chave):
            return nome, link
    return nome_google, None


# ------------------------------------------------------------ calendário
def _preco_cal(txt: str) -> float | None:
    m = re.search(r"([\d.,]+)\s*mil", txt)
    if m:
        return float(m.group(1).replace(".", "").replace(",", ".")) * 1000
    m = re.search(r"R\$\s*([\d.]+)", txt)
    return float(m.group(1).replace(".", "")) if m else None


def calendario(page, dias: int, ida_ref: date) -> dict[tuple[date, date], float]:
    """Abre a busca (ida_ref, ida_ref+dias) e lê o calendário 'viagens de N dias'."""
    page.goto(url(ida_ref, ida_ref + timedelta(days=dias)), wait_until="domcontentloaded", timeout=60_000)
    _esperar_lista(page)
    campo = page.get_by_role("textbox", name=re.compile("Partida|Departure"))
    js = """e => e.map(x => [x.getAttribute('data-iso') || '', x.innerText || ''])"""
    cel = []
    for tentativa in range(3):  # o calendário às vezes não abre no 1º clique
        campo.first.click()
        leituras, inicio = [], time.time()
        while time.time() - inicio < 25:
            cel = page.eval_on_selector_all("[role=gridcell]", js)
            n = sum(1 for _, t in cel if "mil" in t or "R$" in t)
            leituras.append(n)
            if n >= 40 and len(leituras) >= 3 and leituras[-1] == leituras[-2] == leituras[-3]:
                break
            page.wait_for_timeout(1500)
        if leituras and leituras[-1] >= 40:
            break
        log.warning("Calendário %d dias não abriu (tentativa %d), clicando de novo", dias, tentativa + 1)
        page.keyboard.press("Escape")
        page.wait_for_timeout(2000)
    out = {}
    sem_iso = 0
    for iso, txt in cel:
        p = _preco_cal(txt)
        if p is None:
            continue
        if not iso:
            sem_iso += 1
            continue
        ida = date.fromisoformat(iso)
        out[(ida, ida + timedelta(days=dias))] = p
    if sem_iso:
        log.error("Calendário Google: %d células com preço sem data (layout mudou?)", sem_iso)
    page.keyboard.press("Escape")
    return out


# ------------------------------------------------------------ lista de voos
HORA = re.compile(r"^\d{1,2}:\d{2}(\+\d)?$")


def _parse_lista(txt: str) -> list[dict]:
    voos = []
    blocos = re.split(r"\n\s*ida e volta\s*\n", txt)
    for n, bloco in enumerate(blocos):
        linhas = [l.strip() for l in bloco.splitlines() if l.strip() and l.strip() != "–"]
        # avisos como "Econômica premium + Econômica" vêm logo DEPOIS do preço,
        # ou seja, no começo do bloco seguinte (antes do primeiro horário)
        prox = blocos[n + 1] if n + 1 < len(blocos) else ""
        cabeca_prox = re.split(r"\n\s*\d{1,2}:\d{2}", prox, maxsplit=1)[0]
        horas = [i for i, l in enumerate(linhas) if HORA.match(l)]
        precos = re.findall(r"R\$\s*([\d.]+)", bloco)
        if len(horas) < 2 or not precos:
            continue
        i = horas[-1]
        cia_txt = linhas[i + 1] if i + 1 < len(linhas) else ""
        resto = linhas[i + 1:]
        paradas = next((l for l in resto if re.search(r"parada|Sem escalas|Direto", l)), "")
        n_par = 0 if re.search(r"Sem escalas|Direto", paradas) else int((re.findall(r"\d+", paradas) or [9])[0])
        idx = resto.index(paradas) if paradas in resto else -1
        conexao = ""
        if idx >= 0 and idx + 1 < len(resto):
            m = re.search(r"\b([A-Z]{3})\b\s*$", resto[idx + 1])
            conexao = m.group(1) if m else ""
        partes = [p.strip() for p in re.split(r"[·,]|(?<=[a-z])(?=[A-Z])", cia_txt) if p.strip()]
        voos.append({
            "cia_google": cia_txt,
            "principal": partes[0] if partes else cia_txt,
            "paradas": n_par,
            "conexao": conexao,
            "preco": float(precos[-1].replace(".", "")),
            "premium": "premium" in "\n".join(linhas[horas[0]:]).lower() or "premium" in cabeca_prox.lower(),
        })
    return voos


def _esperar_lista(page, timeout=45) -> int:
    """Espera a lista de voos estabilizar; retorna quantos voos a página mostra."""
    leituras, inicio = [], time.time()
    while time.time() - inicio < timeout:
        try:
            n = len(_parse_lista(page.inner_text("body")))
        except Exception:
            n = 0
        leituras.append(n)
        if n >= 3 and len(leituras) >= 3 and leituras[-1] == leituras[-2] == leituras[-3]:
            return n
        page.wait_for_timeout(1500)
    return leituras[-1] if leituras else 0


def confirmar(page, ida: date, volta: date) -> list[dict]:
    """Preços exatos (total 2 adultos, ida e volta, até 1 parada) por companhia."""
    voos = []
    for tentativa in range(2):  # recarrega uma vez se a lista vier vazia
        page.goto(url(ida, volta), wait_until="domcontentloaded", timeout=60_000)
        _esperar_lista(page)
        aba = page.get_by_role("tab", name=re.compile("Menores preços|Cheapest"))
        if aba.count():
            aba.first.click()
            _esperar_lista(page, timeout=20)
        voos = _parse_lista(page.inner_text("body"))
        if voos:
            break
        log.warning("Lista Google %s/%s vazia (tentativa %d)", ida, volta, tentativa + 1)
        try:
            page.screenshot(path=str(DEBUG_DIR / f"falha_google_conf_{ida}_{tentativa}.png"))
        except Exception:
            pass
    melhores: dict[str, dict] = {}
    for v in voos:
        if v["paradas"] > 1 or v["premium"]:
            continue
        if v["principal"].lower().startswith(EXCLUIDAS):
            continue
        nome, link = cia_oficial(v["principal"])
        if nome not in melhores or v["preco"] < melhores[nome]["preco"]:
            melhores[nome] = {
                "ida": ida, "volta": volta, "dias": (volta - ida).days, "preco": v["preco"],
                "cia": nome, "conexao": v["conexao"], "fonte": "Google Flights", "link": link,
            }
    log.info("Confirmação Google %s/%s: %d voos lidos, %d companhias: %s", ida, volta, len(voos), len(melhores),
             ", ".join(f"{c['cia']} R$ {c['preco']:.0f}" for c in sorted(melhores.values(), key=lambda c: c["preco"])))
    return list(melhores.values())
