import unittest

from mypkg import core


class T(unittest.TestCase):
    def test_run(self):
        self.assertTrue(core.run)
