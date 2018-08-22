from datetime import date, datetime
import logging
import os
import retrying
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "testing"))

import sdk_cmd
import sdk_utils

from service_bundle import ServiceBundle
from base_tech_bundle import BASE_TECH_BUNDLE

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


DEFAULT_RETRY_WAIT = 1000
DEFAULT_RETRY_MAX_ATTEMPTS = 5


def directory_date_string() -> str:
    return date.strftime(datetime.now(), "%Y%m%d%H%M%S")


def output_directory_name(package_name: str, service_name: str) -> str:
    return "{}_{}".format(
        sdk_utils.get_deslashed_service_name(service_name), directory_date_string()
    )


def create_output_directory(package_name: str, service_name: str) -> str:
    directory_name = output_directory_name(package_name, service_name)

    if not os.path.exists(directory_name):
        logger.info("Creating directory {}".format(directory_name))
        os.makedirs(directory_name)

    return directory_name


class FullBundle(Bundle):
    def __init__(self, package_name, service_name):
        self.package_name = package_name
        self.service_name = service_name

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT,
        stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS,
    )
    def install_service_cli(self):
        sdk_cmd.run_cli(
            "package install {} --cli --yes".format(self.package_name),
            print_output=False,
        )

    def create(self):
        output_directory = create_output_directory(
            args.get("package_name"), args.get("service_name")
        )

        ServiceBundle(self.package_name, self.service_name, output_directory).create()

        base_tech_bundle = BASE_TECH_BUNDLE.get(self.package_name)

        if base_tech_bundle:
            base_tech_bundle(
                self.package_name, self.service_name, output_directory
            ).create()
        else:
            logger.info(
                "Don't know how to get base tech diagnostics for package '%s'",
                self.package_name,
            )
            logger.info(
                "Supported packages:\n%s",
                "\n".join(["- {}".format(k) for k in sorted(BASE_TECH_BUNDLE.keys())]),
            )

        logger.info("Done!")
