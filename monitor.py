"""
Monitor de preços GRU <-> BKK (Econômica, 2 adultos, até 1 conexão por sentido).

Fontes:
  1. Qatar Airways (site oficial): calendário flexível, preços exatos.
  2. Google Flights: calendário "viagens de N dias" (triagem, valores arredondados)
     + lista de voos para confirmar preço exato e companhia (todas as companhias
     com até 1 parada, exceto LATAM).

Somente CONSULTA: nunca compra, nunca preenche dados pessoais ou de pagamento.
Links nos e-mails apontam só para sites oficiais (lista fixa em gflights.OFICIAIS).

Uso:
    python monitor.py            # execução normal (agendada)
    python monitor.py --teste    # roda uma vez, imprime e manda e-mail de teste
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
LOGS = BASE / "logs"
STATE_FILE = DATA / "state.json"
CSV_FILE = DATA / "historico.csv"
DEBUG_DIR = DATA / "debug"

ANO = 2027
IDA_MIN = date(2027, 3, 15)
IDA_MAX = date(2027, 4, 30)
DIAS_MIN, DIAS_MAX = 14, 18
MAX_CONFIRMACOES = 5   # acessos extras ao Google por execução para preço exato
# O calendário do Google é arredondado ("17,3 mil") e pode estar defasado em centenas
# de reais; confirma (até MAX_CONFIRMACOES) toda data que fique até esta margem do limite.
FOLGA_ARREDONDAMENTO = 1000

PARES = [
    ("2027-03-19", "2027-04-04"),
    ("2027-03-29", "2027-04-14"),
    ("2027-04-08", "2027-04-24"),
    ("2027-04-18", "2027-05-04"),
    ("2027-04-28", "2027-05-14"),
]

URL_BASE = (
    "https://www.qatarairways.com/app/booking/flight-selection?widget=QR&searchType=F"
    "&addTaxToFare=Y&minPurAge=0&selLang=pt&tripType=R&fromStation=GRU&toStation=BKK"
    "&departing={ida}&returning={volta}&bookingClass=E&adults=2&children=0&infants=0"
    "&ofw=0&teenager=0&flexibleDate={flex}"
)

SELETOR = '[aria-label*="por um voo de volta"]'
REGEX = re.compile(
    r"BRL ([\d.,]+) por um voo de volta\. Você parte no dia [^,]+, (\S+) (\d+) "
    r"e retorna no dia [^,]+, (\S+) (\d+)"
)
MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

log = logging.getLogger("monitor")


# ----------------------------------------------------------------- utilidades
def configurar_log() -> None:
    LOGS.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOGS / "monitor.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(fh)
    if sys.stdout is not None:  # pythonw não tem console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def ddmm(d: date) -> str:
    return d.strftime("%d/%m")


def url_qatar(ida: date | str, volta: date | str, flex: bool) -> str:
    return URL_BASE.format(ida=str(ida), volta=str(volta), flex="true" if flex else "false")


def onde_ver() -> str:
    """Link da execução no GitHub Actions, ou a pasta local de logs."""
    if os.getenv("GITHUB_RUN_ID"):
        return (f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}"
                f"/actions/runs/{os.getenv('GITHUB_RUN_ID')}")
    return str(LOGS)


def carregar_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salvar_state(state: dict) -> None:
    DATA.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def config() -> dict:
    load_dotenv(BASE / ".env")
    limite_txt = os.getenv("LIMITE", "17338.00").strip().replace("R$", "").strip()
    if "," in limite_txt:  # aceita 17.338,00 ou 17338.00
        limite_txt = limite_txt.replace(".", "").replace(",", ".")
    return {
        "user": os.getenv("SMTP_USER", "").strip(),
        "senha": os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "").replace("﻿", "").strip(),
        "para": [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()],
        "limite": float(limite_txt),
        "canal": os.getenv("BROWSER_CHANNEL", "chromium").strip().lower(),
        # true = manda um resumo a cada execução, mesmo sem nada abaixo do limite
        "sempre": os.getenv("EMAIL_SEMPRE", "true").strip().lower() not in ("false", "0", "nao", "não", "no"),
    }


def no_periodo(ida: date, volta: date) -> bool:
    return IDA_MIN <= ida <= IDA_MAX and DIAS_MIN <= (volta - ida).days <= DIAS_MAX


def pausa(minimo: float = 5, maximo: float = 15) -> None:
    s = random.uniform(minimo, maximo)
    log.info("Aguardando %.1f s", s)
    time.sleep(s)


# -------------------------------------------------------------------- Qatar
def abrir_contexto(p, canal: str):
    perfil = DATA / f"perfil_{canal}"
    perfil.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        user_data_dir=str(perfil),
        headless=False,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        no_viewport=True,
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        ignore_default_args=["--enable-automation"],
    )
    if canal == "chrome":
        kwargs["channel"] = "chrome"
    log.info("Abrindo navegador (%s)", canal)
    return p.chromium.launch_persistent_context(**kwargs)


def esperar_grade(page) -> int:
    leituras: list[int] = []
    inicio = time.time()
    while time.time() - inicio < 60:
        try:
            n = page.locator(SELETOR).count()
        except Exception:
            n = 0
        leituras.append(n)
        if n > 100 and len(leituras) >= 3 and leituras[-1] == leituras[-2] == leituras[-3]:
            return n
        page.wait_for_timeout(1500)
    return leituras[-1] if leituras else 0


def parse_labels(labels: list[str]) -> list[tuple[date, date, float]]:
    out = []
    for lab in labels:
        m = REGEX.search(lab)
        if not m:
            continue
        preco = float(m.group(1).replace(".", "").replace(",", "."))
        mi, di, mv, dv = m.group(2).lower(), int(m.group(3)), m.group(4).lower(), int(m.group(5))
        if mi not in MESES or mv not in MESES:
            continue
        try:
            out.append((date(ANO, MESES[mi], di), date(ANO, MESES[mv], dv), preco))
        except ValueError:
            continue
    return out


def salvar_debug(page, nome: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"falha_{nome}.png"), full_page=False)
    except Exception:
        pass


def buscar_qatar(ctx, ida: str, volta: str) -> list[tuple[date, date, float]]:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(url_qatar(ida, volta, flex=True), wait_until="domcontentloaded", timeout=60_000)
        n = esperar_grade(page)
        labels = page.eval_on_selector_all(SELETOR, "els => els.map(e => e.getAttribute('aria-label'))")
        celulas = parse_labels(labels)
        log.info("Qatar %s/%s: %d elementos, %d células válidas", ida, volta, n, len(celulas))
        if not celulas:
            salvar_debug(page, f"qatar_{ida}")
        return celulas
    except Exception as e:
        log.error("Erro na busca Qatar %s/%s: %s", ida, volta, e)
        salvar_debug(page, f"qatar_{ida}")
        return []


def combos_qatar(celulas) -> list[dict]:
    melhor: dict[tuple[date, date], float] = {}
    for ida, volta, preco in celulas:
        if no_periodo(ida, volta):
            melhor[(ida, volta)] = min(preco, melhor.get((ida, volta), preco))
    return [
        {"ida": i, "volta": v, "dias": (v - i).days, "preco": p, "cia": "Qatar Airways",
         "conexao": "DOH", "fonte": "qatarairways.com", "link": url_qatar(i, v, flex=False)}
        for (i, v), p in melhor.items()
    ]


# -------------------------------------------------------------------- coleta
def coletar(cfg: dict, state: dict) -> dict:
    """Retorna dict com combos exatos, calendário Google e contadores de falha."""
    import gflights
    from playwright.sync_api import sync_playwright

    r = {"exatos": [], "cal": {}, "n_qatar": 0, "falhas_qatar": 0, "falhas_google": 0, "confirmacoes": 0}
    canal = state.get("canal") or cfg["canal"]
    with sync_playwright() as p:
        ctx = abrir_contexto(p, canal)
        try:
            # ---- 1. Qatar (site oficial)
            celulas = []
            for i, (ida, volta) in enumerate(PARES):
                if i:
                    pausa()
                c = buscar_qatar(ctx, ida, volta)
                if not c and i == 0 and canal != "chrome":
                    log.warning("Grade vazia com %s; tentando o Chrome instalado (channel=chrome)", canal)
                    ctx.close()
                    canal = "chrome"
                    ctx = abrir_contexto(p, canal)
                    pausa()
                    c = buscar_qatar(ctx, ida, volta)
                    if c:
                        state["canal"] = "chrome"
                if not c:
                    r["falhas_qatar"] += 1
                celulas.extend(c)
            r["n_qatar"] = len(celulas)
            r["exatos"].extend(combos_qatar(celulas))

            # ---- 2. Google Flights: calendário por duração (triagem)
            page = ctx.pages[0]
            for dias in range(DIAS_MIN, DIAS_MAX + 1):
                pausa(15, 30)  # o Google responde "Algo deu errado" com acessos muito seguidos
                try:
                    cal = gflights.calendario(page, dias, IDA_MIN)
                    if len(cal) < 40:  # costuma falhar no 1º acesso; tenta a página de novo
                        log.warning("Calendário %d dias vazio; recarregando a busca", dias)
                        pausa(15, 30)
                        cal = gflights.calendario(page, dias, IDA_MIN)
                    cal = {k: v for k, v in cal.items() if no_periodo(*k)}
                    log.info("Google calendário %d dias: %d datas", dias, len(cal))
                    if len(cal) < 40:
                        r["falhas_google"] += 1
                        salvar_debug(page, f"google_cal_{dias}")
                    r["cal"].update(cal)
                except Exception as e:
                    log.error("Erro no calendário Google %d dias: %s", dias, e)
                    salvar_debug(page, f"google_cal_{dias}")
                    r["falhas_google"] += 1

            # ---- 3. Google Flights: confirmação (preço exato + companhia)
            if r["cal"]:
                ordenado = sorted(r["cal"].items(), key=lambda kv: kv[1])
                alvos = [k for k, v in ordenado if v - FOLGA_ARREDONDAMENTO < cfg["limite"]]
                if ordenado[0][0] not in alvos:
                    alvos.insert(0, ordenado[0][0])  # sempre confirma a mais barata
                for ida, volta in alvos[:MAX_CONFIRMACOES]:
                    pausa(15, 30)
                    try:
                        for c in gflights.confirmar(page, ida, volta):
                            if c["cia"] == "Qatar Airways":
                                c["link"] = url_qatar(ida, volta, flex=False)
                            r["exatos"].append(c)
                        r["confirmacoes"] += 1
                    except Exception as e:
                        log.error("Erro na confirmação Google %s/%s: %s", ida, volta, e)
                        salvar_debug(page, f"google_conf_{ida}")
        finally:
            ctx.close()
    return r


def salvar_csv(exatos: list[dict], cal: dict) -> None:
    DATA.mkdir(exist_ok=True)
    cab = ["timestamp", "fonte", "companhia", "ida", "volta", "dias", "preco", "exato"]
    if CSV_FILE.exists():
        with CSV_FILE.open(encoding="utf-8") as f:
            if f.readline().strip().split(";") != cab:
                CSV_FILE.replace(DATA / "historico_qatar_v1.csv")
    novo = not CSV_FILE.exists()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if novo:
            w.writerow(cab)
        for c in exatos:
            w.writerow([ts, c["fonte"], c["cia"], c["ida"].isoformat(), c["volta"].isoformat(),
                        c["dias"], f"{c['preco']:.2f}", 1])
        for (i, v), p in sorted(cal.items()):
            w.writerow([ts, "Google calendário", "(várias)", i.isoformat(), v.isoformat(),
                        (v - i).days, f"{p:.2f}", 0])


# -------------------------------------------------------------------- e-mail
def enviar_email(cfg: dict, assunto: str, corpo: str | tuple[str, str]) -> bool:
    """corpo: texto puro, ou (texto, html) montado por montar_email()."""
    if not (cfg["user"] and cfg["senha"] and cfg["para"]):
        log.error("E-mail não enviado: preencha SMTP_USER, SMTP_APP_PASSWORD e EMAIL_TO (.env local ou Secrets do GitHub)")
        return False
    texto, html = corpo if isinstance(corpo, tuple) else (corpo, None)
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = cfg["user"]
    msg["To"] = ", ".join(cfg["para"])
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(cfg["user"], cfg["senha"])
            s.send_message(msg)
        log.info("E-mail enviado: %s", assunto)
        return True
    except Exception as e:
        log.error("Falha ao enviar e-mail: %s", e)
        return False


def cia_txt(c: dict) -> str:
    return f"{c['cia']} (via {c['conexao']})" if c.get("conexao") else c["cia"]


def link_txt(c: dict) -> str:
    if c.get("link"):
        return c["link"]
    return f"(site oficial da {c['cia']})"


def linhas_combos(combos: list[dict]) -> str:
    return "\n".join(
        f"- {cia_txt(c)} | Ida {c['ida']:%d/%m/%Y} | Volta {c['volta']:%d/%m/%Y} | {c['dias']} dias | "
        f"R$ {brl(c['preco'])} (casal)\n  {link_txt(c)}"
        for c in combos
    )


VERDE, VERMELHO, AZUL, LARANJA = "#1a7f37", "#cf222e", "#0969da", "#bc4c00"


def datas_txt(c: dict) -> str:
    return f"{c['ida']:%d/%m/%Y} → {c['volta']:%d/%m/%Y} ({c['dias']} dias)"


def dif_limite(preco: float, limite: float) -> str:
    d = preco - limite
    if d < 0:
        return f"R$ {brl(-d)} abaixo do limite"
    if d == 0:
        return "igual ao limite"
    return f"R$ {brl(d)} acima do limite"


def montar_email(titulo: str, cor: str, itens: list[tuple[str, str]],
                 combos: list[dict] | None = None, titulo_detalhes: str = "",
                 rodape: str = "") -> tuple[str, str]:
    """Quadro de resumo no topo + detalhes no final. Retorna (texto, html)."""
    combos = combos or []
    largura = max([len(titulo)] + [len(k) + len(v) + 2 for k, v in itens]) + 4
    txt = ["+" + "-" * largura + "+", f"| {titulo.ljust(largura - 2)} |", "+" + "-" * largura + "+"]
    txt += [f"| {(k + ': ' + v).ljust(largura - 2)} |" for k, v in itens]
    txt.append("+" + "-" * largura + "+")
    if combos:
        txt += ["", "", f"DETALHES - {titulo_detalhes}", "", linhas_combos(combos)]
    if rodape:
        txt += ["", rodape]
    linhas = "".join(
        f'<tr><td style="padding:3px 16px 3px 0;color:#57606a;white-space:nowrap">{escape(k)}</td>'
        f'<td style="padding:3px 0;font-weight:600;color:#1f2328">{escape(v)}</td></tr>'
        for k, v in itens
    )
    quadro = (
        f'<div style="border:2px solid {cor};border-left-width:8px;border-radius:8px;padding:16px 20px;'
        f'max-width:640px;background:#f6f8fa;margin-bottom:28px">'
        f'<div style="font-size:19px;font-weight:700;color:{cor};margin-bottom:10px">{escape(titulo)}</div>'
        f'<table style="border-collapse:collapse;font-size:15px">{linhas}</table></div>'
    )
    det = ""
    if combos:
        th = 'style="text-align:left;padding:6px 10px;border-bottom:2px solid #d0d7de;color:#57606a"'
        td = 'style="padding:6px 10px;border-bottom:1px solid #eaeef2"'

        def link_html(c):
            if c.get("link"):
                rot = "abrir busca" if "qatarairways.com" in c["link"] and "departing=" in c["link"] else "site oficial"
                return f'<a href="{escape(c["link"])}">{rot}</a>'
            return "—"

        corpo_tab = "".join(
            f'<tr><td {td}>{escape(cia_txt(c))}</td><td {td}>{c["ida"]:%d/%m/%Y}</td>'
            f'<td {td}>{c["volta"]:%d/%m/%Y}</td><td {td} align="center">{c["dias"]}</td>'
            f'<td {td} align="right"><b>R$ {brl(c["preco"])}</b></td><td {td}>{link_html(c)}</td></tr>'
            for c in combos
        )
        det = (
            f'<div style="font-size:15px;font-weight:700;color:#1f2328;margin-bottom:8px">'
            f'Detalhes — {escape(titulo_detalhes)}</div>'
            f'<table style="border-collapse:collapse;font-size:14px;max-width:760px">'
            f'<tr><th {th}>Companhia</th><th {th}>Ida</th><th {th}>Volta</th><th {th}>Dias</th>'
            f'<th {th}>Preço (casal)</th><th {th}>Link</th></tr>{corpo_tab}</table>'
        )
    rod = f'<p style="color:#57606a;font-size:12px;margin-top:20px">{escape(rodape)}</p>' if rodape else ""
    html = (f'<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328">'
            f'{quadro}{det}{rod}</body></html>')
    return "\n".join(txt), html


RODAPE = ("Total para 2 adultos, Econômica, GRU-BKK ida e volta, até 1 conexão em cada sentido, sem LATAM. "
          "Links somente para sites oficiais: a Qatar abre a busca com as datas exatas; nas demais, "
          "informe as datas na página da companhia. Preços das outras companhias vêm do Google Flights "
          "e podem variar um pouco no site oficial. Somente consulta.")


def agora() -> str:
    return f"{datetime.now():%d/%m/%Y %H:%M}"


def email_alerta(cfg: dict, abaixo: list[dict], prefixo: str = "") -> bool:
    m = abaixo[0]
    assunto = (f"{prefixo}ALERTA GRU-BKK abaixo de R${brl(cfg['limite']).split(',')[0]}: "
               f"menor R${brl(m['preco'])} {m['cia']} ({ddmm(m['ida'])}-{ddmm(m['volta'])})")
    itens = [
        ("Menor preço", f"R$ {brl(m['preco'])}"),
        ("Companhia", cia_txt(m)),
        ("Datas", datas_txt(m)),
        ("Economia", dif_limite(m["preco"], cfg["limite"])),
        ("Opções abaixo", f"{len(abaixo)} ({len({c['cia'] for c in abaixo})} companhia(s))"),
        ("Limite", f"R$ {brl(cfg['limite'])}"),
        ("Verificado em", agora()),
    ]
    corpo = montar_email("✈ PREÇO ABAIXO DO LIMITE", VERDE, itens, abaixo,
                         "opções abaixo do limite (da mais barata para a mais cara)", RODAPE)
    return enviar_email(cfg, assunto, corpo)


def email_resumo(cfg: dict, r: dict, exatos: list[dict], abaixo: list[dict], teste: bool = False) -> bool:
    """Resumo da execução: quadro com o essencial + menor preço por companhia."""
    falhas_txt = (f"falhas: Qatar {r['falhas_qatar']}/{len(PARES)}, Google {r['falhas_google']}/5"
                  if r["falhas_qatar"] or r["falhas_google"] else "funcionando")
    itens = [("Status", falhas_txt)]
    if exatos:
        m = exatos[0]
        itens += [
            ("Menor preço", f"R$ {brl(m['preco'])}"),
            ("Companhia", cia_txt(m)),
            ("Datas", datas_txt(m)),
            ("Situação", dif_limite(m["preco"], cfg["limite"])),
        ]
        if abaixo:
            itens.append(("Abaixo do limite", f"{len(abaixo)} opção(ões), já avisadas antes"))
    else:
        itens.append(("Menor preço", "nenhum encontrado"))
    itens += [
        ("Limite", f"R$ {brl(cfg['limite'])}"),
        ("Cobertura", f"Qatar {r['n_qatar']} células + Google {len(r['cal'])} datas"),
        ("Verificado em", agora()),
    ]
    if teste:
        titulo, assunto = "🧪 TESTE — Monitor GRU-BKK", "[TESTE] Monitor GRU-BKK funcionando"
    else:
        titulo = "📊 RESUMO — Monitor GRU-BKK"
        if exatos:
            m = exatos[0]
            sit = "ABAIXO do limite" if abaixo else "acima do limite"
            assunto = (f"Resumo GRU-BKK: menor R${brl(m['preco'])} {m['cia']} "
                       f"({ddmm(m['ida'])}-{ddmm(m['volta'])}), {sit}")
        else:
            assunto = "Resumo GRU-BKK: nenhum preço encontrado nesta hora"
    corpo = montar_email(titulo, AZUL, itens, por_cia(exatos), "menor preço encontrado por companhia", RODAPE)
    return enviar_email(cfg, assunto, corpo)


# ---------------------------------------------------------------------- main
def por_cia(exatos: list[dict]) -> list[dict]:
    """Menor preço de cada companhia."""
    m: dict[str, dict] = {}
    for c in exatos:
        if c["cia"] not in m or c["preco"] < m[c["cia"]]["preco"]:
            m[c["cia"]] = c
    return sorted(m.values(), key=lambda c: c["preco"])


def imprimir(r: dict, exatos: list[dict], limite: float) -> None:
    print("\n" + "=" * 78)
    print(f"Qatar: {r['n_qatar']} células, {len(PARES) - r['falhas_qatar']}/{len(PARES)} grades | "
          f"Google: {len(r['cal'])} datas no calendário, {r['confirmacoes']} confirmações, "
          f"{r['falhas_google']} falhas")
    if r["cal"]:
        (i, v), p = min(r["cal"].items(), key=lambda kv: kv[1])
        print(f"Menor no calendário Google (arredondado): ~R$ {brl(p)} em {i:%d/%m}-{v:%d/%m}")
    if exatos:
        print(f"Menor preço exato: R$ {brl(exatos[0]['preco'])} {cia_txt(exatos[0])}  |  limite: R$ {brl(limite)}")
        print("-" * 78)
        print("MENOR PREÇO POR COMPANHIA")
        for c in por_cia(exatos):
            marca = "  <== ABAIXO" if c["preco"] < limite else ""
            print(f"  {cia_txt(c):<32}{c['ida']:%d/%m}-{c['volta']:%d/%m} ({c['dias']}d)  "
                  f"R$ {brl(c['preco']):>12}{marca}")
    print("=" * 78 + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teste", action="store_true", help="roda uma vez, imprime e envia e-mail de teste")
    args = ap.parse_args()

    configurar_log()
    cfg = config()
    state = carregar_state()
    log.info("Início da execução%s (limite R$ %s)", " [TESTE]" if args.teste else "", brl(cfg["limite"]))

    try:
        r = coletar(cfg, state)
    except Exception as e:
        log.exception("Erro geral na coleta: %s", e)
        r = {"exatos": [], "cal": {}, "n_qatar": 0, "falhas_qatar": len(PARES),
             "falhas_google": DIAS_MAX - DIAS_MIN + 1, "confirmacoes": 0}

    exatos = sorted(r["exatos"], key=lambda c: (c["preco"], c["ida"], c["cia"]))
    salvar_csv(exatos, r["cal"])
    abaixo = [c for c in exatos if c["preco"] < cfg["limite"]]
    menor = exatos[0]["preco"] if exatos else None
    falhas = r["falhas_qatar"] + r["falhas_google"]
    # O Google falha parcialmente de vez em quando; só é "problema" se a Qatar falhar
    # ou se 3+ dos 5 calendários do Google falharem.
    problema = not exatos or r["falhas_qatar"] > 0 or r["falhas_google"] >= 3
    log.info("Resumo: %d preços exatos, %d datas no calendário Google, %d abaixo do limite, menor %s, falhas %d",
             len(exatos), len(r["cal"]), len(abaixo),
             f"R$ {brl(menor)} {exatos[0]['cia']}" if exatos else "-", falhas)

    if args.teste:
        imprimir(r, exatos, cfg["limite"])
        if abaixo:
            ok = email_alerta(cfg, abaixo, prefixo="[TESTE] ")
        else:
            ok = email_resumo(cfg, r, exatos, abaixo, teste=True)
        print("E-mail de teste:", "ENVIADO" if ok else "NÃO ENVIADO (veja logs/monitor.log)")
        salvar_state(state)
        return 0

    enviou = False  # algum e-mail (alerta, problema, voltou a subir) saiu nesta execução?

    # ---- controle de falhas
    hoje = date.today().isoformat()
    if problema:
        state["falhas_seguidas"] = state.get("falhas_seguidas", 0) + 1
        log.warning("Execução com falha (%d seguidas)", state["falhas_seguidas"])
        if state["falhas_seguidas"] >= 3 and state.get("ultimo_aviso_problema") != hoje:
            corpo = montar_email("⚠ MONITOR COM PROBLEMA", LARANJA, [
                ("Falhas seguidas", str(state["falhas_seguidas"])),
                ("Qatar", f"{r['n_qatar']} células, {r['falhas_qatar']}/{len(PARES)} grades com falha"),
                ("Google", f"{len(r['cal'])} datas, {r['falhas_google']}/5 calendários com falha"),
                ("Verificado em", agora()),
                ("Log e capturas", onde_ver()),
            ], rodape="Este aviso é enviado no máximo 1 vez por dia.")
            if enviar_email(cfg, "Monitor GRU-BKK com problema", corpo):
                state["ultimo_aviso_problema"] = hoje
                enviou = True
    else:
        state["falhas_seguidas"] = 0

    # ---- alertas de preço (anti-spam)
    if abaixo:
        chaves = {f"{c['ida']}|{c['volta']}|{c['cia']}" for c in abaixo}
        avisadas = set(state.get("combos_avisadas", []))
        ultimo = state.get("menor_avisado")
        novas = chaves - avisadas
        if ultimo is None or menor < ultimo or novas:
            log.info("Disparando alerta (menor %s, último avisado %s, opções novas %d)",
                     brl(menor), brl(ultimo) if ultimo else "-", len(novas))
            if email_alerta(cfg, abaixo):
                state["menor_avisado"] = menor
                state["combos_avisadas"] = sorted(avisadas | chaves)
                state["abaixo"] = True
                enviou = True
        else:
            log.info("Abaixo do limite, mas nada novo desde o último aviso (R$ %s)", brl(ultimo))
    elif state.get("abaixo") and not problema:
        # só conclui que subiu se as fontes carregaram
        corpo = montar_email("↑ PREÇO VOLTOU A SUBIR", VERMELHO, [
            ("Menor preço agora", f"R$ {brl(menor)}"),
            ("Companhia", cia_txt(exatos[0])),
            ("Datas", datas_txt(exatos[0])),
            ("Situação", dif_limite(menor, cfg["limite"])),
            ("Último avisado", f"R$ {brl(state.get('menor_avisado') or 0)}"),
            ("Verificado em", agora()),
        ], por_cia(exatos), "menor preço agora por companhia", RODAPE)
        if enviar_email(cfg, f"GRU-BKK voltou a subir: menor R${brl(menor)}", corpo):
            state.update(abaixo=False, menor_avisado=None, combos_avisadas=[])
            enviou = True

    # ---- resumo horário (quando nenhum outro e-mail saiu nesta execução)
    if cfg["sempre"] and not enviou:
        email_resumo(cfg, r, exatos, abaixo)

    state["ultima_execucao"] = datetime.now().isoformat(timespec="seconds")
    state["ultimo_menor"] = menor
    salvar_state(state)
    log.info("Fim da execução")
    return 0


if __name__ == "__main__":
    sys.exit(main())
