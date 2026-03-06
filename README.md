# Orchestro — x402 Payment Demo

A minimal demo of the [x402 HTTP payment protocol](https://x402.org) using [Nevermined](https://nevermined.app) for AI agent monetization.

## How it works

Requests to protected routes require a payment token. Without one, the server returns `402 Payment Required`. The client resolves the payment scheme, generates an access token via the Nevermined SDK, and retries the request.

```
Client → POST /ask (no token)      → 402 + payment-required header
Client → resolves scheme + gets token
Client → POST /ask (with token)    → 200 OK
Server → burns credits asynchronously
```

## Setup

```bash
uv sync
cp .env.example .env   # fill in NVM_API_KEY and NVM_PLAN_ID
```

**.env**
```
NVM_API_KEY=your_key_here
NVM_PLAN_ID=your_plan_id_here
NVM_ENVIRONMENT=sandbox        # or "live" for production
SERVER_URL=http://localhost:3000
```

## Running

**Start the server:**
```bash
uv run agent.py
```

**Run the client demo:**
```bash
uv run client.py
```

## Project structure

| File | Description |
|------|-------------|
| `agent.py` | FastAPI server with `PaymentMiddleware` protecting `POST /ask` |
| `client.py` | Demo client showing the full x402 handshake |

## Payment flow

| Step | Expected output | Failure signs |
|------|----------------|---------------|
| 1. Request without token | `402 Payment Required` + `payment-required` header | `404`/`500`, connection refused, or `200` without a token (middleware not attached) |
| 2. Decode header | Valid base64 JSON with `scheme`, `network`, `planId` | Header absent, not valid base64, or `planId` is null |
| 3. Resolve scheme & generate token | `nvm:card-delegation`, token ~1636 chars, card shown as `visa *4242` | Invalid/expired `NVM_API_KEY`, no enrolled payment method, wrong `NVM_PLAN_ID`, env mismatch (`sandbox` vs `live`) |
| 4. Request with token | `200 OK`, `{"response": "Success: Query received"}` | `402` again — token expired, already used, wrong plan, or wrong route/verb |
| 5. Settlement header | `success: true`, `creditsRedeemed: "1"`, `remainingBalance` decrements | `success: false` with `errorReason`, header absent (settlement is async), or `remainingBalance: 0` (plan exhausted) |

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- A [Nevermined](https://nevermined.app) account with an active plan and enrolled payment method
