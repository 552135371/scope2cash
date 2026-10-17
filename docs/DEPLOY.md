# Deploying Scope2Cash and going live on the PayPal sandbox

Everything here uses the **PayPal sandbox**. No real money moves.

## 1. Environment variables (Render dashboard or `docker run --env-file`)
| Variable | Required? | Value |
|---|---|---|
| `LLM_PROVIDER` | yes | `deepseek` (blueprint default). `nvidia` / `nebius` / `custom` also work |
| `DEEPSEEK_API_KEY` | yes, for live AI | DeepSeek key. Leave it empty and the AI steps run on offline heuristics with a visible warning |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | yes, for live PayPal | From developer.paypal.com > Apps & Credentials > **Sandbox** tab > your app |
| `PAYPAL_ENV` (or `PAYPAL_MODE`) | yes | `sandbox` |
| `PAYPAL_INVOICER_EMAIL` | recommended | The sandbox **business** account email (`sb-…@business.example.com`) |
| `PAYPAL_WEBHOOK_ID` | yes, for real webhooks | ID PayPal shows after you subscribe `https://<host>/api/paypal/webhook` (step 3) |
| `SCOPE2CASH_DEMO_CLIENT_EMAIL` | recommended | The sandbox **personal** (buyer) email, prefilled as the client |
| `SCOPE2CASH_MOCK_WEBHOOK_SECRET` | yes | Random (the blueprint generates it) |
| `SCOPE2CASH_PUBLIC_DEMO`, `SCOPE2CASH_DAILY_LIVE_CAP`, `SCOPE2CASH_RECIPIENT_DOMAINS` | blueprint defaults | `1`, `150`, `example.com` (abuse limits for a public URL) |
| `TAVILY_API_KEY` | optional | Live market-rate research; without it, labelled sample rates are used |
| `SCOPE2CASH_DB` | blueprint default | `/tmp/scope2cash.db` (the free tier's disk is ephemeral) |

Optional tuning (`MODEL_*`, `LLM_EFFORT_*`, `LLM_TIMEOUT_S`, `APP_CURRENCY`, …) is documented in `.env.example`. Render sets `$PORT`, and the Dockerfile honours it.

## 2. Deploy
- Render: New > Blueprint > pick this repo. Fill in the `sync: false` secrets when Render prompts for them. The health check is `/healthz`.
- Anywhere else: `docker build -t scope2cash . && docker run -p 8000:8000 --env-file .env scope2cash`.
- Check `https://<host>/api/health`. `adapters.payments.mode` should say `live` once the PayPal keys are set.

## 3. Subscribe the webhook
1. developer.paypal.com > your sandbox app > Webhooks > Add webhook.
2. URL: `https://<host>/api/paypal/webhook`. Events: `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.CANCELLED`, `INVOICING.INVOICE.REFUNDED`, `INVOICING.INVOICE.UPDATED`.
3. Copy the webhook ID into `PAYPAL_WEBHOOK_ID` and redeploy. `/api/health` should now show `webhooks.mode = live`.

Free Render instances sleep after about 15 minutes idle, so the first request after a sleep takes roughly 30–60 s. If that first delivery times out, PayPal retries it. Before recording a demo, open the app once to wake it.

## 4. One-command check
```bash
export PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... PAYPAL_ENV=sandbox
export PAYPAL_INVOICER_EMAIL=sb-...@business.example.com PAYPAL_BUYER_EMAIL=sb-...@personal.example.com
python scripts/paypal_sandbox_smoke.py --app-url https://<host> --require-webhook
# optional: --wait-paid 600, then pay the printed link while logged in as the sandbox buyer
```
The script creates one $1.00 sandbox invoice, sends it, reads its status back, checks the webhook subscription and the deployed app's `/api/health`, and writes `eval/sandbox_smoke.md`. It refuses to run against live PayPal or to send to an address that isn't a sandbox one. Secrets are never printed.

## 5. Judge access
Create a dedicated sandbox **personal** account for judges (Developer Dashboard > Testing Tools > Sandbox Accounts > Create account, US, with a PayPal balance). Put its email and password in the Devpost testing instructions. It is a sandbox account and cannot move real money.
