"""FengForge Dunning Smart Retry."""
from .core import (DECLINE_TABLE, MAX_RETRIES, assess_account,
                   build_retry_schedule, triage_decline)

__version__ = "1.0.0"
__all__ = ["triage_decline", "build_retry_schedule", "assess_account",
           "DECLINE_TABLE", "MAX_RETRIES", "__version__"]
