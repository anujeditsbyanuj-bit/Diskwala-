import asyncio
import time
from urllib.parse import urlparse, unquote

import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import (
    RequestAppWebViewRequest,
)
from telethon.tl.types import (
    InputBotAppShortName,
    InputPeerSelf,
    DataJSON,
)

API_ID = 34999432
API_HASH = "631117ef44de6b6628da129dd4f4406c"
SESSION = "1BVtsOHsBu3bw7XMJ0h9x58YqS-4XY8SskX2f8DWLPMUeiwl_lYmBKBxcNps38_8ECQxORzSjnw9hSohv_L7KnVJbyjAaphSRrtvlyEnUrDnFQ7NkAhC45uKyILOzM1SVSpK9AZzmlhv8LrINZLsewyDQpDtgDypRCt0zI9NqeRurcoGLdOj3wjvQbJSTD9h_8f0MJa7km5yQGsgR1JPNc86yEhnxVBIOi4l9mYFnT5tYVKVETXLeY9hco375fCMMYnQA_BLRcrkoOFdXa5w_MOM_TbB2yKBY4X-HXfOrmC6RYm-qv58mCj-_7l7sTnEEu52cpgow8tsLkN21S0fhiMUXLkZCPeE="
 

BOT = "sky577bot"
APP_SHORT_NAME = "open"

DOWNLOAD_API = "https://api2.diskwala.net/api/diskwala/download"
STATUS_API = "https://api2.diskwala.net/api/diskwala/status"


async def get_init_data():
    async with TelegramClient(
        StringSession(SESSION),
        API_ID,
        API_HASH,
    ) as client:

        bot = await client.get_input_entity(BOT)

        result = await client(
            RequestAppWebViewRequest(
                peer=InputPeerSelf(),
                app=InputBotAppShortName(
                    bot_id=bot,
                    short_name=APP_SHORT_NAME,
                ),
                platform="android",
                write_allowed=True,
                start_param="",
                theme_params=DataJSON("{}"),
            )
        )

        print("\n========== MINI APP URL ==========")
        print(result.url)

        fragment = urlparse(result.url).fragment
        encoded = fragment.split(
            "tgWebAppData=", 1
        )[1].split("&tgWebAppVersion=", 1)[0]

        init_data = unquote(encoded)

        print("\n========== tgWebAppData ==========")
        print(init_data)
        print("=================================\n")

        return init_data


def wait_for_download(headers, link):
    print("\nStarting Diskwala job...")

    r = requests.post(
        DOWNLOAD_API,
        headers=headers,
        json={"link": link},
        timeout=60,
    )

    print("POST:", r.status_code, r.text)

    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(data)

    while True:
        r = requests.get(
            STATUS_API,
            headers=headers,
            params={"link": link},
            timeout=60,
        )

        print("\n==============================")
        print("GET:", r.url)
        print("HTTP:", r.status_code)

        try:
            data = r.json()
        except Exception:
            print(r.text)
            raise RuntimeError("Invalid JSON")

        print(data)

        if not data.get("ok"):
            raise RuntimeError(data)

        status = data.get("status", "").lower()
        print("Status:", status)

        if status == "pending":
            time.sleep(2)
            continue

        if status == "done":
            if "file" not in data:
                raise RuntimeError(
                    "Done but file missing:\n"
                    + str(data)
                )

            return data["file"]

        raise RuntimeError(data)


async def main():
    link = input("Enter Diskwala link: ").strip()

    init_data = await get_init_data()

    headers = {
        "Authorization": f"Bearer {init_data}",
        "X-Bot-Id": "diskwala",
        "Content-Type": "application/json",
        "Origin": "https://miniapp.diskwala.net",
        "Referer": "https://miniapp.diskwala.net/",
        "Accept": "*/*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
    }

    print("Waiting for Diskwala...")

    file = wait_for_download(headers, link)

    print("\n========== FILE ==========")
    print(file)

    print("\n✅ Download Ready\n")
    print("Name :", file["name"])
    print("Ext  :", file["extension"])
    print("Size :", f"{file['size']:,} bytes")
    print("Thumb:", file["thumb"])
    print("URL  :", file["downloadUrl"])
    print("===========================")


if __name__ == "__main__":
    asyncio.run(main())