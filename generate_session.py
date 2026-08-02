"""
Run this LOCALLY (needs interactive OTP login).
1. pip install telethon
2. Get NEW API_ID / API_HASH from https://my.telegram.org (rotate — don't reuse the old leaked app)
3. export API_ID=... ; export API_HASH=...   (or just paste them when prompted below)
4. python generate_session.py
5. Login with the account's phone number + OTP (+ 2FA password if set)
6. Copy the printed session string into your SESSION env var / Replit Secret
"""

import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID") or input("API_ID: "))
API_HASH = os.getenv("API_HASH") or input("API_HASH: ")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\nYour new session string:\n")
    print(client.session.save())
    print("\nPaste this into config.py as SESSION = \"...\"")
