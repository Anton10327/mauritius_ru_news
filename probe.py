"""Which GigaChat model is billed to which token pool (posts nothing)."""
import os, uuid, requests, certifi, tempfile
KEY = os.environ["GIGACHAT_AUTH_KEY"].strip()
b = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
b.write(open(certifi.where(), "rb").read())
for u in ["https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
          "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt"]:
    b.write(b"\n" + requests.get(u, timeout=30).content)
b.close(); V = b.name
tok = requests.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                    headers={"Authorization": f"Basic {KEY}", "RqUID": str(uuid.uuid4()),
                             "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                    data={"scope": "GIGACHAT_API_PERS"}, timeout=30, verify=V).json()["access_token"]
H = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
bal = lambda: {x["usage"]: x["value"] for x in requests.get("https://api.giga.chat/v1/balance", headers=H, verify=V, timeout=30).json()["balance"]}
before = bal(); print("balance:", before)
for m in ["GigaChat-3-Lightning", "GigaChat-3-Pro", "GigaChat-3-Ultra", "GigaChat-2-Pro"]:
    r = requests.post("https://api.giga.chat/v1/chat/completions", headers=H, verify=V, timeout=60,
                      json={"model": m, "messages": [{"role": "user", "content": "Напиши слово «тест» 200 раз через пробел."}], "max_tokens": 600})
    used = r.json().get("usage", {}).get("total_tokens")
    import time; time.sleep(3)
    after = bal(); diff = {k: before[k] - after[k] for k in after if before.get(k) != after[k]}
    print(f"{m}: used {used} tokens -> charged to {diff}"); before = after
