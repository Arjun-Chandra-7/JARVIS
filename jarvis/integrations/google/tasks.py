"""Google Tasks: list open tasks, add, complete. Returns None if Google isn't connected."""

from __future__ import annotations

from .auth import service


def _default_list(svc) -> str:
    lists = svc.tasklists().list(maxResults=1).execute().get("items", [])
    return lists[0]["id"] if lists else "@default"


def list_tasks(config):
    svc = service(config, "tasks", "v1")
    if svc is None:
        return None
    tasklist = _default_list(svc)
    items = (
        svc.tasks()
        .list(tasklist=tasklist, showCompleted=False, maxResults=100)
        .execute()
        .get("items", [])
    )
    open_items = [t for t in items if t.get("status") != "completed"]
    if not open_items:
        return "No open tasks."
    return "\n".join(f"- {t.get('title', '(untitled)')}" for t in open_items)


def add_task(config, title: str, notes: str = ""):
    svc = service(config, "tasks", "v1")
    if svc is None:
        return None
    tasklist = _default_list(svc)
    svc.tasks().insert(tasklist=tasklist, body={"title": title, "notes": notes}).execute()
    return f"Added task: {title}"


def complete_task(config, title: str):
    svc = service(config, "tasks", "v1")
    if svc is None:
        return None
    tasklist = _default_list(svc)
    items = (
        svc.tasks()
        .list(tasklist=tasklist, showCompleted=False, maxResults=100)
        .execute()
        .get("items", [])
    )
    target = next((t for t in items if title.lower() in t.get("title", "").lower()), None)
    if target is None:
        return f"No open task matching '{title}'."
    svc.tasks().patch(tasklist=tasklist, task=target["id"], body={"status": "completed"}).execute()
    return f"Completed: {target.get('title')}"
