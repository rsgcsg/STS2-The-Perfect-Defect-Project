"""Compatibility alias for the shared neutral OS-held owner lock."""
import sys

from spireagent import instance_lock as _implementation
from spireagent.instance_lock import instance_lock as instance_lock

sys.modules[__name__] = _implementation
