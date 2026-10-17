"""GigaChat connectivity check (posts nothing; never prints the key or token)."""
import os, uuid, json, requests, certifi, tempfile

KEY = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
print("key set:", bool(KEY), "| length:", len(KEY))

# Russian Ministry of Digital Development root CA (Sber's servers use it)
bundle = None
for url in ["https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
            "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt"]:
    try:
        r = requests.get(url, timeout=30)
        print("cert", url.rsplit("/", 1)[1], r.status_code, len(r.content), "bytes",
              "PEM" if b"BEGIN CERTIFICATE" in r.content else "not PEM")
        if r.ok and b"BEGIN CERTIFICATE" in r.content:
            if bundle is None:
                bundle = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
                bundle.write(open(certifi.where(), "rb").read())
            bundle.write(b"\n" + r.content)
    except Exception as e:
        print("cert download failed:", type(e).__name__, str(e)[:100])
if bundle:
    bundle.close()

token = None
for verify in ([bundle.name] if bundle else []) + [certifi.where()]:
    try:
        r = requests.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                          headers={"Authorization": f"Basic {KEY}", "RqUID": str(uuid.uuid4()),
                                   "Content-Type": "application/x-www-form-urlencoded",
                                   "Accept": "application/json"},
                          data={"scope": "GIGACHAT_API_PERS"}, timeout=30, verify=verify)
        print("oauth with", "ru-bundle" if verify != certifi.where() else "certifi", "->", r.status_code,
              r.text[:150] if not r.ok else "OK (token received)")
        if r.ok:
            token = r.json()["access_token"]; VERIFY = verify
            break
    except Exception as e:
        print("oauth fail with", "ru-bundle" if verify != certifi.where() else "certifi", type(e).__name__, str(e)[:150])

if token:
    H = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    for base in ["https://api.giga.chat/v1", "https://gigachat.devices.sberbank.ru/api/v1"]:
        try:
            r = requests.get(f"{base}/models", headers=H, timeout=30, verify=VERIFY)
            names = [m.get("id") for m in r.json().get("data", [])] if r.ok else r.text[:150]
            print("models", base, r.status_code, names)
        except Exception as e:
            print("models fail", base, type(e).__name__, str(e)[:150]); continue
        for model in ["GigaChat-2", "GigaChat-2-Pro", "GigaChat-2-Max"]:
            try:
                r = requests.post(f"{base}/chat/completions", headers=H, verify=VERIFY, timeout=60,
                                  json={"model": model, "messages": [{"role": "user", "content": "Ответь одним словом: столица Маврикия?"}],
                                        "max_tokens": 20})
                out = r.json()["choices"][0]["message"]["content"] if r.ok else r.text[:150]
                print("  ", model, r.status_code, repr(out), r.json().get("usage") if r.ok else "")
            except Exception as e:
                print("  ", model, "fail", type(e).__name__, str(e)[:120])
        break
    try:
        r = requests.get("https://api.giga.chat/v1/balance", headers=H, timeout=30, verify=VERIFY)
        print("balance", r.status_code, r.text[:400])
    except Exception as e:
        print("balance fail", e)
