"""
discovery_buy_tracked.py — Mass buyer with full spend tracking + requirements analysis.

Outputs:
  spend_log.json         — every purchase attempt with currency, amount, datetime
  successful_calls.json  — successful endpoints + what they returned
  needs_info.json        — failed endpoints categorized by WHY they failed and what they need

Run: uv run discovery_buy_tracked.py
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

import httpx
from payments_py import Payments, PaymentOptions
from payments_py.x402.fastapi import X402_HEADERS
from payments_py.x402.types import CardDelegationConfig, X402TokenOptions

# ── Config ────────────────────────────────────────────────────────────────────
NVM_API_KEY     = os.getenv("NVM_API_KEY", "")
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
RAW_JSON_PATH   = "discovery_raw.json"

TEST_MODE         = False
CRYPTO_BUDGET_USD = 20.0
REQUEST_TIMEOUT   = 45.0
MY_TEAM_ID        = "0x659c87f82dd0e194ef17067398ebdb6ee1e13524"

if not NVM_API_KEY:
    print("ERROR: NVM_API_KEY missing")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)

# ── Failure categories for needs_info.json ───────────────────────────────────
# Each entry describes WHY it failed and WHAT is needed to fix it.
FAILURE_CATEGORIES = {
    # wrong field name — we sent "query" but they want "message"
    "WRONG_BODY_FIELD": {
        "fixable": True,
        "fix_type": "body_field_rename",
        "description": "Endpoint requires a specific field name we didn't send",
    },
    # needs a separate API key / Bearer token in Authorization header
    "NEEDS_OWN_AUTH": {
        "fixable": False,
        "fix_type": "external_api_key",
        "description": "Endpoint uses its own auth system on top of x402 — needs their API key",
    },
    # wrong HTTP method (they want GET, or different path)
    "WRONG_METHOD_OR_PATH": {
        "fixable": True,
        "fix_type": "try_get_or_different_path",
        "description": "405 Not Allowed — endpoint may need GET or a different URL path",
    },
    # server-side crash — their bug, not ours
    "SERVER_ERROR": {
        "fixable": False,
        "fix_type": "wait_for_seller_fix",
        "description": "500 Internal Server Error — seller's service is broken",
    },
    # returned 200 but with an error payload (counted as success by HTTP but not semantic success)
    "SEMANTIC_ERROR": {
        "fixable": True,
        "fix_type": "send_jsonrpc_method_field",
        "description": "Got 200 but response contains error — likely needs JSON-RPC method field",
    },
    # crypto plan token error — wallet not funded in sandbox
    "CRYPTO_TOKEN_ERROR": {
        "fixable": True,
        "fix_type": "fund_sandbox_usdc_wallet",
        "description": "Token error for crypto plan — sandbox USDC wallet likely needs funding",
    },
    # sender domain not authorized (Nevermailed)
    "SENDER_NOT_AUTHORIZED": {
        "fixable": True,
        "fix_type": "use_authorized_sender_domain",
        "description": "Email sender domain not authorized — need to use nevermailed.com domain",
    },
    # DNS/network failure — tunnel or server is down
    "NETWORK_DOWN": {
        "fixable": False,
        "fix_type": "wait_for_seller_to_restart",
        "description": "DNS resolution failed — ngrok/cloudflare tunnel is expired or server is down",
    },
    # checkout page, not an API
    "NOT_AN_API": {
        "fixable": False,
        "fix_type": "skip",
        "description": "URL is a web checkout page, not an API endpoint",
    },
}

def classify_failure(note: str, endpoint: str, response_body: str = "") -> dict:
    """Classify a failure and return what's needed to fix it."""

    if "Token error: Invalid access token" in note:
        return {
            "category": "CRYPTO_TOKEN_ERROR",
            "needs": [
                "Fund sandbox USDC wallet at https://nevermined.app",
                "Ensure crypto plan is subscribed before calling get_x402_access_token",
            ],
            "affected_field": None,
            **FAILURE_CATEGORIES["CRYPTO_TOKEN_ERROR"],
        }

    if "Skipped (checkout" in note:
        return {
            "category": "NOT_AN_API",
            "needs": ["This is a web checkout page — skip or handle via browser"],
            "affected_field": None,
            **FAILURE_CATEGORIES["NOT_AN_API"],
        }

    if "nodename nor servname" in note or "Request error" in note:
        return {
            "category": "NETWORK_DOWN",
            "needs": ["Contact seller to restart their tunnel/server"],
            "affected_field": None,
            **FAILURE_CATEGORIES["NETWORK_DOWN"],
        }

    if "HTTP 405" in note:
        return {
            "category": "WRONG_METHOD_OR_PATH",
            "needs": [
                "Try GET request instead of POST",
                "Try appending /query, /ask, /run to the base URL",
                "Check seller's apiSchema for correct path",
            ],
            "affected_field": None,
            **FAILURE_CATEGORIES["WRONG_METHOD_OR_PATH"],
        }

    if "HTTP 401" in note and "Authorization" in response_body:
        return {
            "category": "NEEDS_OWN_AUTH",
            "needs": [
                "Obtain seller-specific API key (e.g. sabi_sk_... for Sabi)",
                "Add Authorization: Bearer <key> header alongside PAYMENT-SIGNATURE",
            ],
            "affected_field": "Authorization header",
            **FAILURE_CATEGORIES["NEEDS_OWN_AUTH"],
        }

    if "HTTP 401" in note:
        return {
            "category": "NEEDS_OWN_AUTH",
            "needs": [
                "Endpoint requires additional Authorization header beyond x402 token",
                "Contact seller for their auth scheme",
            ],
            "affected_field": "Authorization header",
            **FAILURE_CATEGORIES["NEEDS_OWN_AUTH"],
        }

    if "HTTP 422" in note or ("Field required" in note and "missing" in note):
        # Extract the missing field name from the error
        missing_field = None
        try:
            err = json.loads(note.split("HTTP 422: ", 1)[-1])
            locs = [d.get("loc", []) for d in err.get("detail", [])]
            missing_field = ", ".join(str(l[-1]) for l in locs if l)
        except Exception:
            pass
        return {
            "category": "WRONG_BODY_FIELD",
            "needs": [
                f"Add required field: '{missing_field}'" if missing_field else "Add missing required field",
                "Check seller's apiSchema requestBody example for exact field names",
            ],
            "affected_field": missing_field,
            **FAILURE_CATEGORIES["WRONG_BODY_FIELD"],
        }

    if "HTTP 400" in note and "endpoint_url" in note:
        return {
            "category": "WRONG_BODY_FIELD",
            "needs": ["Send {'endpoint_url': '<target_url>'} in body"],
            "affected_field": "endpoint_url",
            **FAILURE_CATEGORIES["WRONG_BODY_FIELD"],
        }

    if "HTTP 500" in note and "authorized" in note.lower():
        return {
            "category": "SENDER_NOT_AUTHORIZED",
            "needs": [
                "Use a sender address from an authorized domain",
                "For Nevermailed: use 'agent@nevermailed.com' or register your domain",
            ],
            "affected_field": "from",
            **FAILURE_CATEGORIES["SENDER_NOT_AUTHORIZED"],
        }

    if "HTTP 500" in note:
        return {
            "category": "SERVER_ERROR",
            "needs": ["Wait for seller to fix their service", "Report to seller team"],
            "affected_field": None,
            **FAILURE_CATEGORIES["SERVER_ERROR"],
        }

    if '"error"' in response_body and ('"code"' in response_body or "jsonrpc" in response_body):
        return {
            "category": "SEMANTIC_ERROR",
            "needs": [
                "Send JSON-RPC 2.0 format: {jsonrpc, method, params, id}",
                "Add 'method' field to request body",
            ],
            "affected_field": "method",
            **FAILURE_CATEGORIES["SEMANTIC_ERROR"],
        }

    return {
        "category": "UNKNOWN",
        "needs": [f"Raw error: {note[:200]}"],
        "affected_field": None,
        "fixable": False,
        "fix_type": "investigate",
        "description": "Unclassified failure",
    }


# ── Body overrides ────────────────────────────────────────────────────────────
BODY_OVERRIDES = {
    "agentaudit.onrender.com/audit": {
        "query": "I want to grow my e-commerce business using AI agents",
    },
    "api.mog.markets/mcp": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "find_service", "arguments": {"query": "AI research agent"}},
    },
    "trust-net-mcp": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "submit_review", "arguments": {
            "agent_id": "orchestro",
            "reviewer_address": "0x659c87f82dd0e194ef17067398ebdb6ee1e13524",
            "verification_tx": "0x0000000000000000000000000000000000000000000000000000000000000001",
            "score": 5,
        }},
    },
    "platon.bigf.me/mcp": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "store_memory", "arguments": {
            "key": "orchestro_test",
            "value": "Orchestro bought from Platon Memory at the Nevermined hackathon.",
        }},
    },
    "ngrok-free.dev/ask": {
        "need": "research",
        "query": "What AI agent services are available on the Nevermined marketplace?",
    },
    "api.mindra.co/v1/workflows/cortex": {
        "task": "Summarize the key benefits of agent-to-agent payments using x402.",
        "metadata": {},
    },
    "api.mindra.co/v1/workflows/social-media-manager": {
        "query": "Create a tweet about AI agents paying each other autonomously",
        "format": "json",
    },
    "agentbank-nine.vercel.app/api/deposit": {
        "agent_id": "did:nvm:orchestro",
        "amount": 1,
        "tx_hash": "0x0000000000000000000000000000000000000000",
    },
    "ai-celebrity-economy.vercel.app": {
        "topic": "AI agent payments", "brand": "Orchestro",
        "product_url": "https://orchestro.vercel.app",
        "audience": "AI developers", "tone": "helpful",
    },
    "agenticard-ai.manus.space/api/v1/enhance": {"cardId": 1, "agentId": "1"},
    "sabi-backend": {
        "question": "Is the Nevermined hackathon still running?",
        "targetLat": 37.7749, "targetLng": -122.4194,
    },
    "us14.abilityai.dev/ask": {
        "query": "What are the latest geopolitical risks affecting AI regulation in 2026?",
        "format": "json",
    },
    # QA Checker needs "message" not "query"
    "us14.abilityai.dev/api/paid/qa-checker": {
        "message": "Fact-check: The x402 protocol enables AI agents to pay each other using HTTP headers.",
        "format": "json",
    },
    # Nexus and Market Intel also use "message"
    "us14.abilityai.dev/api/paid/nexus": {
        "message": "What is the competitive landscape for AI agent marketplaces in 2026?",
        "format": "json",
    },
    "us14.abilityai.dev/api/paid/market-intel": {
        "message": "Give me a company profile for Nevermined.",
        "format": "json",
    },
    "airi-demo.replit.app": {"company": "Salesforce"},
    "api.cloudagi.org/v1/services/code-review": {
        "code": "def add(a, b):\n    return a + b",
        "language": "python", "focus": ["bugs", "security"],
    },
    "api.cloudagi.org/v1/services/smart-search": {
        "query": "Nevermined x402 protocol agent payments",
        "sources": ["exa"], "numResults": 3,
    },
    "api.cloudagi.org/v1/services/ai-research": {
        "query": "agent to agent payments blockchain 2026",
        "type": "auto", "numResults": 3,
    },
    "api.cloudagi.org/v1/services/web-scraper": {
        "url": "https://nevermined.io", "maxPages": 1,
    },
    "api.cloudagi.org/v1/services/gpu-compute": {
        "gpu": "T4", "image": "python:3.13",
        "command": ["python3", "-c", "print('Hello from Orchestro!')"],
        "timeoutSecs": 60,
    },
    "switchboardai.ayushojha.com/api/dataforge-web": {
        "url": "https://nevermined.io", "tier": "basic",
        "intent": "Extract main content and description",
    },
    "switchboardai.ayushojha.com/api/dataforge-search": {
        "query": "agent to agent payments x402", "tier": "quick",
    },
    "switchboardai.ayushojha.com/api/procurepilot": {
        "query": "Research the current state of AI agent payment protocols in 2026",
        "budget": 5,
    },
    "nevermailed.com/api/send": {
        "from": "Orchestro Agent <agent@nevermailed.com>",
        "to": "test@nevermailed.com",
        "subject": "Hello from Orchestro — Nevermined Hackathon",
        "text": "Sent autonomously by the Orchestro agent via x402.",
        "html": "<p>Sent by <strong>Orchestro</strong> via x402.</p>",
    },
    "hack-mined-production.up.railway.app/research": {
        "query": "What is the x402 payment protocol?",
    },
    "hackathons-production": {
        "query": "Orchestro orchestration agent", "format": "json",
    },
    "13.217.131.34:3000/data": {
        "query": "Which agents have the best ROI?",
    },
    "54.183.4.35:9030": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    },
    "54.183.4.35:9020": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    },
    "54.183.4.35:9010": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    },
    "54.183.4.35:9040": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    },
    "supabase.co/functions/v1/agent-predict": {
        "query": "Predict AI agent services market trend Q2 2026",
        "asset": "AI_AGENTS",
    },
    "ngrok-free.app/query": {
        "prompt": "hello from Orchestro",
        "question": "Can you confirm you received this x402 payment?",
    },
    "trycloudflare.com": {
        "query": "Infrastructure routing for AI orchestration platform?",
    },
    "nevermined.app/checkout": None,  # skip
}

LOCAL_PATTERNS = ["localhost", "127.0.0.1", "0.0.0.0", "http://seller:"]

def clean_endpoint_url(url: str) -> str:
    """Fix common URL issues like trailing periods."""
    url = url.strip()
    if url.endswith(".") and not url.endswith(".."):
        url = url[:-1]
    return url

def is_usable_endpoint(url: str) -> bool:
    if not url:
        return False
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    return not any(pat in url for pat in LOCAL_PATTERNS)

def get_body_override(endpoint: str):
    for key, body in BODY_OVERRIDES.items():
        if key in endpoint:
            return body
    return False

def pick_best_plan(plan_pricing: list, already_bought: set):
    if not plan_pricing:
        return None
    for p in plan_pricing:
        if p["planDid"] in already_bought:
            return p
    def sort_key(p):
        return (0 if p.get("paymentType") == "fiat" else 1, p.get("pricePerRequest", 999))
    return sorted(plan_pricing, key=sort_key)[0]

def build_body(seller: dict):
    schema = seller.get("apiSchema", [])
    if isinstance(schema, list):
        for op in schema:
            if isinstance(op, dict):
                raw = op.get("requestBody", "")
                if raw and raw.strip():
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict) and parsed:
                            return parsed
                    except Exception:
                        pass
    if isinstance(schema, dict):
        b = schema.get("body")
        if isinstance(b, dict) and b:
            return b
    return {
        "query": "What can you do? Give me a brief demo.",
        "task": "What can you do? Give me a brief demo.",
        "prompt": "What can you do? Give me a brief demo.",
        "message": "What can you do? Give me a brief demo.",
        "format": "json",
    }

# ── Token cache ───────────────────────────────────────────────────────────────
token_cache: dict = {}  # planDid -> {token, payment_type, price_usd, currency, acquired_at}
_ordered_plans: set = set()  # planDids we've already ordered/subscribed to

def get_token(plan_did, payment_type, price_usd, card_pm, crypto_spent, plan_pricing, agent_id=None):
    if plan_did in token_cache:
        cached = token_cache[plan_did]
        print(f"  Token     : (cached from {cached['acquired_at'][:19]})")
        return cached["token"], crypto_spent, None, True

    if payment_type == "fiat":
        if not card_pm:
            crypto_plans = [p for p in plan_pricing if p.get("paymentType") == "crypto"]
            if not crypto_plans:
                return None, crypto_spent, "No card, no crypto fallback", False
            best = sorted(crypto_plans, key=lambda p: p.get("pricePerRequest", 999))[0]
            plan_did = best["planDid"]
            payment_type = "crypto"
            price_usd = best.get("pricePerRequest", 0)
            if plan_did in token_cache:
                cached = token_cache[plan_did]
                return cached["token"], crypto_spent, None, True
            token_options = X402TokenOptions(scheme="nvm:erc4337")
        else:
            token_options = X402TokenOptions(
                scheme="nvm:card-delegation",
                delegation_config=CardDelegationConfig(
                    provider_payment_method_id=card_pm.id,
                    spending_limit_cents=int(max(price_usd * 100 * 5, 200)),
                    duration_secs=3600,
                    currency="usd",
                ),
            )
    if payment_type == "crypto":
        if crypto_spent + price_usd > CRYPTO_BUDGET_USD:
            return None, crypto_spent, "Crypto budget exceeded", False
        token_options = X402TokenOptions(scheme="nvm:erc4337")

        # Order/subscribe to the crypto plan first (required before getting token)
        if plan_did not in _ordered_plans:
            try:
                order_result = payments.plans.order_plan(plan_id=plan_did)
                print(f"  Ordered   : plan subscribed (balance: {order_result.get('balance', {}).get('remaining', '?')})")
                _ordered_plans.add(plan_did)
            except Exception as e:
                err_str = str(e)
                # If already subscribed, that's fine — continue
                if "already" not in err_str.lower() and "subscriber" not in err_str.lower():
                    return None, crypto_spent, f"Order failed: {err_str}", False
                print(f"  Ordered   : (already subscribed)")
                _ordered_plans.add(plan_did)

    try:
        result = payments.x402.get_x402_access_token(plan_did, agent_id=agent_id, token_options=token_options)
        access_token = result["accessToken"]
        acquired_at = datetime.now(timezone.utc).isoformat()
        token_cache[plan_did] = {
            "token": access_token,
            "payment_type": payment_type,
            "price_usd": price_usd,
            "currency": "USD" if payment_type == "fiat" else "USDC",
            "acquired_at": acquired_at,
            "plan_did": plan_did,
        }
        print(f"  Token     : {access_token[:50]}...")
        if payment_type == "crypto":
            crypto_spent += price_usd
        return access_token, crypto_spent, None, False
    except Exception as e:
        return None, crypto_spent, str(e), False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("Discovery Buyer — TRACKED")
    print(f"  Mode : {'TEST' if TEST_MODE else 'FULL'} | Crypto cap: ${CRYPTO_BUDGET_USD}")
    print("=" * 64)

    with open(RAW_JSON_PATH) as f:
        data = json.load(f)

    all_entries = data.get("sellers", []) + data.get("buyers", [])
    seen, unique = set(), []
    for e in all_entries:
        ep = (e.get("endpointUrl") or "").strip()
        key = ep or str(id(e))
        if key not in seen:
            seen.add(key)
            unique.append(e)

    usable = [
        e for e in unique
        if e.get("teamId") != MY_TEAM_ID
        and is_usable_endpoint((e.get("endpointUrl") or "").strip())
        and e.get("planPricing")
    ]

    def sort_key(e):
        plans = e.get("planPricing", [])
        return (
            0 if any(p.get("paymentType") == "fiat" for p in plans) else 1,
            min((p.get("pricePerRequest", 999) for p in plans), default=999),
        )
    usable.sort(key=sort_key)

    card_pm = None
    try:
        methods = payments.delegation.list_payment_methods()
        if methods:
            card_pm = methods[0]
            print(f"Card: {card_pm.brand} *{card_pm.last4}")
    except Exception as e:
        print(f"Card lookup failed: {e}")

    # ── Output accumulators ───────────────────────────────────────────────────
    spend_log      = []   # every purchase with currency/amount/time
    successful     = []   # endpoints that returned 200 + what they need
    needs_info     = []   # endpoints that failed + why + what they need

    already_bought = set()
    crypto_spent   = 0.0

    print(f"\nBuy loop: {len(usable)} candidates\n{'='*64}")

    for i, seller in enumerate(usable, 1):
        name      = seller.get("name", f"#{i}")
        team      = seller.get("teamName", "")
        endpoint  = clean_endpoint_url(seller.get("endpointUrl", ""))
        plans     = seller.get("planPricing", [])
        agent_id  = seller.get("nvmAgentId")
        started  = datetime.now(timezone.utc).isoformat()

        print(f"\n[{i}/{len(usable)}] {name}  /  {team}")
        print(f"  Endpoint : {endpoint}")

        # Check skip
        override = get_body_override(endpoint)
        if override is None:
            note = "Skipped (checkout/non-API endpoint)"
            print(f"  ✗ {note}")
            needs_info.append({
                "name": name, "team": team, "endpoint": endpoint,
                "attempted_at": started,
                **classify_failure(note, endpoint),
            })
            continue

        best_plan = pick_best_plan(plans, already_bought)
        if not best_plan:
            note = "No planPricing"
            needs_info.append({
                "name": name, "team": team, "endpoint": endpoint,
                "attempted_at": started,
                **classify_failure(note, endpoint),
            })
            continue

        plan_did     = best_plan["planDid"]
        payment_type = best_plan.get("paymentType", "crypto")
        price_usd    = best_plan.get("pricePerRequest", 0)
        currency     = "USD" if payment_type == "fiat" else "USDC"
        formatted    = best_plan.get("pricePerRequestFormatted", "?")
        was_cached   = plan_did in token_cache

        print(f"  Plan     : ...{plan_did[-20:]}  [{payment_type}]  {formatted}")

        token, crypto_spent, token_err, from_cache = get_token(
            plan_did, payment_type, price_usd, card_pm, crypto_spent, plans, agent_id=agent_id
        )

        if token_err:
            note = f"Token error: {token_err}"
            print(f"  ✗ {note}")
            spend_log.append({
                "name": name, "team": team, "endpoint": endpoint,
                "attempted_at": started,
                "plan_did": plan_did,
                "payment_type": payment_type,
                "currency": currency,
                "amount": 0,
                "formatted": formatted,
                "status": "token_failed",
                "note": note,
                "token_cached": False,
            })
            needs_info.append({
                "name": name, "team": team, "endpoint": endpoint,
                "attempted_at": started,
                "plan_did": plan_did,
                "currency": currency,
                "amount": price_usd,
                **classify_failure(note, endpoint),
            })
            continue

        # Build and send request
        body = override if override is not False else build_body(seller)
        headers = {
            "Content-Type": "application/json",
            X402_HEADERS["PAYMENT_SIGNATURE"]: token,
        }

        print(f"  POST     : {endpoint}")
        print(f"  Body     : {json.dumps(body)[:120]}")

        resp_text   = ""
        http_status = None
        success     = False
        note        = ""

        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.post(endpoint, headers=headers, json=body)

                # Retry once on transient 502
                if resp.status_code == 502:
                    print(f"  HTTP     : 502 (retrying...)")
                    time.sleep(2)
                    resp = client.post(endpoint, headers=headers, json=body)

                # Fallback to GET on 405
                if resp.status_code == 405:
                    print(f"  HTTP     : 405 → trying GET")
                    resp = client.get(endpoint, headers=headers, params=body if isinstance(body, dict) else {})

                # Try /mcp suffix on 404 for MCP-style bodies
                if resp.status_code == 404 and isinstance(body, dict) and "jsonrpc" in body:
                    alt = endpoint.rstrip("/") + "/mcp"
                    print(f"  HTTP     : 404 → trying {alt}")
                    resp = client.post(alt, headers=headers, json=body)

            http_status = resp.status_code
            resp_text   = resp.text
            print(f"  HTTP     : {http_status}")

            if http_status == 200:
                try:
                    resp_json = resp.json()
                    preview   = json.dumps(resp_json)[:400]
                except Exception:
                    resp_json = None
                    preview   = resp_text[:400]
                print(f"  Response : {preview}")
                success = True
                note    = "OK"
                already_bought.add(plan_did)

                successful.append({
                    "name": name, "team": team, "endpoint": endpoint,
                    "called_at": started,
                    "plan_did": plan_did,
                    "payment_type": payment_type,
                    "currency": currency,
                    "amount_paid": 0 if from_cache else price_usd,
                    "formatted": formatted,
                    "request_body_sent": body,
                    "response_preview": preview,
                    "response_keys": list(resp_json.keys()) if isinstance(resp_json, dict) else None,
                    "token_was_cached": from_cache,
                    "note": "Semantic check — see response_preview for whether the 200 was meaningful",
                })
            else:
                note = f"HTTP {http_status}: {resp_text[:150]}"
        except Exception as e:
            note = f"Request error: {e}"

        print(f"  {'✓' if success else '✗'} {note}")

        # Always log spend attempt
        spend_log.append({
            "name": name, "team": team, "endpoint": endpoint,
            "attempted_at": started,
            "plan_did": plan_did,
            "payment_type": payment_type,
            "currency": currency,
            "amount": price_usd if (not from_cache and success) else 0,
            "formatted": formatted,
            "token_cached": from_cache,
            "http_status": http_status,
            "status": "success" if success else "failed",
            "note": note,
        })

        if not success:
            needs_info.append({
                "name": name, "team": team, "endpoint": endpoint,
                "attempted_at": started,
                "plan_did": plan_did,
                "currency": currency,
                "amount_would_cost": price_usd,
                "request_body_sent": body,
                **classify_failure(note, endpoint, resp_text),
            })

        if TEST_MODE and success:
            print(f"\n✅ TEST done.")
            break

        time.sleep(0.4)

    # ── Write outputs ─────────────────────────────────────────────────────────

    # Spend summary
    total_fiat   = sum(r["amount"] for r in spend_log if r["currency"] == "USD")
    total_crypto = sum(r["amount"] for r in spend_log if r["currency"] == "USDC")
    spend_summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_usd_spent": round(total_fiat, 6),
        "total_usdc_spent": round(total_crypto, 6),
        "total_attempts": len(spend_log),
        "total_successes": len(successful),
        "entries": spend_log,
    }

    with open("spend_log.json", "w") as f:
        json.dump(spend_summary, f, indent=2)

    # Successful calls
    successful_summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(successful),
        "note": "These endpoints returned HTTP 200. Check response_preview for semantic success.",
        "endpoints": successful,
    }
    with open("successful_calls.json", "w") as f:
        json.dump(successful_summary, f, indent=2)

    # Needs info
    needs_summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(needs_info),
        "by_category": {},
        "endpoints": needs_info,
    }
    for entry in needs_info:
        cat = entry.get("category", "UNKNOWN")
        needs_summary["by_category"].setdefault(cat, []).append(entry["name"])

    with open("needs_info.json", "w") as f:
        json.dump(needs_summary, f, indent=2)

    # ── Print summary ─────────────────────────────────────────────────────────
    ok   = [r for r in spend_log if r["status"] == "success"]
    fail = [r for r in spend_log if r["status"] != "success"]

    print(f"\n{'='*64}")
    print(f"FINAL SUMMARY")
    print(f"  Succeeded       : {len(ok)} / {len(spend_log)}")
    print(f"  Failed          : {len(fail)}")
    print(f"  USD  spent      : ${total_fiat:.6f}")
    print(f"  USDC spent      : ${total_crypto:.6f}")

    if ok:
        print("\n  Successes:")
        for r in ok:
            print(f"    ✓ {r['name']:40s} {r['currency']:5s} {r['formatted']}")

    print("\n  Failure breakdown:")
    for cat, names in needs_summary["by_category"].items():
        print(f"    {cat:30s} x{len(names):2d}: {', '.join(names[:3])}{'...' if len(names)>3 else ''}")

    print("\nOutputs:")
    print("  spend_log.json       — every attempt with currency/amount/datetime")
    print("  successful_calls.json — what worked + what body was sent")
    print("  needs_info.json      — what failed + why + how to fix")


if __name__ == "__main__":
    main()