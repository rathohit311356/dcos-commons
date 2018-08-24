from datetime import date, datetime
from typing import List
import logging
import os
import retrying
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "testing"))

import sdk_cmd
import sdk_utils

from diagnostics.service_bundle import ServiceBundle
import diagnostics.base_tech_bundle as base_tech
from diagnostics.bundle import Bundle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


DEFAULT_RETRY_WAIT = 1000
DEFAULT_RETRY_MAX_ATTEMPTS = 5


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def get_dcos_services() -> dict:
    return sdk_cmd.get_json_output("service --completed --inactive --json", print_output=False)


def to_dcos_service_name(service_name: str) -> str:
    """DC/OS service names don't contain the first slash.
    e.g.:     SDK service name     ->   DC/OS service name
          /data-services/cassandra -> data-services/cassandra
    """
    return service_name.lstrip("/")


def is_service_named(service_name: str, service: dict) -> bool:
    return service.get("name") == to_dcos_service_name(service_name)


def is_service_active(service: dict) -> bool:
    return service.get("active") == True


def services_with_name(service_name: str, services: List[dict]) -> List[dict]:
    return [s for s in services if is_service_named(service_name, s)]


def active_services_with_name(service_name: str, services: List[dict]) -> List[dict]:
    return [s for s in services if is_service_named(service_name, s) and is_service_active(s)]


def is_service_scheduler_task(package_name: str, service_name: str, task: dict) -> bool:
    labels = task.get("labels", [])
    dcos_package_name = next(
        iter([l.get("value") for l in labels if l.get("key") == "DCOS_PACKAGE_NAME"]), ""
    )
    dcos_service_name = next(
        iter([l.get("value") for l in labels if l.get("key") == "DCOS_SERVICE_NAME"]), ""
    )
    return dcos_package_name == package_name and dcos_service_name == to_dcos_service_name(
        service_name
    )


class FullBundle(Bundle):
    def __init__(self, package_name, service_name):
        self.package_name = package_name
        self.service_name = service_name

    def _directory_date_string(self) -> str:
        return date.strftime(datetime.now(), "%Y%m%d%H%M%S")

    def _output_directory_name(self) -> str:
        return "{}_{}".format(
            sdk_utils.get_deslashed_service_name(self.service_name), self._directory_date_string()
        )

    def _create_output_directory(self) -> str:
        directory_name = self._output_directory_name()

        if not os.path.exists(directory_name):
            logger.info("Creating directory {}".format(directory_name))
            os.makedirs(directory_name)

        return directory_name

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def install_service_cli(self):
        sdk_cmd.run_cli(
            "package install {} --cli --yes".format(self.package_name), print_output=False
        )

    def create(self):
        output_directory = self._create_output_directory()

        all_services = get_dcos_services()

        # An SDK service might have multiple DC/OS service entries. We expect that at most one is "active".
        services = [s for s in all_services if is_service_named(self.service_name, s)]
        # TODO: handle inactive services too.
        active_services = [s for s in services if is_service_active(s)]
        active_service = active_services[0]

        marathon_services = [s for s in all_services if is_service_named("marathon", s)]
        # Should we handle the possibility of having no active Marathon services?
        active_marathon_services = [s for s in marathon_services if is_service_active(s)]
        # Should we handle the possibility of having more than one Marathon service?
        active_marathon_service = active_marathon_services[0]

        # TODO: Search in "unreachable_tasks" too.
        scheduler_tasks = [
            t
            for t in active_marathon_service.get("tasks", [])
            if is_service_scheduler_task(self.package_name, self.service_name, t)
        ]
        scheduler_task = scheduler_tasks[0]

        ServiceBundle(
            self.package_name, self.service_name, scheduler_tasks, active_service, output_directory
        ).create()

        # if base_tech.is_package_supported(self.package_name):
        #     BaseTechBundle = base_tech.get_bundle_class(self.package_name)

        #     BaseTechBundle(
        #         self.package_name,
        #         self.service_name,
        #         scheduler_tasks,
        #         active_service,
        #         output_directory,
        #     ).create()
        # else:
        #     logger.info(
        #         "Don't know how to get base tech diagnostics for package '%s'", self.package_name
        #     )
        #     logger.info(
        #         "Supported packages:\n%s",
        #         "\n".join(["- {}".format(k) for k in base_tech.SUPPORTED_PACKAGES]),
        #     )

        logger.info("\nCreated %s", os.path.abspath(output_directory))
