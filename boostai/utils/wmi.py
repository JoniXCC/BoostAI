"""Minimal read-only WMI helper.

Results are copied into plain dicts *inside* the COM apartment so no COM object
outlives ``CoUninitialize`` (which otherwise produces "releasing IUnknown" errors).
"""

from __future__ import annotations

from typing import Any


def _query(namespace: str, wql: str, fields: list[str]) -> list[dict[str, Any]]:
    import win32com.client

    service = win32com.client.GetObject(f"winmgmts:\\\\.\\{namespace}")
    rows = []
    for item in service.ExecQuery(wql):
        rows.append({f: getattr(item, f, None) for f in fields})
        del item
    del service
    return rows


def wmi_query(namespace: str, wql: str, fields: list[str]) -> list[dict[str, Any]]:
    import pythoncom

    pythoncom.CoInitialize()
    try:
        return _query(namespace, wql, fields)
    finally:
        pythoncom.CoUninitialize()


def wmi_call(namespace: str, class_name: str, method: str, *args: Any) -> Any:
    """Invoke a static WMI class method with fixed, caller-validated arguments."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        service = win32com.client.GetObject(f"winmgmts:\\\\.\\{namespace}")
        cls = service.Get(class_name)
        result = getattr(cls, method)(*args)
        del cls, service
        return result
    finally:
        pythoncom.CoUninitialize()
