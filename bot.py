import requests

BOT_TOKEN = "8694198519:AAHkfsd2hG584oC92jM-Ee2PJd2snDy49qM"
CHAT_ID = -1003824246703
USER_ID = 8730393744

url = f"https://api.telegram.org/bot{BOT_TOKEN}/banChatMember"

data = {
    "chat_id": CHAT_ID,
    "user_id": USER_ID,
    "revoke_messages": True
}

r = requests.post(url, json=data)

print(r.status_code)
print(r.json())
