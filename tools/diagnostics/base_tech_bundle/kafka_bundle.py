#!/usr/bin/env python3
#
# TODO: usage, description.

from datetime import date, datetime
from typing import List, Any
import argparse
import json
import logging
import os
import re
import retrying
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "testing"))

from sdk_utils import groupby
import sdk_cmd
import sdk_diag
import sdk_utils

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


def attached_dcos_cluster() -> (int, Any):
    rc, stdout, stderr = sdk_cmd.run_raw_cli("cluster list --attached", print_output=False)

    if rc != 0:
        if "No cluster is attached" in stderr:
            return (rc, stderr)
        else:
            return (False, "Unexpected error\nstdout: '{}'\nstderr: '{}'".format(stdout, stderr))

    (cluster_name, _, dcos_version, cluster_url) = stdout.split("\n")[-1].split()
    return (rc, (cluster_name, dcos_version, cluster_url))


def is_authenticated_to_dcos_cluster() -> (bool, str):
    rc, stdout, stderr = sdk_cmd.run_raw_cli("service", print_output=False)

    if rc != 0:
        if "dcos auth login" in stderr:
            return (False, stderr)
        else:
            return (False, "Unexpected error\nstdout: '{}'\nstderr: '{}'".format(stdout, stderr))

    return (True, "Authenticated")


def get_marathon_app(service_name: str) -> (int, Any):
    rc, stdout, stderr = sdk_cmd.run_raw_cli(
        "marathon app show {}".format(service_name), print_output=False
    )

    if rc != 0:
        if "does not exist" in stderr:
            return (rc, "Service {} does not exist".format(service_name))
        else:
            return (rc, "Unexpected error\nstdout: '{}'\nstderr: '{}'".format(stdout, stderr))

    try:
        return (rc, json.loads(stdout))
    except Exception as e:
        return (1, "Error decoding JSON: {}".format(e))


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def _get_task_page(offset: int, limit: int, framework_id: str = None, task_id: str = None) -> dict:
    path = "/mesos/tasks?offset={}&limit={}".format(offset, limit)

    if framework_id:
        path += "&framework_id={}".format(framework_id)

    if task_id:
        path += "&task_id={}".format(task_id)

    return sdk_cmd.cluster_request("GET", path).json()


def _get_tasks(
    tasks: dict, offset: int, limit: int, framework_id: str = None, task_id: str = None
) -> dict:
    page = _get_task_page(offset, limit, framework_id=framework_id, task_id=task_id)
    page_tasks = page.get("tasks", [])
    tasks_tasks = tasks.get("tasks", [])
    merged_tasks = tasks_tasks + page_tasks
    tasks["tasks"] = merged_tasks

    if len(page_tasks) < limit:
        return tasks
    else:
        return _get_tasks(tasks, offset + limit, limit, framework_id=framework_id, task_id=task_id)


def get_tasks(framework_id: str = None, task_id: str = None) -> dict:
    offset = 0
    limit = 100
    tasks = {}

    return _get_tasks(tasks, offset, limit, framework_id=framework_id, task_id=task_id)


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def marathon_app_show(marathon_app_name: str) -> dict:
    return sdk_cmd.get_json_output(
        "marathon app show {}".format(marathon_app_name), print_output=False
    )


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def get_scheduler_tasks(app: dict) -> dict:
    return list(map(lambda task: get_tasks(task_id=task["id"])["tasks"][0], app["tasks"]))


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def debug_agent_files(agent_id: str) -> List[str]:
    return sdk_cmd.cluster_request("GET", "/slave/{}/files/debug".format(agent_id)).json()


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def browse_agent_path(agent_id: str, agent_path: str) -> List[dict]:
    return sdk_cmd.cluster_request(
        "GET", "/slave/{}/files/browse?path={}".format(agent_id, agent_path)
    ).json()


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def browse_executor_sandbox(agent_id: str, executor_sandbox_path: str) -> List[dict]:
    return browse_agent_path(agent_id, executor_sandbox_path)


def browse_executor_tasks(agent_id: str, executor_sandbox_path: str) -> List[dict]:
    executor_sandbox = browse_executor_sandbox(agent_id, executor_sandbox_path)
    tasks_directory = next(
        filter(
            lambda f: f["mode"].startswith("d") and f["path"].endswith("/tasks"), executor_sandbox
        ),
        None,
    )

    if tasks_directory:
        return browse_agent_path(agent_id, tasks_directory["path"])
    else:
        return []


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def browse_task_sandbox(agent_id: str, executor_sandbox_path: str, task_id: str) -> List[dict]:
    executor_tasks = browse_executor_tasks(agent_id, executor_sandbox_path)

    if executor_tasks:
        task_sandbox_path = os.path.join(executor_sandbox_path, "tasks/{}/".format(task_id))
        return browse_agent_path(agent_id, task_sandbox_path)
    else:
        return []


@retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
def download_agent_path(
    agent_id: str, agent_file_path: str, output_file_path: str, chunk_size: int = 8192
) -> None:
    stream = sdk_cmd.cluster_request(
        "GET", "/slave/{}/files/download?path={}".format(agent_id, agent_file_path), stream=True
    )
    with open(output_file_path, "wb") as f:
        for chunk in stream.iter_content(chunk_size=chunk_size):
            f.write(chunk)


def download_sandbox_files(
    agent_id: str, sandbox: List[dict], output_base_path: str, patterns_to_download: List[str] = []
) -> List[dict]:
    if not os.path.exists(output_base_path):
        os.makedirs(output_base_path)

    for task_file in sandbox:
        task_file_basename = os.path.basename(task_file["path"])
        for pattern in patterns_to_download:
            if re.match(pattern, task_file_basename):
                download_agent_path(
                    agent_id, task_file["path"], os.path.join(output_base_path, task_file_basename)
                )


def download_task_files(
    agent_id: str,
    executor_sandbox_path: str,
    task_id: str,
    base_path: str,
    patterns_to_download: List[str] = [],
) -> List[dict]:
    executor_sandbox = browse_executor_sandbox(agent_id, executor_sandbox_path)
    task_sandbox = browse_task_sandbox(agent_id, executor_sandbox_path, task_id)

    # Pod task: download both its logs and its parent executor's logs.
    if task_sandbox:
        output_task_directory = os.path.join(base_path, task_id, "task")
        download_sandbox_files(
            agent_id, executor_sandbox, output_task_directory, patterns_to_download
        )

        output_executor_directory = os.path.join(base_path, task_id, "executor")
        download_sandbox_files(
            agent_id, task_sandbox, output_executor_directory, patterns_to_download
        )
    # Scheduler task: no parent executor, download only scheduler logs.
    else:
        output_directory = os.path.join(base_path, task_id)
        download_sandbox_files(agent_id, task_sandbox, output_directory, patterns_to_download)


class Bundle(object):
    def write_file(self, file_name, content, serialize_to_json=False):
        file_path = os.path.join(self.directory_name, file_name)

        with open(file_path, "w") as f:
            logger.info("Writing file {}".format(file_path))
            if serialize_to_json:
                json.dump(content, f, indent=2, sort_keys=True)
            else:
                f.write(content)
                f.write("\n")

    def create(self):
        raise NotImplementedError


class ServiceBundle(Bundle):
    DOWNLOAD_FILES_WITH_PATTERNS = ["^stdout(\.\d+)?$", "^stderr(\.\d+)?$"]

    def __init__(self, package_name, service_name, directory_name):
        self.package_name = package_name
        self.service_name = service_name
        self.directory_name = directory_name

    def tasks(self):
        return get_tasks(self.framework_id)["tasks"]

    def tasks_with_state(self, state):
        return list(
            filter(lambda task: task["state"] == state, self.tasks())
        )

    def running_tasks(self):
        return self.tasks_with_state("TASK_RUNNING")

    def tasks_with_state_and_prefix(self, state, prefix):
        return list(
            filter(lambda task: task["name"].startswith(prefix), self.tasks_with_state(state))
        )

    def run_on_tasks(self, fn, task_ids):
        for task_id in task_ids:
            fn(task_id)

    def for_each_running_task(self, fn):
        task_ids = list(map(lambda task: task["id"], self.running_tasks()))
        self.run_on_tasks(fn, task_ids)

    def for_each_running_task_with_prefix(self, prefix, fn):
        task_ids = list(
            map(
                lambda task: task["id"],
                filter(lambda task: task["name"].startswith(prefix), self.running_tasks()),
            )
        )
        self.run_on_tasks(fn, task_ids)

    @retrying.retry(wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS)
    def get_framework_id(self) -> str:
        framework_ids = sdk_cmd.svc_cli(
            self.package_name, self.service_name, "debug state framework_id", json=True, print_output=False
        )

        assert len(framework_ids) == 1, "More than 1 Framework ID returned: {}".format(framework_ids)

        self.framework_id = framework_ids[0]

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_configuration_file(self):
        output = sdk_cmd.svc_cli(
            self.package_name, self.service_name, "describe", print_output=False
        )

        self.write_file("service_configuration.json", output)

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_pod_status_file(self):
        output = sdk_cmd.svc_cli(
            self.package_name, self.service_name, "pod status --json", print_output=False
        )

        self.write_file("service_pod_status.json", output)

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_plan_status_file(self, plan):
        output = sdk_cmd.svc_cli(
            self.package_name,
            self.service_name,
            "plan status {} --json".format(plan),
            print_output=False,
        )

        self.write_file("service_plan_status_{}.json".format(plan), output)

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_plans_status_files(self):
        plans = sdk_cmd.svc_cli(
            self.package_name, self.service_name, "plan list", json=True, print_output=False
        )

        for plan in plans:
            self.create_plan_status_file(plan)

    def create_log_files(self):
        scheduler_tasks = get_scheduler_tasks(marathon_app_show(self.service_name))
        pod_tasks = get_tasks(self.framework_id)["tasks"]
        all_tasks = scheduler_tasks + pod_tasks
        tasks_by_agent_id = dict(groupby("slave_id", all_tasks))
        agent_id_by_task_ids = dict(map(lambda task: (task["id"], task["slave_id"]), all_tasks))

        agent_executor_paths = {}
        for agent_id in tasks_by_agent_id.keys():
            agent_executor_paths[agent_id] = debug_agent_files(agent_id)

        task_executor_sandbox_paths = {}
        for agent_id, tasks in tasks_by_agent_id.items():
            for task in tasks:
                task_executor_sandbox_paths[task["id"]] = sdk_diag._find_matching_executor_path(
                    agent_executor_paths[agent_id], sdk_diag._TaskEntry(task)
                )

        for task_id, task_executor_sandbox_path in task_executor_sandbox_paths.items():
            agent_id = agent_id_by_task_ids[task_id]
            download_task_files(
                agent_id,
                task_executor_sandbox_path,
                task_id,
                os.path.join(self.directory_name, "tasks"),
                self.DOWNLOAD_FILES_WITH_PATTERNS,
            )

    def create(self):
        self.get_framework_id()
        self.create_configuration_file()
        self.create_pod_status_file()
        self.create_plans_status_files()
        self.create_log_files()


class BaseTechBundle(ServiceBundle):
    def task_exec(self):
        raise NotImplementedError

    def create(self):
        raise NotImplementedError


class CassandraBundle(BaseTechBundle):
    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def task_exec(self, task_id, cmd):
        full_cmd = " ".join(
            [
                "export JAVA_HOME=$(ls -d ${MESOS_SANDBOX}/jdk*/jre/) &&",
                "export TASK_IP=$(${MESOS_SANDBOX}/bootstrap --get-task-ip) &&",
                "CASSANDRA_DIRECTORY=$(ls -d ${MESOS_SANDBOX}/apache-cassandra-*/) &&",
                cmd,
            ]
        )

        return sdk_cmd.marathon_task_exec(task_id, "bash -c '{}'".format(full_cmd))

    def create_nodetool_status_file(self, task_id):
        rc, stdout, stderr = self.task_exec(task_id, "${CASSANDRA_DIRECTORY}/bin/nodetool status")

        self.write_file("cassandra_nodetool_status_{}.txt".format(task_id), stdout)

    def create_nodetool_tpstats_file(self, task_id):
        rc, stdout, stderr = self.task_exec(task_id, "${CASSANDRA_DIRECTORY}/bin/nodetool tpstats")

        self.write_file("cassandra_nodetool_tpstats_{}.txt".format(task_id), stdout)

    def create_tasks_nodetool_status_files(self):
        self.for_each_running_task_with_prefix("node", self.create_nodetool_status_file)

    def create_tasks_nodetool_tpstats_files(self):
        self.for_each_running_task_with_prefix("node", self.create_nodetool_tpstats_file)

    def create(self):
        logger.info("Creating Cassandra bundle")
        self.create_tasks_nodetool_status_files()
        self.create_tasks_nodetool_tpstats_files()


class ElasticBundle(BaseTechBundle):
    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def task_exec(self, task_id, cmd):
        full_cmd = " ".join(
            [
                "export JAVA_HOME=$(ls -d ${MESOS_SANDBOX}/jdk*/jre/) &&",
                "export TASK_IP=$(${MESOS_SANDBOX}/bootstrap --get-task-ip) &&",
                "ELASTICSEARCH_DIRECTORY=$(ls -d ${MESOS_SANDBOX}/elasticsearch-*/) &&",
                cmd,
            ]
        )

        return sdk_cmd.marathon_task_exec(task_id, "bash -c '{}'".format(full_cmd))

    def create_stats_file(self, task_id):
        command = "curl -s ${MESOS_CONTAINER_IP}:${PORT_HTTP}/_stats"
        rc, stdout, stderr = self.task_exec(task_id, command)
        self.write_file("elasticsearch_stats_{}.json".format(task_id), stdout)

    def create_tasks_stats_files(self):
        self.for_each_running_task_with_prefix("master", self.create_stats_file)

    def create(self):
        logger.info("Creating Elastic bundle")
        self.create_tasks_stats_files()


class HdfsBundle(BaseTechBundle):
    def create(self):
        logger.info("Creating HDFS bundle (noop)")


class KafkaBundle(BaseTechBundle):
    def create(self):
        logger.info("Creating Kafka bundle (noop)")


BASE_TECH_BUNDLE = {
    "beta-cassandra": CassandraBundle,
    "beta-elastic": ElasticBundle,
    "beta-hdfs": HdfsBundle,
    "beta-kafka": KafkaBundle,
    "cassandra": CassandraBundle,
    "elastic": ElasticBundle,
    "hdfs": HdfsBundle,
    "kafka": KafkaBundle,
}

class DcosBundle(Bundle):
    def __init__(self, directory_name):
        self.directory_name = directory_name

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_dcos_version_file(self):
        output = sdk_cmd.run_cli("--version", print_output=False)
        self.write_file("dcos_version.txt", output)

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_task_file(self):
        output = sdk_cmd.run_cli("task --json", print_output=False)
        self.write_file("dcos_task.json", output)

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def create_marathon_task_list_file(self):
        output = sdk_cmd.run_cli("marathon task list --json", print_output=False)
        self.write_file("dcos_marathon_task_list.json", output)

    def create(self):
        self.create_dcos_version_file()
        self.create_task_file()
        self.create_marathon_task_list_file()

class FullBundle(Bundle):
    def __init__(self, package_name, service_name, directory_name):
        self.package_name = package_name
        self.service_name = service_name
        self.directory_name = directory_name

    @retrying.retry(
        wait_fixed=DEFAULT_RETRY_WAIT, stop_max_attempt_number=DEFAULT_RETRY_MAX_ATTEMPTS
    )
    def install_service_cli(self):
        sdk_cmd.run_cli(
            "package install {} --cli --yes".format(self.package_name), print_output=False
        )

    def create(self):
        DcosBundle(self.directory_name).create()

        ServiceBundle(self.package_name, self.service_name, self.directory_name).create()

        base_tech_bundle = BASE_TECH_BUNDLE.get(self.package_name)

        if base_tech_bundle:
            base_tech_bundle(self.package_name, self.service_name, self.directory_name).create()
        else:
            logger.info(
                "Don't know how to get base tech diagnostics for package '%s'", self.package_name
            )
            logger.info(
                "Supported packages:\n%s",
                "\n".join(["- {}".format(k) for k in sorted(BASE_TECH_BUNDLE.keys())]),
            )

        logger.info("Done!")


def parse_args() -> dict:
    parser = argparse.ArgumentParser(description="Create an SDK service Diagnostics bundle")

    parser.add_argument(
        "--package-name",
        type=str,
        required=True,
        default=None,
        help="The package name for the service to create the bundle for",
    )

    parser.add_argument(
        "--service-name",
        type=str,
        required=True,
        default=None,
        help="The service name to create the bundle for",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Disable interactive mode and assume 'yes' is the answer to all prompts.",
    )

    return parser.parse_args()


def preflight_check() -> (int, bool, dict):
    args = parse_args()
    package_name_given = args.package_name
    service_name = args.service_name
    should_prompt_user = not args.yes

    (is_authenticated, message) = is_authenticated_to_dcos_cluster()
    if not is_authenticated:
        logger.error(
            "We were unable to verify that you're authenticated to a DC/OS cluster.\nError: %s",
            message,
        )
        return (1, False, {})

    (rc, cluster_or_error) = attached_dcos_cluster()
    if rc != 0:
        logger.error(
            "We were unable to verify the cluster you're attached to.\nError: %s",
            service_name,
            cluster_or_error,
        )
        return (rc, False, {})

    (cluster_name, dcos_version, cluster_url) = cluster_or_error

    (rc, marathon_app_or_error) = get_marathon_app(service_name)
    if rc == 0:
        package_name = marathon_app_or_error.get("labels", {}).get("DCOS_PACKAGE_NAME")
        package_version = marathon_app_or_error.get("labels", {}).get("DCOS_PACKAGE_VERSION")
    else:
        logger.warn(
            "We were unable to get details about %s.\nError: %s",
            service_name,
            marathon_app_or_error,
        )
        package_name = package_name_given
        package_version = "n/a"

    if package_name_given != package_name:
        logger.error(
            "Package name given '%s' is different than actual '%s' package name: '%s'",
            package_name_given,
            service_name,
            package_name,
        )
        return (1, False, {})

    return (
        0,
        True,
        {
            "package_name": package_name,
            "service_name": service_name,
            "package_version": package_version,
            "cluster_name": cluster_name,
            "dcos_version": dcos_version,
            "cluster_url": cluster_url,
            "should_prompt_user": should_prompt_user,
        },
    )


def main(argv):
    (rc, should_proceed, args) = preflight_check()
    if not should_proceed:
        return rc

    logger.info("\nWill create bundle for:")
    logger.info("  Package:         %s", args.get("package_name"))
    logger.info("  Package version: %s", args.get("package_version"))
    logger.info("  Service name:    %s", args.get("service_name"))
    logger.info("  DC/OS version:   %s", args.get("dcos_version"))
    logger.info("  Cluster URL:     %s\n", args.get("cluster_url"))

    if args.get("should_prompt_user"):
        answer = input("\nProceed? [Y/n]: ")
        if answer.strip().lower() in ["n", "no", "false"]:
            return 0

    output_directory = create_output_directory(args.get("package_name"), args.get("service_name"))

    FullBundle(args.get("package_name"), args.get("service_name"), output_directory).create()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
