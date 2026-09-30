"""Fail after one second to validate crash reporting."""

import time

time.sleep(1)
raise RuntimeError("Intentional fixture failure")
