"""Windows service inspection (read-only)."""

from __future__ import annotations

import logging

import psutil

from boostai.core.models import ServiceInfo

log = logging.getLogger(__name__)


def collect_services() -> list[ServiceInfo]:
    services: list[ServiceInfo] = []
    try:
        iterator = psutil.win_service_iter()
    except (AttributeError, OSError) as exc:  # non-Windows or SCM unavailable
        log.info("Service enumeration unavailable: %s", exc)
        return services
    for svc in iterator:
        try:
            services.append(_to_info(svc.as_dict()))
        except (psutil.Error, OSError):
            # Some services deny QueryServiceConfig to standard users; fall back to the basics.
            try:
                services.append(ServiceInfo(name=svc.name(), display_name=svc.display_name(), status="unknown", start_type="unknown"))
            except (psutil.Error, OSError):
                continue
    return services


def get_service(name: str) -> ServiceInfo | None:
    try:
        return _to_info(psutil.win_service_get(name).as_dict())
    except (psutil.Error, OSError, AttributeError):
        return None


def _to_info(d: dict) -> ServiceInfo:
    return ServiceInfo(
        name=str(d.get("name", "")),
        display_name=str(d.get("display_name", "")),
        status=str(d.get("status", "unknown")),
        start_type=str(d.get("start_type", "unknown")),
        pid=d.get("pid") or None,
        username=d.get("username") or None,
        binpath=d.get("binpath") or None,
    )
