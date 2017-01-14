import requests
import tweepy
import elasticsearch
ACCESS_TOKEN = '793829198112501760-41cDNkTYIlAtueTlVeVFnNTvAYd4aN1'
ACCESS_SECRET = 'weEGrhmG7doH4dwIbDtMYAcjTllWOoUla5KhDWkP88doZ'
CONSUMER_KEY = 'UliqTh4cO7UZyIYsXN2wtZdyw'
CONSUMER_SECRET = 'kcLRRCC1lVUIrXfLckS4eQsokTzW2ryj5f1z80Gsv5fWzeVkmp'
auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
auth.set_access_token(ACCESS_TOKEN, ACCESS_SECRET)

api = tweepy.API(auth)

user = api.get_user('GlusterDev')
print (user.screen_name)
print (user.followers_count)
print (user.statuses_count)


es = elasticsearch.Elasticsearch([{'host': 'localhost', 'port': 9200}])
es.index(index='twitter', doc_type='trends', id=1, body={
    'handle': user.screen_name,
    'followers': user.followers_count,
    'tweets': user.statuses_count,

})
print(es.get(index='twitter', doc_type='trends', id=1))
