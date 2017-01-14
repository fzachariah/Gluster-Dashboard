import urllib2
import base64
import json
import os

USER = 'timhayduk'
API_TOKEN='9a0ee3dee3dd9db3b51effe36e3d051a499b5484'
GIT_API_URL='https://api.github.com/users/Gluster/repos'

def get_api():
    try:
        request = urllib2.Request(GIT_API_URL)
        #base64string = base64.encodestring('%s/token:%s' % (USER, API_TOKEN)).replace('\n', '')
        #request.add_header("Authorization", "Basic %s" % base64string)
        result = urllib2.urlopen(request)
        repos = json.load(result)
        result.close()
        return repos
    except Exception as e:
        print ('Failed to get api request from %s' % GIT_API_URL)
        #print (base64string)
        print (e)


if __name__ == "__main__":
    repos = get_api()
    for repo in repos:
        command = "python3 /home/tim/Dashboard-test/GrimoireELK/utils/p2o.py --enrich --index git_gluster -e http://localhost:9200 --no_inc --debug git https://github.com/gluster/" + repo['name']
        os.system(command)
