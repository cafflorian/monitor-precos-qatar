# Monitor de preços GRU ⇄ BKK

A cada hora, procura passagens de ida e volta GRU ⇄ BKK (Econômica, 2 adultos, idas de 15/03 a 30/04/2027, viagens de 14 a 18 dias), **com no máximo 1 conexão em cada sentido e sem LATAM**. Envia e-mail quando o total do casal fica abaixo do limite. **Só faz consulta**: nunca compra nem preenche dados.

Roda no **GitHub Actions** (24x7, sem depender do seu PC), com Chromium em modo visível sobre uma tela virtual (Xvfb).

## Fontes
| Fonte | O que faz | Acessos/hora |
|---|---|---|
| Site oficial da Qatar | 5 grades flexíveis com preços exatos (218 das 235 combinações) | 5 |
| Google Flights, calendário | Triagem de todas as companhias com até 1 parada, para cada duração de 14 a 18 dias (235 combinações; valores arredondados) | 5 |
| Google Flights, lista | Preço exato e companhia para as até 5 datas mais promissoras | até 5 |

Companhias vistas até agora: Qatar (DOH), Air France (CDG), Ethiopian (ADD), Turkish (IST), KLM (AMS), ITA (FCO), Emirates (DXB) e SWISS (ZRH). Itinerários vendidos pela LATAM e misturas com Econômica Premium são descartados.

**Links nos e-mails:** apontam só para sites oficiais, a partir de uma lista fixa em `gflights.py` (`OFICIAIS`). Nenhum link é copiado das páginas. A Qatar abre a busca com as datas exatas. Nas outras companhias, o link abre o site oficial e você informa as datas.

## 1. Senha de app do Gmail
1. Ative a verificação em duas etapas: https://myaccount.google.com/signinoptions/twosv
2. Abra https://myaccount.google.com/apppasswords, crie uma senha (por exemplo, `monitor-qatar`) e copie as 16 letras.

## 2. Segredos no GitHub (Settings → Secrets and variables → Actions)
| Tipo | Nome | Valor |
|---|---|---|
| Secret | `SMTP_USER` | Gmail que envia |
| Secret | `SMTP_APP_PASSWORD` | senha de app |
| Secret | `EMAIL_TO` | destinatários separados por vírgula |
| Variable (opcional) | `LIMITE` | padrão `17338.00` |

No terminal, use `gh secret set NOME -b "valor"`. No PowerShell, não use pipe (`|`), porque ele acrescenta um caractere invisível. Para rodar localmente, copie `.env.example` para `.env`.

## 3. Testar
`gh workflow run monitor.yml -f teste=true`, ou na aba **Actions → Monitor GRU-BKK → Run workflow**.

## 4. Logs e histórico
- `gh run list --workflow=monitor.yml`: lista as execuções.
- `gh run view <id> --log`: mostra o log de uma execução.
- `gh run download <id>`: baixa `historico.csv` (timestamp;fonte;companhia;ida;volta;dias;preco;exato), `state.json`, os logs e as capturas de falha. Na coluna `exato`, 0 indica os valores arredondados do calendário do Google.

## 5. Pausar, retomar e remover
```
gh workflow disable monitor.yml   # pausar
gh workflow enable  monitor.yml   # retomar
gh repo delete cafflorian/monitor-precos-qatar   # remover tudo
```

## Regras de alerta
- **Alerta**: há opção abaixo do limite (preço exato) que é nova (datas + companhia), ou o menor preço caiu em relação ao último aviso.
- **Voltou a subir**: depois de um alerta, todas as fontes carregaram e nada está abaixo do limite.
- **Problema**: 3 execuções seguidas com falha (no máximo 1 aviso por dia).
- O `keepalive.yml` faz um commit por mês para o GitHub não desativar o agendamento.
