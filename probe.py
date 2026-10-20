"""Ask GigaChat for the token balance (posts nothing)."""
import bot, requests
tok = bot._giga_token()
for url in ["https://api.giga.chat/v1/balance", "https://gigachat.devices.sberbank.ru/api/v1/balance"]:
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30, verify=bot._giga_verify())
        print(url, r.status_code, r.text[:600])
    except Exception as e:
        print(url, "FAIL", e)
