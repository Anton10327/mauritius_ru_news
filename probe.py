"""Check which news sources are reachable from GitHub servers (not used by the bot)."""
import re, requests, feedparser
URLS = """
https://ionnews.mu/wp-json/wp/v2/posts?per_page=2&_fields=date,title,link,content
https://www.lemauricien.com/
https://www.lemauricien.com/wp-json/wp/v2/posts?per_page=2&_fields=date,title,link
https://lematinal.media/feed/
https://lematinal.media/wp-json/wp/v2/posts?per_page=2&_fields=date,title,link
https://www.topfm.mu/feed/
https://topfm.mu/
https://www.mauritiustimes.com/feed
https://govmu.org/EN/newsgov/SitePages/Index.aspx
https://govmu.org/EN/Pages/default.aspx
https://lexpress.mu/
https://www.lexpress.mu/
https://newsmoris.com/wp-json/wp/v2/posts?per_page=2&_fields=date,title,link
https://www.mbc.intnet.mu/
https://mbcradio.tv/
https://www.businessmag.mu/
https://news.google.com/rss/search?q=Maurice+when:1d&hl=fr&gl=MU&ceid=MU:fr
https://news.google.com/rss/search?q=Mauritius+when:1d&hl=en-MU&gl=MU&ceid=MU:en
https://www.lexpress.mu/actualites
https://www.inside.news/wp-json/wp/v2/posts?per_page=1&_fields=date,title
https://www.wazaa.mu/wp-json/wp/v2/posts?per_page=1&_fields=date,title
https://www.zinfos974.com/xml/syndication.rss
https://www.clicanoo.re/rss
""".split()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
for u in URLS:
    try:
        r = requests.get(u, timeout=20, headers=H)
        ct = r.headers.get("content-type", "")[:30]
        info = ""
        if "json" in ct:
            try:
                j = r.json(); info = f"json items={len(j)} | {j[0].get('date','')} | {re.sub('<[^>]+>','',str(j[0].get('title',{}).get('rendered',''))[:60])} | content={len(str(j[0].get('content',{}).get('rendered','')))}ch"
            except Exception as e: info = f"json? {e}"
        elif "xml" in ct or "rss" in ct or r.text.lstrip().startswith("<?xml"):
            f = feedparser.parse(r.text); e = f.entries[0] if f.entries else {}
            info = f"feed items={len(f.entries)} | {e.get('published','')[:25]} | {e.get('title','')[:60]}"
        else:
            t = re.search(r"<title>(.*?)</title>", r.text, re.S)
            info = f"html {len(r.text)//1024}KB | {(t.group(1).strip() if t else '')[:60]}"
        print(f"{r.status_code} {u}\n     {ct} {info}")
    except Exception as ex:
        print(f"ERR {u}: {type(ex).__name__}")
