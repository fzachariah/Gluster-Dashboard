#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Ocean lib
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

"""Ocean feeder for Elastic from  Perseval data"""


import inspect
import json
import logging
import requests

from datetime import datetime


class ElasticOcean(object):

    @classmethod
    def add_params(cls, cmdline_parser):
        """ Shared params in all backends """

        parser = cmdline_parser

        parser.add_argument("-e", "--elastic_url",  default="http://127.0.0.1:9200",
                            help="Host with elastic search" +
                            "(default: http://127.0.0.1:9200)")
        parser.add_argument("--elastic_url-enrich",
                            help="Host with elastic search and enriched indexes")

    def __init__(self, perceval_backend, from_date=None, fetch_cache=False,
                 project=None, insecure=True):

        self.perceval_backend = perceval_backend
        self.last_update = None  # Last update in ocean items index for feed
        self.from_date = from_date  # fetch from_date
        self.fetch_cache = fetch_cache  # fetch from cache
        self.project = project  # project to be used for this data source

        self.requests = requests.Session()
        if insecure:
            requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
            self.requests.verify = False


    def set_elastic(self, elastic):
        """ Elastic used to store last data source state """
        self.elastic = elastic

    def get_field_date(self):
        """ Field with the update in the JSON items. Now the same in all. """
        return "metadata__updated_on"

    def get_field_unique_id(self):
        return "ocean-unique-id"

    def get_elastic_mappings(self):
        """ origin used to filter in incremental updates """
        mapping = '{}'

        return {"items":mapping}

    def get_last_update_from_es(self, _filter = None):
        last_update = self.elastic.get_last_date(self.get_field_date(), _filter)

        return last_update

    def get_connector_name(self):
        """ Find the name for the current connector """
        from ..utils import get_connector_name
        return get_connector_name(type(self))

    def drop_item(self, item):
        """ Drop items not to be inserted in Elastic """
        return False

    def _fix_item(self, item):
        """ Some buggy data sources need fixing (like mbox and message-id) """
        pass

    def add_update_date(self, item):
        """ All item['updated_on'] from perceval is epoch """
        updated = datetime.fromtimestamp(item['updated_on'])
        timestamp = datetime.fromtimestamp(item['timestamp'])
        item['metadata__updated_on'] = updated.isoformat()
        # Also add timestamp used in incremental enrichment
        item['metadata__timestamp'] = timestamp.isoformat()

    def feed(self, from_date=None, offset=None):
        """ Feed data in Elastic from Perceval """

        # Always filter by origin to support multi origin indexes
        filter_ = {"name":"origin",
                   "value":self.perceval_backend.origin}
        self.last_update = self.get_last_update_from_es(filter_)
        last_update = self.last_update
        # last_update = '2015-12-28 18:02:00'
        if from_date:
            # Forced from backend command line.
            last_update = from_date

        logging.info("Incremental from: %s", last_update)

        # Check if backend supports from_date
        signature = inspect.signature(self.perceval_backend.fetch)

        if 'from_date' not in signature.parameters:
            last_update = None
            logging.debug("Fetch method does not use 'from_date' parameter")

        task_init = datetime.now()

        items_pack = []  # to feed item in packs
        drop = 0
        if self.fetch_cache:
            items = self.perceval_backend.fetch_from_cache()
        else:
            if last_update and not offset:
                # if offset used for incremental do not use date
                # Perceval backend from_date must not include timezone
                # It always uses the server datetime
                last_update = last_update.replace(tzinfo=None)
                items = self.perceval_backend.fetch(from_date=last_update)
            else:
                if offset:
                    items = self.perceval_backend.fetch(offset=offset)
                else:
                    items = self.perceval_backend.fetch()
        for item in items:
            # print("%s %s" % (item['url'], item['lastUpdated_date']))
            # Add date field for incremental analysis if needed
            self.add_update_date(item)
            self._fix_item(item)
            if self.project:
                item['project'] = self.project
            if len(items_pack) >= self.elastic.max_items_bulk:
                self._items_to_es(items_pack)
                items_pack = []
            if not self.drop_item(item):
                items_pack.append(item)
            else:
                drop +=1
        self._items_to_es(items_pack)


        total_time_min = (datetime.now()-task_init).total_seconds()/60

        logging.debug("Dropped %i items using drop_item filter" % (drop))
        logging.info("Finished in %.2f min" % (total_time_min))

        return self


    def _items_to_es(self, json_items):
        """ Append items JSON to ES (data source state) """

        if len(json_items) == 0:
            return

        logging.info("Adding items to Ocean for %s (%i items)" %
                      (self, len(json_items)))

        field_id = self.get_field_unique_id()

        self.elastic.bulk_upload_sync(json_items, field_id)

    # Iterator
    def _get_elastic_items(self):
        """ Get the items from the index related to the backend """

        url = self.elastic.index_url
        # 1 minute to process the results of size items
        # In gerrit enrich with 500 items per page we need >1 min
        max_process_items_pack_time = "3m"  # 3 minutes
        url += "/_search?scroll=%s&size=%i" % (max_process_items_pack_time,
                                               self.elastic_page)

        if self.elastic_scroll_id:
            """ Just continue with the scrolling """
            url = self.elastic.url
            url += "/_search/scroll"
            scroll_data = {
                "scroll" : max_process_items_pack_time,
                "scroll_id" : self.elastic_scroll_id
                }
            r = self.requests.post(url, data=json.dumps(scroll_data))
        else:
            filters = "{}"
            # If origin Always filter by origin to support multi origin indexes
            if self.perceval_backend and self.perceval_backend.origin:
                filters = '''
                    {"term":
                        { "origin" : "%s"  }
                    }
                ''' % (self.perceval_backend.origin)

            if self.from_date:
                date_field = self.get_field_date()
                from_date = self.from_date.isoformat()

                filters += '''
                    , {"range":
                        {"%s": {"gte": "%s"}}
                    }
                ''' % (date_field, from_date)

            order_field = 'metadata__updated_on'
            order_query = ''
            if self.perceval_backend:
                # logstash backends does not have the order_field
                order_query = ', "sort": { "%s": { "order": "asc" }} ' % order_field

            query = """
            {
                "query": {
                    "bool": {
                        "must": [%s]
                    }
                } %s
            }
            """ % (filters, order_query)

            # logging.debug("%s %s", url, query)

            r = self.requests.post(url, data=query)

        items = []
        try:
            rjson = r.json()
        except:
            logging.warning("No JSON found in %s" % (r.text))
            logging.warning("No results found from %s" % (url))

        if rjson and "_scroll_id" in rjson:
            self.elastic_scroll_id = rjson["_scroll_id"]
        else:
            self.elastic_scroll_id = None

        if rjson and "hits" in rjson:
            for hit in rjson["hits"]["hits"]:
                items.append(hit['_source'])
        else:
            logging.warning("No results found from %s" % (url))

        return items

    def __iter__(self):

        self.elastic_scroll_id = None
        # In large projects like Eclipse commits, 100 is too much
        # self.elastic_page = 100
        self.elastic_page = 10
        self.iter_items = self._get_elastic_items()

        return self

    def __next__(self):

        if len(self.iter_items) > 0:
            return self.iter_items.pop()
        else:
            if self.elastic_scroll_id:
                self.iter_items = self._get_elastic_items()
            if len(self.iter_items) > 0:
                return self.__next__()
            else:
                raise StopIteration
