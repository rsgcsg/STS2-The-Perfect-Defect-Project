"""Static compatibility probe for the locked Host Runtime consumer API."""

from sts2_headless import ManagedPlayerEnvironment

from stpd.contracts import PlayerEnvironmentPort


def managed_environment_implements_port(
    environment: ManagedPlayerEnvironment,
) -> PlayerEnvironmentPort:
    return environment
