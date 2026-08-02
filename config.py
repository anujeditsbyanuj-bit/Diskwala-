import os, logging, sys

API_ID = 37476811
API_HASH = "7aa60670b871050820086c6267371ee6"
BOT_TOKEN = "8694198519:AAHkfsd2hG584oC92jM-Ee2PJd2snDy49qM"

OWNER_ID = 8730393744
TG_BOT_WORKERS = 4
FORCE_SUB_CHANNEL = "log_ak_bots"  # without the @

DB_URI = os.getenv("DATABASE_URL", "mongodb+srv://Anujedit:Anujedit@cluster0.7cs2nhd.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DATABASE_NAME", "diskwala")
SESSION = "1BVtsOJ0Bu7iFhYaPaDWTPDdVAtu310L3iOh4PlTSxrTSyGaJPYQzf6rDgAMM9xGktQy9DodpC5TCCDTBMV3AiS4f5SNUbDR6kiPQ0PHUfj--XOQv82ZW2w2e7SM6GXvGdVTDXczbTBypUSYN0pSu-IMCd5atImWZBG6DvOg8o95pKmC9nc0H5jRMCfTBrtFbB0ba6iysaBd515MDP8fEWefKnRB8k8az61yt3hYhNhG-LQ2xgb1bz845tfZUK2KuKzXnmRZiMUmB_0agXDwQSEnTlJa5NlJxken911hhODilu2VCTKwbnweqj9QFHPDGoJcRZ-1GaVeGI-o1K70J3GZqLdcA_Ls="
 
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
    "kartik": -1003824246703,
    "ronak": -1003955674028,
}

PAYMENT_VERIFY_API = "https://pay-rho-seven.vercel.app/"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
LOGGER = lambda name: logging.getLogger(name)
