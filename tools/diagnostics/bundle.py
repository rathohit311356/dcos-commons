import json
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "testing"))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


class Bundle(object):
    def write_file(self, file_name, content, serialize_to_json=False):
        file_path = os.path.join(self.output_directory, file_name)

        with open(file_path, "w") as f:
            logger.info("Writing file {}".format(file_path))
            if serialize_to_json:
                json.dump(content, f, indent=2, sort_keys=True)
            else:
                f.write(content)
                f.write("\n")

    def create(self):
        raise NotImplementedError
