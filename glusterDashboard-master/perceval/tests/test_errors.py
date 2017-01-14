#!/usr/bin/env python3
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
#

import sys

if not '..' in sys.path:
    sys.path.insert(0, '..')

import unittest

import perceval.errors as errors


# Mock classes to test BaseError class
class MockErrorNoArgs(errors.BaseError):
    message = "Mock error without args"


class MockErrorArgs(errors.BaseError):
    message = "Mock error with args. Error: %(code)s %(msg)s"


class TestBaseError(unittest.TestCase):

    def test_subblass_with_no_args(self):
        """Check subclasses that do not require arguments.

        Arguments passed to the constructor should be ignored.
        """
        e = MockErrorNoArgs(code=1, msg='Fatal error')

        self.assertEqual("Mock error without args", str(e))

    def test_subclass_args(self):
        """Check subclasses that require arguments"""

        e = MockErrorArgs(code=1, msg='Fatal error')

        self.assertEqual("Mock error with args. Error: 1 Fatal error",
                         str(e))

    def test_subclass_invalid_args(self):
        """Check when required arguments are not given.

        When this happens, it raises a KeyError exception.
        """
        kwargs = {'code' : 1, 'error' : 'Fatal error'}
        self.assertRaises(KeyError, MockErrorArgs, **kwargs)


class TestBackendError(unittest.TestCase):

    def test_message(self):
        """Make sure that prints the correct error"""

        e = errors.BackendError(cause='mock error on backend')
        self.assertEqual('mock error on backend', str(e))


class TestCacheError(unittest.TestCase):

    def test_message(self):
        """Make sure that prints the correct error"""

        e = errors.CacheError(cause='invalid cache')
        self.assertEqual('invalid cache', str(e))


class TestInvalidDateError(unittest.TestCase):

    def test_message(self):
        """Make sure that prints the correct error"""

        e = errors.InvalidDateError(date='1900-13-01')
        self.assertEqual('1900-13-01 is not a valid date',
                         str(e))

class TestParseError(unittest.TestCase):

    def test_message(self):
        """Make sure that prints the correct error"""

        e = errors.ParseError(cause='error on line 10')
        self.assertEqual('error on line 10', str(e))


if __name__ == "__main__":
    unittest.main()
