"""FengForge Chargeback CE3.0 Compiler."""
from .core import (check_ce30_eligibility, generate_evidence_packet,
                   match_qualifying_transactions)

__version__ = "1.0.0"
__all__ = ["check_ce30_eligibility", "match_qualifying_transactions",
           "generate_evidence_packet", "__version__"]
