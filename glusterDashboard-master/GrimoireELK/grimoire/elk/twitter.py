#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
#
# Copyright (C) 2015 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#

import json
import logging

from dateutil import parser

from grimoire.elk.enrich import Enrich

class TwitterEnrich(Enrich):

    def __init__(self, twitter, db_sortinghat=None, db_projects_map = None):
        super().__init__(db_sortinghat, db_projects_map)
        self.elastic = None
        self.perceval_backend = twitter
        self.index_twitter = "twitter"

    def set_elastic(self, elastic):
        self.elastic = elastic

    def get_field_date(self):
        return "created_at"

    def get_field_unique_id(self):
        return "id"

    def get_elastic_mappings(self):

        mapping = """
        {
            "properties": {
                "hashtags_analyzed": {
                  "type": "string",
                  "index":"analyzed"
                  },
                "text_analyzed": {
                  "type": "string",
                  "index":"analyzed"
                  },
                  "geolocation": {
                     "type": "geo_point"
                  }
           }
        } """

        return {"items":mapping}

    def get_sh_identity(self, item):
        identity = {}
        identity['username'] = None
        identity['email'] = None
        identity['name'] = None

        if 'user' in item:
            identity['username'] = item['user']['screen_name']
            identity['name'] = item['user']['name']
        return identity

    def get_identities(self, item):
        """ Return the identities from an item """
        identities = []

        user = self.get_sh_identity(item)
        identities.append(user)

        return identities

    def get_rich_item(self, item):
        eitem = {}

        # The real data
        tweet = item

        # data fields to copy
        copy_fields = ["id", "lang", "place", "retweet_count",
                       "text", "in_reply_to_user_id_str", "in_reply_to_screen_name"]
        for f in copy_fields:
            if f in tweet:
                eitem[f] = tweet[f]
            else:
                eitem[f] = None
        # Date fields
        eitem["created_at"]  = parser.parse(tweet["created_at"]).isoformat()
        # Fields which names are translated
        map_fields = {"@timestamp": "timestamp",
                      "@version": "version"
                      }
        for f in map_fields:
            if f in tweet:
                eitem[map_fields[f]] = tweet[f]
            else:
                eitem[map_fields[f]] = None

        # data fields to copy from user
        copy_fields = ["created_at", "description", "followers_count",
                       "friends_count", "id_str", "lang", "location", "name",
                       "url", "utc_offset", "verified"]
        for f in copy_fields:
            if f in tweet['user']:
                eitem["user_" + f] = tweet['user'][f]
            else:
                eitem["user_" + f] = None

        if "text" in tweet:
            eitem["text_analyzed"] = tweet["text"]

        eitem['hashtags_analyzed'] = ''
        for tag in tweet['entities']['hashtags']:
            eitem['hashtags_analyzed'] += tag['text']+","
        eitem['hashtags_analyzed'] = eitem['hashtags_analyzed'][0:-1]

        eitem['retweeted'] = 0
        if tweet['retweeted']:
            eitem['retweeted'] = 1

        eitem['url'] = "http://twitter.com/"+tweet['user']['screen_name']+"/status/"+tweet['id_str']
        eitem['user_url_twitter'] = "http://twitter.com/"+tweet['user']['screen_name']

        if self.sortinghat:
            eitem.update(self.get_item_sh(tweet, "user"))

        eitem.update(self.get_grimoire_fields(tweet["created_at"], "twitter"))

        return eitem

    def enrich_items(self, items):
        max_items = self.elastic.max_items_bulk
        current = 0
        bulk_json = ""

        url = self.elastic.index_url+'/items/_bulk'

        logging.debug("Adding items to %s (in %i packs)" % (url, max_items))

        for item in items:
            if current >= max_items:
                self.requests.put(url, data=bulk_json)
                bulk_json = ""
                current = 0

            rich_item = self.get_rich_item(item)
            data_json = json.dumps(rich_item)
            bulk_json += '{"index" : {"_id" : "%s" } }\n' % \
                (rich_item[self.get_field_unique_id()])
            bulk_json += data_json +"\n"  # Bulk document
            current += 1
        self.requests.put(url, data = bulk_json)
