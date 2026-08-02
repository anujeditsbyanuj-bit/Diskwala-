import os, logging, sys

API_ID = 34999432
API_HASH = "631117ef44de6b6628da129dd4f4406c"
BOT_TOKEN = "8626993234:AAE4BTXWRaNHt7vmJ43wQ_WoPrjOQvuD2zw"

OWNER_ID = 8414819080
TG_BOT_WORKERS = 4
FORCE_SUB_CHANNEL = "proerro"  # without the @

DB_URI = os.getenv("DATABASE_URL", "mongodb+srv://nikl85743_db_user:4ztyZ1IVyVID6z3y@cluster0.6tgpbcj.mongodb.net/?retryWrites=true&w=majority")
DB_NAME = os.getenv("DATABASE_NAME", "devil2")
SESSION = "1BVtsOHsBu3bw7XMJ0h9x58YqS-4XY8SskX2f8DWLPMUeiwl_lYmBKBxcNps38_8ECQxORzSjnw9hSohv_L7KnVJbyjAaphSRrtvlyEnUrDnFQ7NkAhC45uKyILOzM1SVSpK9AZzmlhv8LrINZLsewyDQpDtgDypRCt0zI9NqeRurcoGLdOj3wjvQbJSTD9h_8f0MJa7km5yQGsgR1JPNc86yEhnxVBIOi4l9mYFnT5tYVKVETXLeY9hco375fCMMYnQA_BLRcrkoOFdXa5w_MOM_TbB2yKBY4X-HXfOrmC6RYm-qv58mCj-_7l7sTnEEu52cpgow8tsLkN21S0fhiMUXLkZCPeE="
 
commands = ['login','start','setwatermark']

PLANS = [
    {"label": "1 Day",   "days": 1,   "price": "₹10"},
    {"label": "7 Days",  "days": 7,   "price": "₹59"},
    {"label": "30 Days", "days": 30,  "price": "₹149"},
]

PAYMENT_ACCOUNTS = {
    "kartik": {"upi": "paytm.s1xvid5@pty", "merchant": "axdolt07031393696413"},
    "ronak":  {"upi": "paytm.s1xvid5@pty",  "merchant": "axdolt07031393696413"},
}
ACTIVE_PAYMENT = "ronak"  # which account currently receives payments

LOG_CHANNELS = {
    "kartik": -1004497361680,
    "ronak": -1004497361680,
}

PAYMENT_VERIFY_API = "https://pay-rho-seven.vercel.app/"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
LOGGER = lambda name: logging.getLogger(name)