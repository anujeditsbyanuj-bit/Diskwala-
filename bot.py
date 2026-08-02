import requests

BOT_TOKEN = "8626993234:AAE4BTXWRaNHt7vmJ43wQ_WoPrjOQvuD2zw"
CHAT_ID = -1004497361680
USER_ID = 8414819080

url = f"https://api.telegram.org/bot{BOT_TOKEN}/banChatMember"

data = {
    "chat_id": CHAT_ID,
    "user_id": USER_ID,
    "revoke_messages": True
}

r = requests.post(url, json=data)

print(r.status_code)
print(r.json())