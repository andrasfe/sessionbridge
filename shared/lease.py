"""Short-lived session lease.

A lease is a task-scoped permission minted by the control plane and handed to
the runner. The raw browser session never leaves the runner's memory; the lease
just authorises *this* job's automation and expires quickly. On AWS this maps to
a Secrets Manager short-TTL secret or an STS-style token; for the PoC it is an
in-process signed-ish opaque token with an expiry.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class Lease:
    token: str
    job_id: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


def mint_lease(job_id: str, ttl: int) -> Lease:
    return Lease(token=secrets.token_urlsafe(32), job_id=job_id,
                 expires_at=time.time() + ttl)
