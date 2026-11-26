# Monitor de preços Qatar GRU ⇄ BKK

Consulta a cada hora o calendário flexível da Qatar Airways (Economy, 2 adultos, idas de 15/03 a 30/04/2027, 14 a 18 dias de viagem) e envia e-mail quando o total do casal fica abaixo do limite. **Só faz consulta**: não clica em nada nem preenche dados.

Roda no **GitHub Actions** (24x7, sem depender do seu PC), com Chromium em modo visível sobre uma tela virtual (Xvfb).

## 1. Gerar a senha de app do Gmail
1. Ative a **verificação em duas etapas**: https://myaccount.google.com/signinoptions/twosv
2. Abra https://myaccount.google.com/apppasswords
3. Dê um nome (ex.: `monitor-qatar`) e clique em **Criar**.
4. Copie a senha de 16 letras (os espaços não importam). Ela só aparece uma vez.

## 2. Configurar os segredos no GitHub
No repositório, vá em **Settings → Secrets and variables → Actions**:

| Tipo | Nome | Valor |
|---|---|---|
| Secret | `SMTP_USER` | e-mail Gmail que envia |
| Secret | `SMTP_APP_PASSWORD` | senha de app do passo 1 |
| Secret | `EMAIL_TO` | destinatários separados por vírgula |
| Variable (opcional) | `LIMITE` | padrão `17338.00` |
| Variable (opcional) | `BROWSER_CHANNEL` | `chromium` (padrão) ou `chrome` |

Pelo terminal: `gh secret set SMTP_APP_PASSWORD` (pede o valor sem mostrar na tela).

Para rodar localmente, copie `.env.example` para `.env` e preencha.

## 3. Testar
- GitHub: aba **Actions → Monitor Qatar GRU-BKK → Run workflow** (com "teste" marcado). Ou: `gh workflow run monitor.yml -f teste=true`
- Local: `.venv\Scripts\python monitor.py --teste`

## 4. Ver logs e histórico
- `gh run list --workflow=monitor.yml` lista as execuções; `gh run view <id> --log` mostra o log.
- Cada execução publica o artefato **historico-e-logs** com `historico.csv` (timestamp;ida;volta;dias;preco), `state.json`, o log com rotação e as capturas de tela das falhas. Para baixar: `gh run download <id>`.
- O estado (anti-spam, histórico, perfil do navegador) fica guardado no cache do Actions entre as execuções.

## 5. Pausar, retomar e remover
```
gh workflow disable monitor.yml   # pausar
gh workflow enable  monitor.yml   # retomar
gh repo delete <usuario>/<repo>   # remover tudo
```

## Regras de alerta
- **Alerta**: há combinação abaixo do limite e ela é nova, ou o menor preço caiu em relação ao último aviso.
- **Voltou a subir**: depois de um alerta, todas as 5 grades carregaram e nada está abaixo do limite.
- **Problema**: 3 execuções seguidas com falha ou grade vazia (no máximo 1 aviso por dia).
- O GitHub pode atrasar o cron alguns minutos. O `keepalive.yml` faz um commit por mês para o GitHub não desativar o agendamento.
