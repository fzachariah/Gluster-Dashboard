import json
import re
import requests
import elasticsearch
import bs4
params = {'q': 'Gluster'}
req = requests.get('https://www.google.com/search', params=params)

bs_result = bs4.BeautifulSoup(req.text, 'html.parser')
hit_string = bs_result.find("div", id="resultStats").text
# Remove commas or dots
hit_string = hit_string.replace(',', u'')
hit_string = hit_string.replace('.', u'')
# Strip the hits
hits = re.search('\d+', hit_string).group(0)
hits = int(hits)
print(hits)
es = elasticsearch.Elasticsearch([{'host': 'localhost', 'port': 9200}])
es.index(index='google', doc_type='hits', id=1, body={
     "hits": hits,
     "type": "googleSearchHits",

})
print(es.get(index='google', doc_type='hits', id=1))
