"""
continuous_buyer.py — Continuously buy from marketplace agents.

Discovers agents dynamically from the Nevermined discovery API and
builds request bodies from their apiSchema. Falls back to curated
overrides for agents where we know a better payload.

Designed to run as a GitHub Actions workflow or locally.

Env vars:
  NVM_API_KEY       — Nevermined API key (required)
  NVM_ENVIRONMENT   — "sandbox" or "live" (default: sandbox)
  MAX_ROUNDS        — Number of rounds to run (default: 0 = unlimited)
  LOOP_DELAY        — Seconds between rounds (default: 5)
  CALL_DELAY        — Seconds between calls (default: 0.5)
  AGENT_FILTER      — Only buy from this agent label (default: all)
  CONSEC_FAIL_SKIP  — Skip agent after N consecutive failures (default: 10)
  ALL_FAIL_EXIT     — Exit if all agents fail N rounds in a row (default: 20)

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
AGENT_FILTER    = os.getenv("AGENT_FILTER", "")

# Failure handling
CONSEC_FAIL_SKIP    = int(os.getenv("CONSEC_FAIL_SKIP", "10"))
CONSEC_FAIL_BACKOFF = int(os.getenv("CONSEC_FAIL_BACKOFF", "5"))
MAX_BACKOFF         = float(os.getenv("MAX_BACKOFF", "60"))
ALL_FAIL_EXIT       = int(os.getenv("ALL_FAIL_EXIT", "20"))
REDISCOVERY_INTERVAL = int(os.getenv("REDISCOVERY_INTERVAL", "100"))

payments = None

def _init_payments():
    global payments
    if payments is not None:
        return payments
    if not NVM_API_KEY:
        print("ERROR: NVM_API_KEY not set")
        sys.exit(1)
    payments = Payments.get_instance(
        PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
    )
    return payments

# ── Variety pools for randomizing request bodies ─────────────────────────────
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
TOPICS = [
    "AI agent orchestration", "decentralized finance", "machine learning ops",
    "autonomous vehicles", "quantum computing startups", "robotics automation",
    "generative AI art", "AI drug discovery", "smart contract auditing",
    "edge computing", "federated learning", "synthetic data generation",
    "AI cybersecurity", "natural language processing", "computer vision APIs",
]
COMPANIES = [
    "Salesforce", "Google", "Microsoft", "Apple", "Amazon", "Meta", "Tesla",
    "Nvidia", "OpenAI", "Anthropic", "Stripe", "Shopify", "Netflix", "Uber",
    "Snowflake", "Databricks", "Palantir", "CrowdStrike", "Cloudflare",
]

# ── Endpoint patterns to always skip ────────────────────────────────────────
SKIP_PATTERNS = [
    "localhost", "127.0.0.1", "0.0.0.0", "http://seller:",
    "nevermined.app/checkout",
    "ngrok-free.dev", "ngrok-free.app",  # tunnels expire, always fail in CI
    "trycloudflare.com",                 # same — ephemeral tunnels
]


# ── Curated body overrides: endpoint substring -> body or body_fn ────────────
# These override the apiSchema when we know what actually works.
# Use a callable (takes round_num) for variety, or a dict for static body.

def _celebrity_body(n):
    return {
        "topic": random.choice(TOPICS),
        "brand": random.choice(["Orchestro", "NevermindAI", "AgentHub", "SmartFlow", "DataPulse"]),
        "product_url": "https://orchestro.vercel.app",
        "audience": random.choice(["AI developers", "startup founders", "enterprise CTOs", "data scientists"]),
        "tone": random.choice(["helpful", "professional", "casual", "authoritative"]),
    }

def _nevermailed_body(n):
    subjects = [
        f"Orchestro Agent Update — Round {n}",
        f"Automated Purchase Confirmation #{n}",
        f"AI Agent Marketplace Report — Iteration {n}",
    ]
    return {
        "from": "Orchestro Agent <agent@nevermailed.com>",
        "to": "test@nevermailed.com",
        "subject": random.choice(subjects),
        "text": f"Automated round {n} by Orchestro agent via x402. Topic: {random.choice(TOPICS)}.",
        "html": f"<p>Round <strong>{n}</strong> - {random.choice(TOPICS)}</p>",
    }

def _market_buyer_body(n):
    return {"query": random.choice(QUERIES), "task": random.choice(QUERIES)}

def _agenticard_body(n):
    # Only use card IDs 1-5 which are known to exist
    return {"cardId": (n % 5) + 1, "agentId": str((n % 5) + 1)}

def _predictive_body(n):
    return {
        "query": f"Predict {random.choice(TOPICS)} market trend Q2 2026",
        "asset": random.choice(["AI_AGENTS", "BTC", "ETH", "SOL", "MATIC", "LINK"]),
    }

def _airi_body(n):
    return {"company": random.choice(COMPANIES)}

def _cloudagi_search_body(n):
    return {"query": random.choice(QUERIES), "sources": ["exa"], "numResults": 3}

def _cloudagi_research_body(n):
    return {"query": random.choice(QUERIES), "type": "auto", "numResults": 3}

def _cloudagi_code_review_body(n):
    snippets = [
        ("def add(a, b):\n    return a + b", "python"),
        ("const fetch = async (url) => {\n  const r = await fetch(url);\n  return r.json();\n}", "javascript"),
        ("fn main() {\n    println!(\"hello\");\n}", "rust"),
    ]
    code, lang = random.choice(snippets)
    return {"code": code, "language": lang, "focus": ["bugs", "security"]}

def _cloudagi_scraper_body(n):
    urls = ["https://nevermined.io", "https://example.com", "https://httpbin.org"]
    return {"url": random.choice(urls), "maxPages": 1}

def _cloudagi_gpu_body(n):
    return {
        "gpu": "T4", "image": "python:3.13",
        "command": ["python3", "-c", f"print('Hello from Orchestro round {n}!')"],
        "timeoutSecs": 60,
    }

def _generic_query_body(n):
    return {"query": random.choice(QUERIES)}

def _generic_message_body(n):
    return {"message": random.choice(QUERIES)}


BODY_OVERRIDES = {
    # Proven working payloads from successful_calls.json
    "ai-celebrity-economy.vercel.app": {"body_fn": _celebrity_body, "force_crypto": False},
    "nevermailed.com/api/send": {"body_fn": _nevermailed_body, "force_crypto": False},
    "nevermined-autonomous-business-hack.vercel.app/api/agent/research": {"body_fn": _market_buyer_body, "force_crypto": False},
    "agenticard-ai.manus.space/api/v1/enhance": {"body_fn": _agenticard_body, "force_crypto": False},
    "supabase.co/functions/v1/agent-predict": {"body_fn": _predictive_body, "force_crypto": True},
    "airi-demo.replit.app/resilience-score": {"body_fn": _airi_body, "force_crypto": True},
    "api.cloudagi.org/v1/services/smart-search/execute": {"body_fn": _cloudagi_search_body, "force_crypto": True},
    "api.cloudagi.org/v1/services/ai-research/execute": {"body_fn": _cloudagi_research_body, "force_crypto": True},
    "api.cloudagi.org/v1/services/code-review/execute": {"body_fn": _cloudagi_code_review_body, "force_crypto": True},
    "api.cloudagi.org/v1/services/web-scraper/execute": {"body_fn": _cloudagi_scraper_body, "force_crypto": True},
    "api.cloudagi.org/v1/services/gpu-compute/execute": {"body_fn": _cloudagi_gpu_body, "force_crypto": True},

    # Agents that need specific field names (from needs_info.json)
    "us14.abilityai.dev/ask": {"body_fn": _generic_query_body, "force_crypto": False},
    "us14.abilityai.dev/api/paid/qa-checker": {"body_fn": _generic_message_body, "force_crypto": False},
    "us14.abilityai.dev/api/paid/nexus": {"body_fn": _generic_message_body, "force_crypto": False},
    "us14.abilityai.dev/api/paid/market-intel": {"body_fn": _generic_message_body, "force_crypto": False},
    "hack-mined-production.up.railway.app/research": {"body_fn": _generic_query_body, "force_crypto": False},

    # Skip: these require their own auth or are known broken
    "sabi-backend": {"skip": True, "reason": "Needs separate API key"},
    "api.mindra.co": {"skip": True, "reason": "Needs separate auth"},
}


# ── Parse apiSchema to extract a usable request body ─────────────────────────

def parse_api_schema(schema):
    """Extract a request body dict from apiSchema (list or dict format)."""
    if isinstance(schema, list):
        for op in schema:
            if not isinstance(op, dict):
                continue
            raw = op.get("requestBody", "")
            if raw and isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and parsed:
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
    elif isinstance(schema, dict):
        body = schema.get("body")
        if isinstance(body, dict) and body:
            return body
    return None


def randomize_schema_body(body_template, round_num):
    """Take a schema example body and inject variety into string fields."""
    body = dict(body_template)
    for key in body:
        if key == "query" and isinstance(body[key], str):
            body[key] = random.choice(QUERIES)
        elif key == "task" and isinstance(body[key], str):
            body[key] = random.choice(QUERIES)
        elif key == "message" and isinstance(body[key], str):
            body[key] = random.choice(QUERIES)
        elif key == "prompt" and isinstance(body[key], str):
            body[key] = random.choice(QUERIES)
        elif key == "company" and isinstance(body[key], str):
            body[key] = random.choice(COMPANIES)
        elif key == "topic" and isinstance(body[key], str):
            body[key] = random.choice(TOPICS)
    return body


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
        try:
            with open("discovery_raw.json") as f:
                return json.load(f)
        except FileNotFoundError:
            print("  No local discovery_raw.json either. Exiting.")
            sys.exit(1)


def _should_skip_endpoint(endpoint):
    """Return a reason string if the endpoint should be skipped, else None."""
    if not endpoint:
        return "empty"
    if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
        return "not http"
    for pat in SKIP_PATTERNS:
        if pat in endpoint:
            return f"matches skip pattern: {pat}"
    # Check curated skips
    for key, cfg in BODY_OVERRIDES.items():
        if key in endpoint and cfg.get("skip"):
            return cfg.get("reason", "curated skip")
    return None


def load_agents(data):
    """Build agent list from discovery data.

    For each agent:
    1. If we have a curated override in BODY_OVERRIDES, use that body_fn
    2. Else parse apiSchema for an example request body
    3. Skip agents where we can't build any body
    """
    all_entries = data.get("sellers", []) + data.get("buyers", [])
    agents = []
    seen = set()

    for entry in all_entries:
        if entry.get("teamId") == MY_TEAM_ID:
            continue

        endpoint = (entry.get("endpointUrl") or "").strip()
        if endpoint.endswith(".") and not endpoint.endswith(".."):
            endpoint = endpoint[:-1]

        skip_reason = _should_skip_endpoint(endpoint)
        if skip_reason:
            continue
        if endpoint in seen:
            continue

        plans = entry.get("planPricing", [])
        if not plans:
            continue

        name = entry.get("name", "?")

        # Check for curated override
        override = None
        for key, cfg in BODY_OVERRIDES.items():
            if key in endpoint:
                override = cfg
                break

        if override and override.get("skip"):
            continue

        # Determine body function
        if override and "body_fn" in override:
            body_fn = override["body_fn"]
        else:
            # Try to parse apiSchema
            schema_body = parse_api_schema(entry.get("apiSchema"))
            if schema_body:
                # Capture template in closure
                template = dict(schema_body)
                body_fn = lambda n, t=template: randomize_schema_body(t, n)
            else:
                # Last resort: generic multi-field body that covers common field names
                body_fn = lambda n: {
                    "query": random.choice(QUERIES),
                    "task": random.choice(QUERIES),
                    "message": random.choice(QUERIES),
                }

        # Pick best plan: prefer fiat unless override says force_crypto
        force_crypto = override.get("force_crypto", False) if override else False
        if force_crypto:
            matching = [p for p in plans if p.get("paymentType") == "crypto"]
        else:
            # Prefer fiat, fall back to crypto
            matching = [p for p in plans if p.get("paymentType") == "fiat"]
            if not matching:
                matching = [p for p in plans if p.get("paymentType") == "crypto"]
        if not matching:
            continue
        best = sorted(matching, key=lambda p: p.get("pricePerRequest", 999))[0]

        seen.add(endpoint)
        agents.append({
            "endpoint": endpoint,
            "name": name,
            "agent_id": entry.get("nvmAgentId"),
            "plan_did": best["planDid"],
            "payment_type": best.get("paymentType", "fiat"),
            "price": best.get("pricePerRequest", 0),
            "formatted": best.get("pricePerRequestFormatted", "?"),
            "body_fn": body_fn,
            "label": name,
        })

    return agents


# ── Token management ─────────────────────────────────────────────────────────
token_cache = {}
_ordered_plans = set()


def get_or_create_token(agent, card_pm):
    p = _init_payments()
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
                p.plans.order_plan(plan_id=plan_did)
                _ordered_plans.add(plan_did)
            except Exception as e:
                if "already" not in str(e).lower() and "subscriber" not in str(e).lower():
                    return None, False
                _ordered_plans.add(plan_did)

    try:
        result = p.x402.get_x402_access_token(
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

# Per-agent consecutive failure counter
consec_failures = {}   # label -> count
skipped_agents = set() # labels that hit CONSEC_FAIL_SKIP
all_fail_streak = 0    # rounds where every agent failed


def backoff_delay(fails):
    """Exponential backoff: 2^(fails - CONSEC_FAIL_BACKOFF) seconds, capped."""
    if fails < CONSEC_FAIL_BACKOFF:
        return 0
    return min(2 ** (fails - CONSEC_FAIL_BACKOFF), MAX_BACKOFF)


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
            data = resp.json()
            # Check for semantic errors (HTTP 200 but error in body)
            if isinstance(data, dict):
                if data.get("error") and not data.get("success"):
                    return False, f"Semantic error: {json.dumps(data)[:80]}"
            preview = json.dumps(data)[:100]
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
    p = _init_payments()
    card_pm = None
    try:
        methods = p.delegation.list_payment_methods()
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
    global all_fail_streak
    while running:
        round_num += 1
        if MAX_ROUNDS and round_num > MAX_ROUNDS:
            break

        # Periodic rediscovery to pick up new agents / drop dead ones
        if round_num > 1 and REDISCOVERY_INTERVAL and round_num % REDISCOVERY_INTERVAL == 0:
            print("\n  [*] Re-running discovery...")
            data = fetch_discovery()
            new_agents = load_agents(data)
            if AGENT_FILTER:
                new_agents = [a for a in new_agents if a["label"] == AGENT_FILTER]
            if new_agents:
                agents = new_agents
                # Reset failure counters for agents that reappeared
                active_labels = {a["label"] for a in agents}
                for label in list(skipped_agents):
                    if label in active_labels:
                        skipped_agents.discard(label)
                        consec_failures.pop(label, None)
                        print(f"    Re-enabled: {label}")
                print(f"    Agents after rediscovery: {len(agents)}")

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"\n--- Round {round_num} [{ts}] ---")

        round_ok = 0
        round_attempted = 0
        for agent in agents:
            if not running:
                break
            label = agent["label"]

            # Skip agents that have been failing too much
            if label in skipped_agents:
                continue

            if agent["payment_type"] == "fiat" and not card_pm:
                continue

            # Apply backoff delay for agents with consecutive failures
            fails = consec_failures.get(label, 0)
            extra_delay = backoff_delay(fails)
            if extra_delay > 0:
                print(f"  [~] {label:25s} backoff {extra_delay:.0f}s (fails: {fails})")
                time.sleep(extra_delay)

            round_attempted += 1
            body = agent["body_fn"](round_num)
            token, was_cached = get_or_create_token(agent, card_pm)
            if not token:
                consec_failures[label] = consec_failures.get(label, 0) + 1
                if consec_failures[label] >= CONSEC_FAIL_SKIP:
                    skipped_agents.add(label)
                    print(f"  [x] {label:25s} token_failed — SKIPPING ({consec_failures[label]} consecutive failures)")
                else:
                    print(f"  [x] {label:25s} token_failed ({consec_failures[label]}/{CONSEC_FAIL_SKIP})")
                transaction_log.append({
                    "round": round_num, "label": label,
                    "endpoint": agent["endpoint"], "success": False,
                    "note": "token_failed", "price": 0,
                    "currency": "USD" if agent["payment_type"] == "fiat" else "USDC",
                    "at": datetime.now(timezone.utc).isoformat(),
                })
                continue

            try:
                success, note = call_agent(agent, token, body, round_num)
            except Exception as e:
                success, note = False, f"Error: {e}"

            currency = "USD" if agent["payment_type"] == "fiat" else "USDC"
            price = agent["price"] if success and not was_cached else 0
            icon = "+" if success else "x"

            if success:
                round_ok += 1
                consec_failures[label] = 0
                print(f"  [{icon}] {label:25s} {note[:70]}")
            else:
                consec_failures[label] = consec_failures.get(label, 0) + 1
                if consec_failures[label] >= CONSEC_FAIL_SKIP:
                    skipped_agents.add(label)
                    print(f"  [{icon}] {label:25s} {note[:50]} — SKIPPING ({consec_failures[label]} consecutive failures)")
                else:
                    print(f"  [{icon}] {label:25s} {note[:50]} ({consec_failures[label]}/{CONSEC_FAIL_SKIP})")

            transaction_log.append({
                "round": round_num, "label": label,
                "endpoint": agent["endpoint"], "body": body,
                "success": success, "note": note,
                "price": price, "currency": currency,
                "was_cached": was_cached,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            time.sleep(CALL_DELAY)

        total_ok = sum(1 for t in transaction_log if t["success"])
        print(f"  Round {round_num}: {round_ok} ok | Total: {total_ok}/{len(transaction_log)}")

        # Track all-fail streaks for early exit
        if round_attempted > 0 and round_ok == 0:
            all_fail_streak += 1
        else:
            all_fail_streak = 0

        # Early exit if all agents are skipped or all failing
        active_agents = [a for a in agents if a["label"] not in skipped_agents]
        if not active_agents:
            print(f"\n  All agents skipped due to consecutive failures. Exiting early.")
            break

        if ALL_FAIL_EXIT and all_fail_streak >= ALL_FAIL_EXIT:
            print(f"\n  All agents failed for {all_fail_streak} consecutive rounds. Exiting early.")
            break

        if running and (not MAX_ROUNDS or round_num < MAX_ROUNDS):
            time.sleep(LOOP_DELAY)

    print_summary()


if __name__ == "__main__":
    main()
