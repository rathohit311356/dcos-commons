import logging
import os
import retrying
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "testing"))

from diagnostics.base_tech_bundle import BaseTechBundle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


DEFAULT_RETRY_WAIT = 1000
DEFAULT_RETRY_MAX_ATTEMPTS = 5


class HdfsBundle(BaseTechBundle):
    def create(self):
        logger.info("Creating HDFS bundle (noop)")
