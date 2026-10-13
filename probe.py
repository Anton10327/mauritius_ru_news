"""Check how news sites are structured from GitHub servers (not used by the bot)."""
import re, requests, trafilatura, collections
from urllib.parse import urljoin
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
SITES = ["https://lexpress.mu/", "https://mbcradio.tv/", "https://topfm.mu/"]
for site in SITES:
    print(f"\n######## {site}")
    try:
        html = requests.get(site, timeout=25, headers=H).text
    except Exception as e:
        print("ERR", e); continue
    links = []
    for m in re.finditer(r'href="([^"#]+)"', html):
        u = urljoin(site, m.group(1))
        if u.startswith(site.rstrip("/")) and u not in links:
            links.append(u)
    # group by first path segment and depth
    pat = collections.Counter("/".join(u.split("/")[3:4]) + f" depth={len([p for p in u.split('/')[3:] if p])}" for u in links)
    print("link patterns:", pat.most_common(12))
    long = [u for u in links if len(u.rstrip("/").split("/")[-1]) > 25][:8]
    for u in long: print("  ", u)
    if long:
        a = requests.get(long[0], timeout=25, headers=H)
        txt = trafilatura.extract(a.text) or ""
        meta = {k: (re.search(rf'<meta[^>]+(?:property|name)="{k}"[^>]+content="([^"]+)"', a.text) or [None, None])[1] for k in ["og:title", "og:image", "article:published_time"]}
        print(f"  article {a.status_code}: text {len(txt)} chars | {meta}")
        print("  start:", txt[:200].replace("\n", " "))
print("\n######## Le Mauricien REST")
r = requests.get("https://www.lemauricien.com/wp-json/wp/v2/posts?per_page=3&_fields=id,date,title,link,content,excerpt,jetpack_featured_media_url,yoast_head_json", timeout=25, headers=H)
for p in r.json():
    og = (p.get("yoast_head_json") or {}).get("og_image") or [{}]
    print(p["id"], p["date"], p["title"]["rendered"][:70], "| content", len(p.get("content", {}).get("rendered", "")), "| img", p.get("jetpack_featured_media_url") or og[0].get("url"), "|", p["link"])
