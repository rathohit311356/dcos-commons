import logging
import os
import re
import sys
from typing import List

import retrying

import sdk_cmd
import sdk_diag
from bundle import Bundle
from sdk_utils import groupby

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "testing"))


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


DEFAULT_RETRY_WAIT = 1000
DEFAULT_RETRY_MAX_ATTEMPTS = 5


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


class ServiceBundle(Bundle):
    DOWNLOAD_FILES_WITH_PATTERNS = ["^stdout(\.\d+)?$", "^stderr(\.\d+)?$"]

    def __init__(self, package_name, service_name, output_directory):
        self.package_name = package_name
        self.service_name = service_name
        self.output_directory = output_directory

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
                os.path.join(self.output_directory, "tasks"),
                self.DOWNLOAD_FILES_WITH_PATTERNS,
            )

    def create(self):
        self.get_framework_id()
        self.create_configuration_file()
        self.create_pod_status_file()
        self.create_plans_status_files()
        self.create_log_files()
