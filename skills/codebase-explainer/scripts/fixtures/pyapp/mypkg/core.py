"""Core: imports utils, and utils imports core back (an import-time cycle)."""
import os

import requests

from . import utils


def run():
    return utils.helper(os.getcwd(), requests)
