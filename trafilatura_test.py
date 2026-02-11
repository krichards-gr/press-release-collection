# import the necessary functions
from trafilatura import fetch_url, extract

# grab a HTML file to extract data from
downloaded = fetch_url('https://about.att.com/aboutus/pressrelease/2026/small-business-contest-winner.html')

# output main content and comments as plain text
result = extract(downloaded)
print(result)


requests.get("https://about.att.com/aboutus/pressrelease/2026/small-business-contest-winner.html")


import requests
from readability import Document

response = requests.get('https://about.att.com/aboutus/pressrelease/2026/small-business-contest-winner.html')
doc = Document(response.content)
doc.title()

doc.summary()



# GOOSE3

from goose3 import Goose
url = 'https://about.att.com/aboutus/pressrelease/2026/small-business-contest-winner.html'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

g = Goose({'browser_user_agent': headers['User-Agent']})
article = g.extract(url=url)
article.title
article.meta_description
article.cleaned_text[:150]



## Cloudscraper
import cloudscraper
from goose3 import Goose

scraper = cloudscraper.create_scraper() # Returns a request object
url = 'https://about.att.com/aboutus/pressrelease/2026/small-business-contest-winner.html'

# Get the HTML first using cloudscraper
html = scraper.get(url).text

# Pass the raw HTML to your extractor instead of the URL
g = Goose()
article = g.extract(raw_html=html)
print(article.title)
