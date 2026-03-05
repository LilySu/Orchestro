import os
from fastapi import FastAPI, Request
from payments_py import Payments, PaymentOptions
from payments_py.x402.fastapi import PaymentMiddleware
from dotenv import load_dotenv
# import os  # duplicate import, not needed

load_dotenv()

app = FastAPI()

# Initialize Payments
payments = Payments.get_instance(
    PaymentOptions(
        nvm_api_key=os.environ["NVM_API_KEY"],
        environment="live" if os.environ.get("ENV") == "production" else "sandbox"
    )
)

# Protect routes with one line
app.add_middleware(
    PaymentMiddleware,
    payments=payments,
    routes={
        "POST /ask": {"plan_id": os.environ["NVM_PLAN_ID"], "credits": 1}
    }
)

# Route handler - no payment logic needed!
@app.post("/ask")
async def ask(request: Request):
    body = await request.json()
    
    query = body.get("query")

    # Original AI call (commented out)
    # response = await generate_ai_response(body.get("query"))

    # Replace AI call with simple success/failure logic
    if query:
        print(f"Received query: {query}")
        return {"response": "Success: Query received"}
    else:
        print("Failure: No query provided")
        return {"response": "Failure: No query provided"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)