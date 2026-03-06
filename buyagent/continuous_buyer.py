"""
continuous_buyer.py — Continuously buy from proven fiat (USD) agents.

Designed to run as a GitHub Actions workflow or locally.

Env vars:
  NVM_API_KEY       — Nevermined API key (required)
  NVM_ENVIRONMENT   — "sandbox" or "live" (default: sandbox)
  MAX_ROUNDS        — Number of rounds to run (default: 0 = unlimited)
  LOOP_DELAY        — Seconds between rounds (default: 5)
  CALL_DELAY        — Seconds between calls (default: 0.5)

Run locally:  python continuous_buyer.py
Stop locally: Ctrl+C (prints summary on exit)
"""

import json
import os
import random
import signal
import sys
import time
import warnings
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()                                      # buyagent/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))  # root .env

warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")
warnings.filterwarnings("ignore", message=".*shadows an attribute.*")

import httpx
from payments_py import Payments, PaymentOptions
from payments_py.x402.fastapi import X402_HEADERS
from payments_py.x402.types import CardDelegationConfig, X402TokenOptions

# ── Config from env ──────────────────────────────────────────────────────────
NVM_API_KEY     = os.getenv("NVM_API_KEY", "")
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
MAX_ROUNDS      = int(os.getenv("MAX_ROUNDS", "0"))       # 0 = unlimited
LOOP_DELAY      = float(os.getenv("LOOP_DELAY", "5"))
CALL_DELAY      = float(os.getenv("CALL_DELAY", "0.5"))
REQUEST_TIMEOUT = 45.0
DISCOVERY_URL   = "https://nevermined.ai/hackathon/register/api/discover"
MY_TEAM_ID      = "0x659c87f82dd0e194ef17067398ebdb6ee1e13524"
AGENT_FILTER    = os.getenv("AGENT_FILTER", "")  # empty = all, or a label like "Celebrity Economy"

if not NVM_API_KEY:
    print("ERROR: NVM_API_KEY not set")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)

# ── Request body variety ─────────────────────────────────────────────────────
TOPICS = [
    "AI agent orchestration", "decentralized finance", "machine learning ops",
    "autonomous vehicles", "quantum computing startups", "robotics automation",
    "generative AI art", "AI drug discovery", "smart contract auditing",
    "edge computing", "federated learning", "synthetic data generation",
    "AI cybersecurity", "natural language processing", "computer vision APIs",
    "AI-powered customer support", "predictive maintenance", "digital twins",
    "AI governance frameworks", "neuromorphic computing", "AI chip design",
    "multimodal AI models", "retrieval augmented generation", "AI agent payments",
    "supply chain optimization with AI", "AI in healthcare diagnostics",
]
BRANDS = [
    "Orchestro", "NevermindAI", "AgentHub", "SmartFlow", "DataPulse",
    "CortexLabs", "SynapseAI", "AgentForge", "NeuralPay", "ChainMind",
]
AUDIENCES = [
    "AI developers", "startup founders", "enterprise CTOs", "data scientists",
    "blockchain developers", "product managers", "ML engineers", "DevOps teams",
]
COMPANIES = [
    "Salesforce", "Google", "Microsoft", "Apple", "Amazon", "Meta", "Tesla",
    "Nvidia", "OpenAI", "Anthropic", "Stripe", "Shopify", "Netflix", "Uber",
    "Snowflake", "Databricks", "Palantir", "CrowdStrike", "Cloudflare",
]
QUERIES = [
    "What are the top AI agent frameworks in 2026?",
    "How does x402 protocol compare to traditional payment APIs?",
    "What is the state of autonomous AI agent marketplaces?",
    "Explain the benefits of agent-to-agent commerce.",
    "What are the risks of AI agent autonomy in financial transactions?",
    "How do decentralized AI marketplaces ensure trust?",
    "Compare the top 5 AI orchestration platforms.",
    "What regulations apply to autonomous AI agents in the EU?",
    "How can AI agents negotiate prices with each other?",
    "What is the future of programmable payments for AI?",
    "Summarize recent breakthroughs in multi-agent systems.",
    "How do AI agents handle payment disputes?",
    "What are the key metrics for evaluating AI agent performance?",
    "Explain the role of session keys in x402 payments.",
    "What security considerations apply to AI agent wallets?",
]
EMAIL_SUBJECTS = [
    "Orchestro Agent Update — Round {n}",
    "Automated Purchase Confirmation #{n}",
    "AI Agent Marketplace Report — Iteration {n}",
    "x402 Transaction Log — Cycle {n}",
    "Hackathon Progress Update #{n}",
]


# ── Body generators per agent ────────────────────────────────────────────────

def make_celebrity_body(n):
    return {
        "topic": random.choice(TOPICS),
        "brand": random.choice(BRANDS),
        "product_url": "https://orchestro.vercel.app",
        "audience": random.choice(AUDIENCES),
        "tone": random.choice(["helpful", "professional", "casual", "authoritative"]),
    }

def make_market_buyer_body(n):
    return {"query": random.choice(QUERIES), "task": random.choice(QUERIES)}

def make_nevermailed_body(n):
    return {
        "from": "Orchestro Agent <agent@nevermailed.com>",
        "to": "test@nevermailed.com",
        "subject": random.choice(EMAIL_SUBJECTS).format(n=n),
        "text": f"Automated round {n} by Orchestro agent via x402. Topic: {random.choice(TOPICS)}.",
        "html": f"<p>Round <strong>{n}</strong> - {random.choice(TOPICS)}</p>",
    }

def make_agenticard_body(n):
    return {"cardId": (n % 20) + 1, "agentId": str((n % 5) + 1)}

def make_baselayer_body(n):
    return {"jsonrpc": "2.0", "id": n, "method": "tools/list", "params": {}}

def make_predictive_body(n):
    assets = ["AI_AGENTS", "BTC", "ETH", "SOL", "MATIC", "LINK"]
    return {
        "query": f"Predict {random.choice(TOPICS)} market trend Q2 2026",
        "asset": random.choice(assets),
    }

def make_airi_body(n):
    return {"company": random.choice(COMPANIES)}

def make_cloudagi_search_body(n):
    return {"query": random.choice(QUERIES), "sources": ["exa"], "numResults": 3}


# ── Agent registry: endpoint substring -> config ─────────────────────────────
AGENT_CONFIGS = {
    "ai-celebrity-economy.vercel.app": {
        "body_fn": make_celebrity_body, "label": "Celebrity Economy",
    },
    "nevermined-autonomous-business-hack.vercel.app/api/agent/research": {
        "body_fn": make_market_buyer_body, "label": "Market Buyer",
    },
    "nevermailed.com/api/send": {
        "body_fn": make_nevermailed_body, "label": "Nevermailed",
    },
    "agenticard-ai.manus.space/api/v1/enhance": {
        "body_fn": make_agenticard_body, "label": "AgentCard",
    },
    "54.183.4.35:9010": {
        "body_fn": make_baselayer_body, "label": "BaseLayer Crypto Intel",
    },
    "54.183.4.35:9020": {
        "body_fn": make_baselayer_body, "label": "BaseLayer Web Scraper",
    },
    "54.183.4.35:9030": {
        "body_fn": make_baselayer_body, "label": "BaseLayer Agent Eval",
    },
    "supabase.co/functions/v1/agent-predict": {
        "body_fn": make_predictive_body, "label": "PredictiveEdge",
        "force_crypto": True,
    },
    "airi-demo.replit.app/resilience-score": {
        "body_fn": make_airi_body, "label": "AiRI",
        "force_crypto": True,
    },
    "api.cloudagi.org/v1/services/smart-search/execute": {
        "body_fn": make_cloudagi_search_body, "label": "CloudAGI Search",
        "force_crypto": True,
    },
}


# ── Discovery ────────────────────────────────────────────────────────────────

def fetch_discovery():
    """Fetch live agent list from Nevermined discovery API."""
    print("Fetching discovery data...")
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(DISCOVERY_URL, headers={"x-nvm-api-key": NVM_API_KEY})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"  Discovery fetch failed: {e}")
        # Fall back to local file
        try:
            with open("discovery_raw.json") as f:
                return json.load(f)
        except FileNotFoundError:
            print("  No local discovery_raw.json either. Exiting.")
            sys.exit(1)


def load_agents(data):
    """Filter discovery data to only agents we know how to call."""
    all_entries = data.get("sellers", []) + data.get("buyers", [])
    agents = []
    seen = set()

    for entry in all_entries:
        if entry.get("teamId") == MY_TEAM_ID:
            continue

        endpoint = (entry.get("endpointUrl") or "").strip()
        if endpoint.endswith(".") and not endpoint.endswith(".."):
            endpoint = endpoint[:-1]
        if not endpoint or endpoint in seen:
            continue
        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            continue

        config = None
        for key, cfg in AGENT_CONFIGS.items():
            if key in endpoint:
                config = cfg
                break
        if not config:
            continue

        plans = entry.get("planPricing", [])
        if not plans:
            continue

        force_crypto = config.get("force_crypto", False)
        target_type = "crypto" if force_crypto else "fiat"
        matching = [p for p in plans if p.get("paymentType") == target_type]
        if not matching:
            continue
        best = sorted(matching, key=lambda p: p.get("pricePerRequest", 999))[0]

        seen.add(endpoint)
        agents.append({
            "endpoint": endpoint,
            "name": entry.get("name", "?"),
            "agent_id": entry.get("nvmAgentId"),
            "plan_did": best["planDid"],
            "payment_type": best.get("paymentType", "fiat"),
            "price": best.get("pricePerRequest", 0),
            "formatted": best.get("pricePerRequestFormatted", "?"),
            "body_fn": config["body_fn"],
            "label": config["label"],
        })

    return agents


# ── Token management ─────────────────────────────────────────────────────────
token_cache = {}
_ordered_plans = set()


def get_or_create_token(agent, card_pm):
    plan_did = agent["plan_did"]
    payment_type = agent["payment_type"]

    if plan_did in token_cache:
        return token_cache[plan_did], True

    if payment_type == "fiat":
        if not card_pm:
            return None, False
        token_options = X402TokenOptions(
            scheme="nvm:card-delegation",
            delegation_config=CardDelegationConfig(
                provider_payment_method_id=card_pm.id,
                spending_limit_cents=int(max(agent["price"] * 100 * 50, 2000)),
                duration_secs=7200,
                currency="usd",
            ),
        )
    else:
        token_options = X402TokenOptions(scheme="nvm:erc4337")
        if plan_did not in _ordered_plans:
            try:
                payments.plans.order_plan(plan_id=plan_did)
                _ordered_plans.add(plan_did)
            except Exception as e:
                if "already" not in str(e).lower() and "subscriber" not in str(e).lower():
                    return None, False
                _ordered_plans.add(plan_did)

    try:
        result = payments.x402.get_x402_access_token(
            plan_did, agent_id=agent.get("agent_id"), token_options=token_options
        )
        token_cache[plan_did] = result["accessToken"]
        return token_cache[plan_did], False
    except Exception as e:
        print(f"    Token error [{agent['label']}]: {e}")
        return None, False


# ── Main ─────────────────────────────────────────────────────────────────────
transaction_log = []
running = True


def handle_sigint(sig, frame):
    global running
    running = False


signal.signal(signal.SIGINT, handle_sigint)


def print_summary():
    ok = [t for t in transaction_log if t["success"]]
    fail = [t for t in transaction_log if not t["success"]]
    total_usd = sum(t["price"] for t in ok if t["currency"] == "USD")
    total_usdc = sum(t["price"] for t in ok if t["currency"] == "USDC")

    print(f"\n{'='*64}")
    print("CONTINUOUS BUYER SUMMARY")
    print(f"  Total transactions : {len(transaction_log)}")
    print(f"  Successful         : {len(ok)}")
    print(f"  Failed             : {len(fail)}")
    print(f"  USD spent          : ${total_usd:.4f}")
    print(f"  USDC spent         : {total_usdc:.4f} USDC")

    if ok:
        by_agent = {}
        for t in ok:
            by_agent.setdefault(t["label"], []).append(t)
        print("\n  By agent:")
        for label, txns in sorted(by_agent.items(), key=lambda x: -len(x[1])):
            print(f"    {label:30s} x{len(txns):3d}")

    suffix = f"-{AGENT_FILTER.replace(' ', '_')}" if AGENT_FILTER else ""
    log_path = f"continuous_buyer_log{suffix}.json"
    with open(log_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(transaction_log),
            "successes": len(ok),
            "failures": len(fail),
            "total_usd": round(total_usd, 6),
            "total_usdc": round(total_usdc, 6),
            "transactions": transaction_log,
        }, f, indent=2)
    print(f"\n  Log: {log_path}")
    print(f"{'='*64}")


def call_agent(agent, token, body, round_num):
    """POST to an agent, return (success, note)."""
    headers = {
        "Content-Type": "application/json",
        X402_HEADERS["PAYMENT_SIGNATURE"]: token,
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        resp = client.post(agent["endpoint"], headers=headers, json=body)

        if resp.status_code == 405:
            resp = client.get(
                agent["endpoint"], headers=headers,
                params=body if isinstance(body, dict) else {},
            )

    if resp.status_code == 200:
        try:
            preview = json.dumps(resp.json())[:100]
        except Exception:
            preview = resp.text[:100]
        return True, f"OK: {preview}"

    # Clear cached token on auth errors so next round retries
    if resp.status_code in (401, 402, 403):
        token_cache.pop(agent["plan_did"], None)

    return False, f"HTTP {resp.status_code}: {resp.text[:80]}"


def main():
    global running

    print("=" * 64)
    print("CONTINUOUS BUYER")
    print(f"  Env: {NVM_ENVIRONMENT} | Max rounds: {MAX_ROUNDS or 'unlimited'}")
    print(f"  Loop delay: {LOOP_DELAY}s | Call delay: {CALL_DELAY}s")
    print("=" * 64)

    # Card setup
    card_pm = None
    try:
        methods = payments.delegation.list_payment_methods()
        if methods:
            card_pm = methods[0]
            print(f"  Card: {card_pm.brand} *{card_pm.last4}")
    except Exception as e:
        print(f"  Card lookup failed: {e}")

    if not card_pm:
        print("  WARNING: No card — fiat agents will be skipped")

    # Load agents from live discovery, apply filter if set
    data = fetch_discovery()
    agents = load_agents(data)
    if AGENT_FILTER:
        agents = [a for a in agents if a["label"] == AGENT_FILTER]
        print(f"  Filter: {AGENT_FILTER}")
    fiat_count = sum(1 for a in agents if a["payment_type"] == "fiat")
    crypto_count = sum(1 for a in agents if a["payment_type"] == "crypto")
    print(f"  Agents: {fiat_count} fiat + {crypto_count} crypto = {len(agents)} total")
    for a in agents:
        print(f"    {a['label']:30s} [{a['payment_type']:6s}] {a['formatted']}")

    if not agents:
        print("  No agents found. Exiting.")
        sys.exit(1)

    round_num = 0
    while running:
        round_num += 1
        if MAX_ROUNDS and round_num > MAX_ROUNDS:
            break

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"\n--- Round {round_num} [{ts}] ---")

        round_ok = 0
        for agent in agents:
            if not running:
                break
            if agent["payment_type"] == "fiat" and not card_pm:
                continue

            body = agent["body_fn"](round_num)
            token, was_cached = get_or_create_token(agent, card_pm)
            if not token:
                transaction_log.append({
                    "round": round_num, "label": agent["label"],
                    "endpoint": agent["endpoint"], "success": False,
                    "note": "token_failed", "price": 0,
                    "currency": "USD" if agent["payment_type"] == "fiat" else "USDC",
                    "at": datetime.now(timezone.utc).isoformat(),
                })
                print(f"  [x] {agent['label']:25s} token_failed")
                continue

            try:
                success, note = call_agent(agent, token, body, round_num)
            except Exception as e:
                success, note = False, f"Error: {e}"

            currency = "USD" if agent["payment_type"] == "fiat" else "USDC"
            price = agent["price"] if success and not was_cached else 0
            icon = "+" if success else "x"
            print(f"  [{icon}] {agent['label']:25s} {note[:70]}")

            if success:
                round_ok += 1

            transaction_log.append({
                "round": round_num, "label": agent["label"],
                "endpoint": agent["endpoint"], "body": body,
                "success": success, "note": note,
                "price": price, "currency": currency,
                "was_cached": was_cached,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            time.sleep(CALL_DELAY)

        total_ok = sum(1 for t in transaction_log if t["success"])
        print(f"  Round {round_num}: {round_ok} ok | Total: {total_ok}/{len(transaction_log)}")

        if running and (not MAX_ROUNDS or round_num < MAX_ROUNDS):
            time.sleep(LOOP_DELAY)

    print_summary()


if __name__ == "__main__":
    main()
