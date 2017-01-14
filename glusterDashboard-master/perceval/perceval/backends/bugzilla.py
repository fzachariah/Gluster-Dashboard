# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2016 Bitergia
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
#     Santiago Dueñas <sduenas@bitergia.com>
#     Alvaro del Castillo San Felix <acs@bitergia.com>
#

import csv
import datetime
import json
import logging
import os.path
import re

import bs4
import requests

from ..backend import Backend, BackendCommand, metadata
from ..cache import Cache
from ..errors import BackendError, CacheError, ParseError
from ..utils import DEFAULT_DATETIME, str_to_datetime, xml_to_dict


MAX_BUGS = 200 # Maximum number of bugs per query

logger = logging.getLogger(__name__)


def get_update_time(item):
    """Extracts the update time from a Bugzilla item"""
    return item['delta_ts'][0]['__text__']


class Bugzilla(Backend):
    """Bugzilla backend.

    This class allows the fetch the bugs stored in Bugzilla
    repository. To initialize this class the URL of the server
    must be provided.

    :param url: Bugzilla server URL
    :param max_bugs: maximum number of bugs requested on the same query
    :param cache: cache object to store raw data
    """
    version = '0.1.0'

    def __init__(self, url, max_bugs=MAX_BUGS, cache=None):
        super().__init__(url, cache=cache)
        self.url = url
        self.max_bugs = max(1, max_bugs)
        self.client = BugzillaClient(url)

    @metadata(get_update_time)
    def fetch(self, from_date=DEFAULT_DATETIME):
        """Fetch the bugs from the repository.

        The method retrieves, from a Bugzilla repository, the bugs
        updated since the given date.

        :param from_date: obtain bugs updated since this date

        :returns: a generator of bugs
        """
        if not from_date:
            from_date = DEFAULT_DATETIME

        logger.info("Looking for bugs: '%s' updated from '%s'",
                    self.url, str(from_date))

        self._purge_cache_queue()

        buglist = [bug for bug in self.__fetch_buglist(from_date)]

        nbugs = 0
        tbugs = len(buglist)

        for i in range(0, tbugs, self.max_bugs):
            chunk = buglist[i:i + self.max_bugs]
            bugs_ids = [b['bug_id'] for b in chunk]

            logger.info("Fetching bugs: %s/%s", i, tbugs)
            bugs = self.__fetch_and_parse_bugs_details(bugs_ids)

            for bug in bugs:
                bug_id = bug['bug_id'][0]['__text__']
                bug['activity'] = self.__fetch_and_parse_bug_activity(bug_id)
                nbugs += 1
                yield bug

            self._flush_cache_queue()

        logger.info("Fetch process completed: %s/%s bugs fetched",
                    nbugs, tbugs)

    @metadata(get_update_time)
    def fetch_from_cache(self):
        """Fetch the bugs from the cache.

        It returns the bugs stored in the cache object provided during
        the initialization of the object. If this method is called but
        no cache object was provided, the method will raise a `CacheError`
        exception.

        :returns: a generator of bugs

        :raises CacheError: raised when an error occurs accesing the
            cache
        """
        if not self.cache:
            raise CacheError(cause="cache instance was not provided")

        logger.info("Retrieving cached bugs: '%s'", self.url)

        cache_items = self.cache.retrieve()

        nbugs = 0

        while True:
            try:
                raw_bugs = next(cache_items)
            except StopIteration:
                break

            bugs = self.parse_bugs_details(raw_bugs)

            for bug in bugs:
                try:
                    raw_activity = next(cache_items)
                except StopIteration:
                    # Fatal error. The code should not reach here.
                    # Cache should had stored an activity item per parsed bug.
                    cause = "cache is exhausted but more items were expected"
                    raise CacheError(cause=cause)

                activity = self.parse_bug_activity(raw_activity)
                bug['activity'] = [event for event in activity]
                nbugs += 1
                yield bug

        logger.info("Retrieval process completed: %s bugs retrieved from cache",
                    nbugs)

    def __fetch_buglist(self, from_date):
        buglist = self.__fetch_and_parse_buglist_page(from_date)

        while buglist:
            bug = buglist.pop(0)
            last_date = bug['changeddate']
            yield bug

            # Bugzilla does not support pagination. Due to this,
            # the next list of bugs is requested adding one second
            # to the last date obtained.
            if not buglist:
                from_date = str_to_datetime(last_date)
                from_date += datetime.timedelta(seconds=1)
                buglist = self.__fetch_and_parse_buglist_page(from_date)

    def __fetch_and_parse_buglist_page(self, from_date):
        logger.debug("Fetching and parsing buglist page from %s", str(from_date))
        raw_csv = self.client.buglist(from_date=from_date)
        buglist = self.parse_buglist(raw_csv)
        return [bug for bug in buglist]

    def __fetch_and_parse_bugs_details(self, *bug_ids):
        logger.debug("Fetching and parsing bugs details")
        raw_bugs = self.client.bugs(*bug_ids)
        self._push_cache_queue(raw_bugs)
        return self.parse_bugs_details(raw_bugs)

    def __fetch_and_parse_bug_activity(self, bug_id):
        logger.debug("Fetching and parsing bug #%s activity", bug_id)
        raw_activity = self.client.bug_activity(bug_id)
        self._push_cache_queue(raw_activity)
        activity = self.parse_bug_activity(raw_activity)
        return [event for event in activity]

    @staticmethod
    def parse_buglist(raw_csv):
        """Parse a Bugzilla CSV bug list.

        The method parses the CSV file and returns an iterator of
        dictionaries. Each one of this, contains the summary of a bug.

        :param raw_csv: CSV string to parse

        :returns: a generator of parsed bugs
        """
        reader = csv.DictReader(raw_csv.split('\n'),
                                delimiter=',', quotechar='"')
        for row in reader:
            yield row

    @staticmethod
    def parse_bugs_details(raw_xml):
        """Parse a Bugilla bugs details XML stream.

        This method returns a generator which parses the given XML,
        producing an iterator of dictionaries. Each dictionary stores
        the information related to a parsed bug.

        If the given XML is invalid or does not contains any bug, the
        method will raise a ParseError exception.

        :param raw_xml: XML string to parse

        :returns: a generator of parsed bugs

        :raises ParseError: raised when an error occurs parsing
            the given XML stream
        """
        bugs = xml_to_dict(raw_xml)

        if 'bug' not in bugs:
            cause = "No bugs found. XML stream seems to be invalid."
            raise ParseError(cause=cause)

        for bug in bugs['bug']:
            yield bug

    @staticmethod
    def parse_bug_activity(raw_html):
        """Parse a Bugzilla bug activity HTML stream.

        This method extracts the information about activity from the
        given HTML stream. The bug activity is stored into a HTML
        table. Each parsed activity event is returned into a dictionary.

        If the given HTML is invalid, the method will raise a ParseError
        exception.

        :param raw_html: HTML string to parse

        :returns: a generator of parsed activity events

        :raises ParseError: raised when an error occurs parsing
            the given HTML stream
        """
        def is_activity_empty(bs):
            EMPTY_ACTIVITY = "No changes have been made to this bug yet."
            tag = bs.find(text=re.compile(EMPTY_ACTIVITY))
            return tag is not None

        def find_activity_table(bs):
            # The first table with 5 columns is the table of activity
            tables = bs.find_all('table')

            for tb in tables:
                nheaders = len(tb.tr.find_all('th', recursive=False))
                if nheaders == 5:
                    return tb
            raise ParseError(cause="Table of bug activity not found.")

        def remove_tags(bs):
            HTML_TAGS_TO_REMOVE = ['a', 'i', 'span']

            for tag in bs.find_all(HTML_TAGS_TO_REMOVE):
                tag.replaceWith(tag.text)

        def format_text(bs):
            strings = [s.strip(' \n\t') for s in bs.stripped_strings]
            s = ' '.join(strings)
            return s

        # Parsing starts here
        bs = bs4.BeautifulSoup(raw_html)

        if is_activity_empty(bs):
            fields = []
        else:
            activity_tb = find_activity_table(bs)
            remove_tags(activity_tb)
            fields = activity_tb.find_all('td')

        while fields:
            # First two fields: 'Who' and 'When'.
            who = fields.pop(0)
            when = fields.pop(0)

            # The attribute 'rowspan' of 'who' field tells how many
            # changes were made on the same date.
            n = int(who.get('rowspan'))

            # Next fields are split into chunks of three elements:
            # 'What', 'Removed' and 'Added'. These chunks share
            # 'Who' and 'When' values.
            for _ in range(n):
                what = fields.pop(0)
                removed = fields.pop(0)
                added = fields.pop(0)
                event = {'Who'     : format_text(who),
                         'When'    : format_text(when),
                         'What'    : format_text(what),
                         'Removed' : format_text(removed),
                         'Added'   : format_text(added)}
                yield event


class BugzillaCommand(BackendCommand):
    """Class to run Bugzilla backend from the command line."""

    def __init__(self, *args):
        super().__init__(*args)

        self.url = self.parsed_args.url
        self.max_bugs = self.parsed_args.max_bugs
        self.from_date = str_to_datetime(self.parsed_args.from_date)
        self.outfile = self.parsed_args.outfile

        if not self.parsed_args.no_cache:
            if not self.parsed_args.cache_path:
                base_path = os.path.expanduser('~/.perceval/cache/')
            else:
                base_path = self.parsed_args.cache_path

            cache_path = os.path.join(base_path, self.url)

            cache = Cache(cache_path)

            if self.parsed_args.clean_cache:
                cache.clean()
            else:
                cache.backup()
        else:
            cache = None

        self.backend = Bugzilla(self.url, max_bugs=self.max_bugs,
                                cache=cache)

    def run(self):
        """Fetch and print the bugs.

        This method runs the backend to fetch the bugs from the given
        repository. Bugs are converted to JSON objects and printed to the
        defined output.
        """
        if self.parsed_args.fetch_cache:
            bugs = self.backend.fetch_from_cache()
        else:
            bugs = self.backend.fetch(from_date=self.from_date)

        try:
            for bug in bugs:
                obj = json.dumps(bug, indent=4, sort_keys=True)
                self.outfile.write(obj)
                self.outfile.write('\n')
        except IOError as e:
            raise RuntimeError(str(e))
        except Exception as e:
            if self.backend.cache:
                self.backend.cache.recover()
            raise RuntimeError(str(e))

    @classmethod
    def create_argument_parser(cls):
        """Returns the Bugzilla argument parser."""

        parser = super().create_argument_parser()

        # Bugzilla options
        group = parser.add_argument_group('Bugzilla arguments')
        group.add_argument('--max-bugs', dest='max_bugs',
                           type=int, default=MAX_BUGS,
                           help="Maximum number of bugs requested on the same query")

        # Required arguments
        parser.add_argument('url',
                            help="URL of the Bugzilla server")

        return parser


class BugzillaClient:
    """Bugzilla API client.

    This class implements a simple client to retrieve distinct
    kind of data from a Bugzilla repository. Currently, it only
    supports 3.x and 4.x servers.

    When it is initialized, it checks if the given Bugzilla is
    available and retrieves its version.

    :param base_url: URL of the Bugzilla server

    :raises BackendError: when an error occurs initilizing the
        client
    """
    URL = "%(base)s/%(cgi)s"
    HEADERS = {'User-Agent': 'perceval-bg-0.1'}

    # Regular expression to check the Bugzilla version
    VERSION_REGEX = re.compile(r'.+bugzilla version="([^"]+)"',
                               flags=re.DOTALL)

    # Bugzilla versions that follow the old style queries
    OLD_STYLE_VERSIONS = ['3.2.3', '3.2.2']

    # CGI methods
    CGI_BUGLIST = 'buglist.cgi'
    CGI_BUG = 'show_bug.cgi'
    CGI_BUG_ACTIVITY = 'show_activity.cgi'

    # CGI params
    PBUG_ID= 'id'
    PCHFIELD_FROM = 'chfieldfrom'
    PCTYPE = 'ctype'
    PORDER = 'order'
    PEXCLUDE_FIELD = 'excludefield'

    # Content-type values
    CTYPE_CSV = 'csv'
    CTYPE_XML = 'xml'


    def __init__(self, base_url):
        self.base_url = base_url
        self.version = None

    def metadata(self):
        """Get metadata information in XML format."""

        params = {
            self.PCTYPE : self.CTYPE_XML
        }

        response = self.call(self.CGI_BUG, params)

        return response

    def buglist(self, from_date=DEFAULT_DATETIME):
        """Get a summary of bugs in CSV format.

        :param from_date: retrieve bugs that where updated from that date
        """
        if not self.version:
            self.version = self.__fetch_version()

        if self.version in self.OLD_STYLE_VERSIONS:
            order = 'Last+Changed'
        else:
            order = 'changeddate'

        date = from_date.strftime("%Y-%m-%d %H:%M:%S")

        params = {
            self.PCHFIELD_FROM : date,
            self.PCTYPE : self.CTYPE_CSV,
            self.PORDER : order
        }

        response = self.call(self.CGI_BUGLIST, params)

        return response

    def bugs(self, *bug_ids):
        """Get the information of a list of bugs in XML format.

        :param bug_ids: list of bug identifiers
        """
        params = {
            self.PBUG_ID : bug_ids,
            self.PCTYPE : self.CTYPE_XML,
            self.PEXCLUDE_FIELD : 'attachmentdata'
        }

        response = self.call(self.CGI_BUG, params)

        return response

    def bug_activity(self, bug_id):
        """Get the activity of a bug in HTML format.

        :param bug_id: bug identifier
        """
        params = {
            self.PBUG_ID : bug_id
        }

        response = self.call(self.CGI_BUG_ACTIVITY, params)

        return response

    def call(self, cgi, params):
        """Run an API command.

        :param cgi: cgi method to run on the server
        :param params: dict with the HTTP parameters needed to run
            the given method
        """
        url = self.URL % {'base' : self.base_url, 'cgi' : cgi}

        logger.debug("Bugzilla client calls method: %s params: %s",
                     cgi, str(params))

        req = requests.get(url, params=params,
                           headers=self.HEADERS)
        req.raise_for_status()

        return req.text

    def __fetch_version(self):
        response = self.metadata()
        m = re.match(self.VERSION_REGEX, response)

        if m:
            version = m.group(1)
            logger.debug("Bugzilla server is online: %s (v. %s)",
                         self.base_url, version)
            return version
        else:
            cause = "Bugzilla client could not determine the server version."
            raise BackendError(cause=cause)
