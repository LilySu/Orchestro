import os
import pathlib
import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from payments_py import Payments, PaymentOptions
from payments_py.x402.fastapi import PaymentMiddleware, X402_HEADERS
from payments_py.x402.resolve_scheme import resolve_scheme
from payments_py.x402.types import CardDelegationConfig, X402TokenOptions
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_PLAN_ID = os.environ["NVM_PLAN_ID"]
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")

# Initialize Payments
payments = Payments.get_instance(
    PaymentOptions(
        nvm_api_key=NVM_API_KEY,
        environment="sandbox"
    )
)

# Protect /ask with x402 middleware
app.add_middleware(
    PaymentMiddleware,
    payments=payments,
    routes={
        "POST /ask": {"plan_id": NVM_PLAN_ID, "credits": 1}
    }
)

# Route handler - no payment logic needed!
@app.post("/ask")
async def ask(request: Request):
    body = await request.json()
    query = body.get("query")
    if query:
        print(f"Received query: {query}")
        return {"response": "Success: Query received"}
    else:
        print("Failure: No query provided")
        return {"response": "Failure: No query provided"}


# Proxy endpoint: browser calls this, server handles full x402 flow
@app.post("/buy")
async def buy(request: Request):
    body = await request.json()
    query = body.get("query", "buy")

    try:
        # Step 1: resolve scheme and build token options
        scheme = resolve_scheme(payments, NVM_PLAN_ID)
        token_options = X402TokenOptions(scheme=scheme)

        if scheme == "nvm:card-delegation":
            methods = payments.delegation.list_payment_methods()
            if not methods:
                return JSONResponse(status_code=402, content={"error": "No payment methods enrolled"})
            pm = methods[0]
            token_options = X402TokenOptions(
                scheme=scheme,
                delegation_config=CardDelegationConfig(
                    provider_payment_method_id=pm.id,
                    spending_limit_cents=10000,
                    duration_secs=604800,
                    currency="usd",
                ),
            )

        # Step 2: generate access token
        token_result = payments.x402.get_x402_access_token(NVM_PLAN_ID, token_options=token_options)
        access_token = token_result["accessToken"]

        # Step 3: call /ask with token
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{SERVER_URL}/ask",
                headers={
                    "Content-Type": "application/json",
                    X402_HEADERS["PAYMENT_SIGNATURE"]: access_token,
                },
                json={"query": query},
            )

        if response.status_code == 200:
            return response.json()
        else:
            return JSONResponse(status_code=response.status_code, content={"error": "Payment failed", "detail": response.text})

    except Exception as e:
        print(f"Error in /buy: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# Serve index.html from project root
ROOT = pathlib.Path(__file__).parent.parent  # api/ -> project root
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")