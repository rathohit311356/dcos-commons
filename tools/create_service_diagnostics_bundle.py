#!/usr/bin/env python3

from datetime import date, datetime
import typing
import argparse
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "testing"))
sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from diagnostics import FullBundle
import sdk_utils

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


def is_authenticated_to_dcos_cluster() -> (bool, str):
    rc, stdout, stderr = sdk_cmd.run_raw_cli("service", print_output=False)

    if rc != 0:
        if "dcos auth login" in stderr:
            return (False, stderr)
        else:
            return (False, "Unexpected error\nstdout: '{}'\nstderr: '{}'".format(stdout, stderr))

    return (True, "Authenticated")


def attached_dcos_cluster() -> (int, Any):
    rc, stdout, stderr = sdk_cmd.run_raw_cli("cluster list --attached", print_output=False)

    if rc != 0:
        if "No cluster is attached" in stderr:
            return (rc, stderr)
        else:
            return (False, "Unexpected error\nstdout: '{}'\nstderr: '{}'".format(stdout, stderr))

    (cluster_name, _, dcos_version, cluster_url) = stdout.split("\n")[-1].split()
    return (rc, (cluster_name, dcos_version, cluster_url))


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

    FullBundle(args.get("package_name"), args.get("service_name")).create()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
