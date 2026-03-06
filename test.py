from payments_py import Payments, PaymentOptions
from dotenv import load_dotenv
import os

load_dotenv()

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=os.getenv("NVM_API_KEY"), environment="sandbox")
)

result = payments.plans.order_fiat_plan("111736569662109516650030463386820585016258090562242502132632199468023764656066")
print(result)