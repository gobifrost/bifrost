"""Trusted companion policy configuration; never workflow authorization."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from uuid import UUID


@dataclass(frozen=True)
class HttpsPolicy:
    policy_id: str
    org_id: str
    origins: tuple[str, ...]
    max_requests: int

    def envelope(self, execution_id):
        return {'policy_id': self.policy_id, 'org_id': self.org_id,
                'execution_id': execution_id, 'origins': list(self.origins),
                'max_requests': self.max_requests}


def snapshot_policies(policies):
    if policies is None:
        return {}
    if not isinstance(policies, dict) or len(policies) > 16:
        raise ValueError('Bounded trusted HTTPS policies required')
    bound = {}
    for key, value in policies.items():
        if (not isinstance(key, str) or re.fullmatch(r'[a-z][a-z0-9-]{0,63}', key) is None
            or not isinstance(value, dict) or set(value) != {'org_id', 'origins', 'max_requests'}):
            raise ValueError('Explicit trusted HTTPS policy shape required')
        org = value['org_id']
        if not isinstance(org, str) or str(UUID(org)) != org:
            raise ValueError('Canonical HTTPS tenant scope required')
        origins, limit = value['origins'], value['max_requests']
        if (not isinstance(origins, (list, tuple)) or not 1 <= len(origins) <= 16
            or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError('Bounded HTTPS origins and request budget required')
        checked = []
        for origin in origins:
            if not isinstance(origin, str):
                raise ValueError('Exact HTTPS origin required')
            url = urlsplit(origin)
            host = url.hostname or ''
            if (url.scheme != 'https' or url.username or url.password or url.path or url.query or url.fragment
                or not re.fullmatch(r'[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?)+', host)
                or re.fullmatch(r'[\d.]+', host) or origin != 'https://' + host
                or origin in checked):
                raise ValueError('Canonical DNS HTTPS origin required')
            checked.append(origin)
        bound[key] = HttpsPolicy(key, org, tuple(checked), limit)
    return bound
