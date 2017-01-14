#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
#
# Copyright (C) Bitergia
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

from .utils import get_time_diff_days


class KitsuneEnrich(Enrich):

    def __init__(self, jenkins, db_sortinghat=None, db_projects_map = None):
        super().__init__(db_sortinghat, db_projects_map)
        self.elastic = None
        self.perceval_backend = jenkins
        self.index_jenkins = "jenkins"

    def set_elastic(self, elastic):
        self.elastic = elastic

    def get_field_date(self):
        return "metadata__updated_on"

    def get_field_unique_id(self):
        return "question_id"

    def get_elastic_mappings(self):

        mapping = """
        {
            "properties": {
                "content_analyzed": {
                  "type": "string",
                  "index":"analyzed"
                  },
                "tags_analyzed": {
                  "type": "string",
                  "index":"analyzed"
                  }
           }
        } """

        return {"items":mapping}


    def get_sh_identity(self, owner):
        identity = {}

        identity['username'] = owner['username']
        identity['email'] = None
        identity['name'] = owner['username']
        if owner['display_name']:
            identity['name'] = owner['display_name']

        return identity

    def get_identities(self, item):
        """ Return the identities from an item """
        identities = []

        item = item['data']

        for identity in ['creator']:
            # Todo: questions has also involved and solved_by
            if identity in item and item[identity]:
                user = self.get_sh_identity(item[identity])
                identities.append(user)
            if 'answers_date' in item:
                for answer in item['answers']:
                    user = self.get_sh_identity(answer[identity])
                    identities.append(user)
        return identities

    def get_item_sh(self, item, identity_field):
        """ Add sorting hat enrichment fields for the author of the item """

        eitem = {}  # Item enriched

        update_date = parser.parse(item["updated"])

        # Add Sorting Hat fields
        if identity_field not in item:
            return eitem
        identity  = self.get_sh_identity(item[identity_field])
        eitem = self.get_item_sh_fields(identity, update_date)

        return eitem

    def get_rich_item(self, item, kind='question'):
        eitem = {}

        # Fields common in questions and answers
        common_fields = ["product", "topic", "locale", "is_spam", "title"]

        if kind == 'question':
            eitem['type'] = kind
            # metadata fields to copy
            copy_fields = ["metadata__updated_on","metadata__timestamp","ocean-unique-id","origin"]
            for f in copy_fields:
                if f in item:
                    eitem[f] = item[f]
                else:
                    eitem[f] = None
            # The real data
            question = item['data']

            # data fields to copy
            copy_fields = ["content", "num_answers", "solution"]
            copy_fields += common_fields
            for f in copy_fields:
                if f in question:
                    eitem[f] = question[f]
                else:
                    eitem[f] = None
            eitem["content_analyzed"] = question['content']

            # Fields which names are translated
            map_fields = {
                    "id": "question_id",
                    "num_votes": "score"
            }
            for fn in map_fields:
                eitem[map_fields[fn]] = question[fn]

            tags = ''
            for tag in question['tags']:
                tags += tag['slug'] + ","
            tags = tags[0:-1] # remove last ,
            eitem["tags"] = tags
            eitem["tags_analyzed"] = tags

            # Enrich dates
            eitem["creation_date"] = parser.parse(question["created"]).isoformat()
            eitem["last_activity_date"] = parser.parse(question["updated"]).isoformat()

            eitem['lifetime_days'] = \
                get_time_diff_days(question['created'], question['updated'])

            eitem.update(self.get_grimoire_fields(question['created'], "question"))

            eitem['author'] = question['creator']['username']
            if question['creator']['display_name']:
                eitem['author'] = question['creator']['display_name']

            if self.sortinghat:
                eitem.update(self.get_item_sh(question, "creator"))

        elif kind == 'answer':
            answer = item
            eitem['type'] = kind

            # data fields to copy
            copy_fields = ["content", "solution"]
            copy_fields += common_fields
            for f in copy_fields:
                if f in answer:
                    eitem[f] = answer[f]
                else:
                    eitem[f] = None
            eitem["content_analyzed"] = answer['content']

            # Fields which names are translated
            map_fields = {
                    "id": "answer_id",
                    "question": "question_id",
                    "num_helpful_votes": "score",
                    "num_unhelpful_votes":"unhelpful_answer"
            }
            for fn in map_fields:
                eitem[map_fields[fn]] = answer[fn]

            eitem["helpful_answer"] = answer['num_helpful_votes']

            # Enrich dates
            eitem["creation_date"] = parser.parse(answer["created"]).isoformat()
            eitem["last_activity_date"] = parser.parse(answer["updated"]).isoformat()

            eitem['lifetime_days'] = \
                get_time_diff_days(answer['created'], answer['updated'])

            eitem.update(self.get_grimoire_fields(answer['created'], "answer"))

            eitem['author'] = answer['creator']['username']
            if answer['creator']['display_name']:
                eitem['author'] = answer['creator']['display_name']

            if self.sortinghat:
                eitem.update(self.get_item_sh(answer, "creator"))

        return eitem

    def enrich_items(self, items):
        max_items = self.elastic.max_items_bulk
        current = 0
        bulk_json = ""

        url = self.elastic.index_url+'/items/_bulk'

        logging.debug("Adding items to %s (in %i packs)", url, max_items)

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
            # Time to enrich also de answers
            if 'answers_data' in item['data']:
                for answer in item['data']['answers_data']:
                    # Add question title in answers
                    answer['title'] = item['data']['title']
                    answer['solution'] = 0
                    if answer['id'] == item['data']['solution']:
                        answer['solution'] = 1
                    rich_answer = self.get_rich_item(answer, kind='answer')
                    data_json = json.dumps(rich_answer)
                    bulk_json += '{"index" : {"_id" : "%i_%i" } }\n' % \
                        (rich_answer[self.get_field_unique_id()],
                         rich_answer['answer_id'])
                    bulk_json += data_json +"\n"  # Bulk document
                    current += 1

        self.requests.put(url, data = bulk_json)
