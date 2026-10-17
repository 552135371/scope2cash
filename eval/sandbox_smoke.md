# PayPal sandbox smoke (2026-10-17 21:12 CST)

Result: **PASS** · base https://api-m.sandbox.paypal.com

| step | result | detail |
|---|---|---|
| config | PASS | sandbox · client id set (80 chars) · buyer sb-demo-client@personal.example.com |
| oauth | PASS | client-credentials token issued |
| draft | PASS | INV2-C9D7-WCB7-NH59-LLRB number=0003 status=DRAFT |
| send | PASS | status=SENT |
| status | PASS | SENT -> app status SENT; payer link: https://www.sandbox.paypal.com/invoice/p/#C9D7WCB7NH59LLRB |
| webhook | WARN | no listener for */api/paypal/webhook (subscribed: none); deploy, then add it in the dashboard |
| app | SKIP | no --app-url / PUBLIC_BASE_URL |
