import re, requests, bot
home = requests.get("https://topfm.mu/", headers=bot.UA, timeout=30).text
links = [l for l in dict.fromkeys(re.findall(r'href="(/actualites/[a-z0-9-]{15,})"', home))][:6]
links = [l for l in links if "navratri" in l or "skandamata" in l] + links[:2]
for l in links[:3]:
    url = "https://topfm.mu" + l
    page = requests.get(url, headers=bot.UA, timeout=30).text
    img = bot._meta(page, "og:image")
    print("\n", url, "\n og:image:", repr(img))
    for u in [img]:
        try:
            r = requests.get(u, headers=bot.UA, timeout=30)
            print("  GET", r.status_code, r.headers.get("content-type"), len(r.content), "bytes; final:", r.url[:120])
        except Exception as e:
            print("  GET fail", type(e).__name__, e)
    print(" imgs in page:", re.findall(r'<img[^>]+src="([^"]+)"', page)[:4])
