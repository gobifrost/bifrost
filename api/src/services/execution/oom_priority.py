"""Steer the kernel's out-of-memory killer toward work, away from executors.

Standard library only: forked workflow children import it, and they must not
load database libraries.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The kernel's out-of-memory preference for work processes (the maximum).
OOM_SCORE_ADJ_WORK = 1000


def volunteer_for_oom_kill() -> None:
    """Make this process the kernel's first choice when memory runs out.

    Called by every forked run and platform-job runner as it starts, so an
    out-of-memory kill takes a piece of work, never the executor that would
    record its failure. Raising one's own score needs no privilege.
    """
    try:
        with open("/proc/self/oom_score_adj", "w") as score:
            score.write(str(OOM_SCORE_ADJ_WORK))
    except OSError:
        # No /proc (macOS development): there is no OOM killer to steer.
        logger.debug("Could not set oom_score_adj", exc_info=True)


__all__ = ["OOM_SCORE_ADJ_WORK", "volunteer_for_oom_kill"]
