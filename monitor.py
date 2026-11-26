"""
Monitor de preços Qatar Airways GRU <-> BKK (Economy, 2 adultos).

Somente CONSULTA: abre o calendário flexível, lê os aria-labels das células
e nunca clica em nada, nunca preenche dados pessoais ou de pagamento.

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
from datetime import date, datetime
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
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(fh)
    if sys.stdout is not None:  # pythonw não tem console
        log.addHandler(sh)


def brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def ddmm(d: date) -> str:
    return d.strftime("%d/%m")


def url_busca(ida: date | str, volta: date | str, flex: bool) -> str:
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
    }


# -------------------------------------------------------------------- coleta
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


def buscar(ctx, ida: str, volta: str) -> list[tuple[date, date, float]]:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    url = url_busca(ida, volta, flex=True)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        n = esperar_grade(page)
        labels = page.eval_on_selector_all(SELETOR, "els => els.map(e => e.getAttribute('aria-label'))")
        celulas = parse_labels(labels)
        log.info("Busca %s/%s: %d elementos, %d células válidas (url final: %s)",
                 ida, volta, n, len(celulas), page.url.split("?")[0])
        if not celulas:
            salvar_debug(page, ida)
        return celulas
    except Exception as e:
        log.error("Erro na busca %s/%s: %s", ida, volta, e)
        salvar_debug(page, ida)
        return []


def salvar_debug(page, nome: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"falha_{nome}.png"), full_page=False)
    except Exception:
        pass


def coletar(cfg: dict, state: dict) -> tuple[list[tuple[date, date, float]], int, int]:
    """Retorna (células brutas, total de células, número de buscas que falharam)."""
    from playwright.sync_api import sync_playwright

    canal = state.get("canal") or cfg["canal"]
    todas: list[tuple[date, date, float]] = []
    falhas = 0
    with sync_playwright() as p:
        ctx = abrir_contexto(p, canal)
        try:
            for i, (ida, volta) in enumerate(PARES):
                if i > 0:
                    pausa = random.uniform(5, 15)
                    log.info("Aguardando %.1f s", pausa)
                    time.sleep(pausa)
                celulas = buscar(ctx, ida, volta)
                if not celulas and i == 0 and canal != "chrome":
                    log.warning("Grade vazia com %s; tentando o Chrome instalado (channel=chrome)", canal)
                    ctx.close()
                    canal = "chrome"
                    ctx = abrir_contexto(p, canal)
                    time.sleep(random.uniform(5, 15))
                    celulas = buscar(ctx, ida, volta)
                    if celulas:
                        state["canal"] = "chrome"
                if not celulas:
                    falhas += 1
                todas.extend(celulas)
        finally:
            ctx.close()
    return todas, len(todas), falhas


def filtrar(celulas: list[tuple[date, date, float]]) -> list[dict]:
    melhor: dict[tuple[date, date], float] = {}
    for ida, volta, preco in celulas:
        dias = (volta - ida).days
        if IDA_MIN <= ida <= IDA_MAX and DIAS_MIN <= dias <= DIAS_MAX:
            k = (ida, volta)
            melhor[k] = min(preco, melhor.get(k, preco))
    combos = [
        {"ida": k[0], "volta": k[1], "dias": (k[1] - k[0]).days, "preco": v}
        for k, v in melhor.items()
    ]
    return sorted(combos, key=lambda c: (c["preco"], c["ida"]))


def salvar_csv(combos: list[dict]) -> None:
    DATA.mkdir(exist_ok=True)
    novo = not CSV_FILE.exists()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if novo:
            w.writerow(["timestamp", "ida", "volta", "dias", "preco"])
        for c in combos:
            w.writerow([ts, c["ida"].isoformat(), c["volta"].isoformat(), c["dias"], f"{c['preco']:.2f}"])


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


def linhas_combos(combos: list[dict]) -> str:
    linhas = []
    for c in combos:
        linhas.append(
            f"- Ida {c['ida']:%d/%m/%Y} | Volta {c['volta']:%d/%m/%Y} | {c['dias']} dias | "
            f"R$ {brl(c['preco'])} (casal)\n  {url_busca(c['ida'], c['volta'], flex=False)}"
        )
    return "\n".join(linhas)


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
    # ---- texto puro
    largura = max([len(titulo)] + [len(k) + len(v) + 2 for k, v in itens]) + 4
    txt = ["+" + "-" * largura + "+", f"| {titulo.ljust(largura - 2)} |", "+" + "-" * largura + "+"]
    txt += [f"| {(k + ': ' + v).ljust(largura - 2)} |" for k, v in itens]
    txt.append("+" + "-" * largura + "+")
    if combos:
        txt += ["", "", f"DETALHES - {titulo_detalhes}", "", linhas_combos(combos)]
    if rodape:
        txt += ["", rodape]
    # ---- html
    linhas = "".join(
        f'<tr><td style="padding:3px 16px 3px 0;color:#57606a;white-space:nowrap">{escape(k)}</td>'
        f'<td style="padding:3px 0;font-weight:600;color:#1f2328">{escape(v)}</td></tr>'
        for k, v in itens
    )
    quadro = (
        f'<div style="border:2px solid {cor};border-left-width:8px;border-radius:8px;padding:16px 20px;'
        f'max-width:600px;background:#f6f8fa;margin-bottom:28px">'
        f'<div style="font-size:19px;font-weight:700;color:{cor};margin-bottom:10px">{escape(titulo)}</div>'
        f'<table style="border-collapse:collapse;font-size:15px">{linhas}</table></div>'
    )
    det = ""
    if combos:
        th = 'style="text-align:left;padding:6px 10px;border-bottom:2px solid #d0d7de;color:#57606a"'
        td = 'style="padding:6px 10px;border-bottom:1px solid #eaeef2"'
        corpo_tab = "".join(
            f'<tr><td {td}>{c["ida"]:%d/%m/%Y}</td><td {td}>{c["volta"]:%d/%m/%Y}</td>'
            f'<td {td} align="center">{c["dias"]}</td>'
            f'<td {td} align="right"><b>R$ {brl(c["preco"])}</b></td>'
            f'<td {td}><a href="{escape(url_busca(c["ida"], c["volta"], flex=False))}">abrir busca</a></td></tr>'
            for c in combos
        )
        det = (
            f'<div style="font-size:15px;font-weight:700;color:#1f2328;margin-bottom:8px">'
            f'Detalhes — {escape(titulo_detalhes)}</div>'
            f'<table style="border-collapse:collapse;font-size:14px;max-width:600px">'
            f'<tr><th {th}>Ida</th><th {th}>Volta</th><th {th}>Dias</th><th {th}>Preço (casal)</th>'
            f'<th {th}>Link</th></tr>{corpo_tab}</table>'
        )
    rod = f'<p style="color:#57606a;font-size:12px;margin-top:20px">{escape(rodape)}</p>' if rodape else ""
    html = (f'<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328">'
            f'{quadro}{det}{rod}</body></html>')
    return "\n".join(txt), html


RODAPE = ("Total para 2 adultos, Economy, GRU-BKK ida e volta. Os links abrem a busca "
          "com as datas exatas (flexibleDate=false). Somente consulta.")


def agora() -> str:
    return f"{datetime.now():%d/%m/%Y %H:%M}"


def email_alerta(cfg: dict, abaixo: list[dict], prefixo: str = "") -> bool:
    m = abaixo[0]
    assunto = (f"{prefixo}ALERTA Qatar GRU-BKK abaixo de R${brl(cfg['limite']).split(',')[0]}: "
               f"menor R${brl(m['preco'])} ({ddmm(m['ida'])}-{ddmm(m['volta'])})")
    itens = [
        ("Menor preço", f"R$ {brl(m['preco'])}"),
        ("Datas", datas_txt(m)),
        ("Economia", dif_limite(m["preco"], cfg["limite"])),
        ("Combinações abaixo", str(len(abaixo))),
        ("Limite", f"R$ {brl(cfg['limite'])}"),
        ("Verificado em", agora()),
    ]
    corpo = montar_email("✈ PREÇO ABAIXO DO LIMITE", VERDE, itens, abaixo,
                         "combinações abaixo do limite (da mais barata para a mais cara)", RODAPE)
    return enviar_email(cfg, assunto, corpo)


# ---------------------------------------------------------------------- main
def imprimir(combos: list[dict], limite: float, n_celulas: int, falhas: int) -> None:
    print("\n" + "=" * 72)
    print(f"Células lidas: {n_celulas} | buscas com falha: {falhas}/{len(PARES)} | "
          f"combinações válidas (ida 15/03-30/04, 14-18 dias): {len(combos)}")
    if combos:
        print(f"Menor preço: R$ {brl(combos[0]['preco'])}  |  limite: R$ {brl(limite)}")
        print("-" * 72)
        print(f"{'IDA':<12}{'VOLTA':<12}{'DIAS':>5}{'PREÇO (casal)':>20}")
        for c in combos[:20]:
            marca = "  <== ABAIXO" if c["preco"] < limite else ""
            print(f"{c['ida']:%d/%m/%Y}  {c['volta']:%d/%m/%Y}  {c['dias']:>4}   R$ {brl(c['preco']):>14}{marca}")
        if len(combos) > 20:
            print(f"... (+{len(combos) - 20} combinações)")
    print("=" * 72 + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teste", action="store_true", help="roda uma vez, imprime e envia e-mail de teste")
    args = ap.parse_args()

    configurar_log()
    cfg = config()
    state = carregar_state()
    log.info("Início da execução%s (limite R$ %s)", " [TESTE]" if args.teste else "", brl(cfg["limite"]))

    try:
        celulas, n_celulas, falhas = coletar(cfg, state)
    except Exception as e:
        log.exception("Erro geral na coleta: %s", e)
        celulas, n_celulas, falhas = [], 0, len(PARES)

    combos = filtrar(celulas)
    if combos:
        salvar_csv(combos)
    abaixo = [c for c in combos if c["preco"] < cfg["limite"]]
    menor = combos[0]["preco"] if combos else None
    log.info("Resumo: %d células, %d combinações válidas, %d abaixo do limite, menor %s, falhas %d",
             n_celulas, len(combos), len(abaixo), f"R$ {brl(menor)}" if menor else "-", falhas)

    if args.teste:
        imprimir(combos, cfg["limite"], n_celulas, falhas)
        if abaixo:
            ok = email_alerta(cfg, abaixo, prefixo="[TESTE] ")
        else:
            itens = [("Status", "funcionando" if falhas == 0 else f"{falhas}/{len(PARES)} buscas com falha")]
            if combos:
                itens += [
                    ("Menor preço", f"R$ {brl(menor)}"),
                    ("Datas", datas_txt(combos[0])),
                    ("Situação", dif_limite(menor, cfg["limite"])),
                ]
            else:
                itens.append(("Menor preço", "nenhum encontrado"))
            itens += [
                ("Limite", f"R$ {brl(cfg['limite'])}"),
                ("Células lidas", f"{n_celulas} ({len(combos)} combinações válidas)"),
                ("Verificado em", agora()),
            ]
            corpo = montar_email("🧪 TESTE — Monitor Qatar GRU-BKK", AZUL, itens, combos[:10],
                                 "10 combinações mais baratas", RODAPE)
            ok = enviar_email(cfg, "[TESTE] Monitor Qatar GRU-BKK funcionando", corpo)
        print("E-mail de teste:", "ENVIADO" if ok else "NÃO ENVIADO (veja logs/monitor.log)")
        salvar_state(state)  # guarda apenas o canal do navegador, se mudou
        return 0

    # ---- controle de falhas
    hoje = date.today().isoformat()
    if n_celulas == 0 or falhas > 0:
        state["falhas_seguidas"] = state.get("falhas_seguidas", 0) + 1
        log.warning("Execução com falha (%d seguidas)", state["falhas_seguidas"])
        if state["falhas_seguidas"] >= 3 and state.get("ultimo_aviso_problema") != hoje:
            corpo = montar_email("⚠ MONITOR COM PROBLEMA", LARANJA, [
                ("Falhas seguidas", str(state["falhas_seguidas"])),
                ("Última execução", f"{n_celulas} células lidas, {falhas}/{len(PARES)} buscas com falha"),
                ("Verificado em", agora()),
                ("Log e capturas", onde_ver()),
            ], rodape="Este aviso é enviado no máximo 1 vez por dia.")
            if enviar_email(cfg, "Monitor Qatar GRU-BKK com problema", corpo):
                state["ultimo_aviso_problema"] = hoje
    else:
        state["falhas_seguidas"] = 0

    # ---- alertas de preço (anti-spam)
    if abaixo:
        chaves = {f"{c['ida']}|{c['volta']}" for c in abaixo}
        avisadas = set(state.get("combos_avisadas", []))
        ultimo = state.get("menor_avisado")
        novas = chaves - avisadas
        if ultimo is None or menor < ultimo or novas:
            log.info("Disparando alerta (menor %s, último avisado %s, combinações novas %d)",
                     brl(menor), brl(ultimo) if ultimo else "-", len(novas))
            if email_alerta(cfg, abaixo):
                state["menor_avisado"] = menor
                state["combos_avisadas"] = sorted(avisadas | chaves)
                state["abaixo"] = True
        else:
            log.info("Abaixo do limite, mas nada novo desde o último aviso (R$ %s)", brl(ultimo))
    elif state.get("abaixo") and falhas == 0 and combos:
        # só conclui que subiu se todas as 5 grades carregaram
        corpo = montar_email("↑ PREÇO VOLTOU A SUBIR", VERMELHO, [
            ("Menor preço agora", f"R$ {brl(menor)}"),
            ("Datas", datas_txt(combos[0])),
            ("Situação", dif_limite(menor, cfg["limite"])),
            ("Último avisado", f"R$ {brl(state.get('menor_avisado') or 0)}"),
            ("Verificado em", agora()),
        ], combos[:5], "5 combinações mais baratas agora", RODAPE)
        if enviar_email(cfg, f"Qatar GRU-BKK voltou a subir: menor R${brl(menor)}", corpo):
            state.update(abaixo=False, menor_avisado=None, combos_avisadas=[])

    state["ultima_execucao"] = datetime.now().isoformat(timespec="seconds")
    state["ultimo_menor"] = menor
    salvar_state(state)
    log.info("Fim da execução")
    return 0


if __name__ == "__main__":
    sys.exit(main())
