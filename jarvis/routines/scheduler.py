"""APScheduler-based routine manager: schedules the morning brief and any monitors."""

from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import Config
from .monitors import Monitor, run_monitor
from .morning_brief import morning_brief

Deliver = Callable[[str, str], None]


class RoutineManager:
    def __init__(self, config: Config, deliver: Deliver) -> None:
        self.config = config
        self.deliver = deliver
        self.scheduler = AsyncIOScheduler()

    def add_morning_brief(self, hour: int, minute: int) -> None:
        async def job() -> None:
            text = await morning_brief(self.config)
            if text:
                self.deliver("Morning briefing", text)

        self.scheduler.add_job(
            job, CronTrigger(hour=hour, minute=minute), id="morning_brief", replace_existing=True
        )

    def add_monitor(self, monitor: Monitor) -> None:
        async def job() -> None:
            alert = await run_monitor(self.config, monitor)
            if alert:
                self.deliver(f"Jarvis — {monitor.name}", alert)

        self.scheduler.add_job(
            job,
            IntervalTrigger(minutes=monitor.minutes),
            id=f"monitor:{monitor.name}",
            replace_existing=True,
        )

    def add_daily_rollup(self, hour: int, minute: int) -> None:
        from .daily_rollup import daily_rollup

        async def job() -> None:
            await daily_rollup(self.config)  # writes the summary into the journal

        self.scheduler.add_job(
            job, CronTrigger(hour=hour, minute=minute), id="daily_rollup", replace_existing=True
        )

    def add_reminders_checker(self) -> None:
        from ..jobs import reminders

        def job() -> None:
            for text in reminders.check_now(self.config.vault_path):
                self.deliver("Reminder", text)

        self.scheduler.add_job(
            job, IntervalTrigger(seconds=30), id="reminders", replace_existing=True
        )

    def add_battery_monitor(self, minutes: int = 5) -> None:
        from .monitors import battery_alert

        def job() -> None:
            alert = battery_alert()
            if alert:
                self.deliver("Jarvis — battery", alert)

        self.scheduler.add_job(
            job, IntervalTrigger(minutes=minutes), id="battery", replace_existing=True
        )

    def add_resource_monitor(self, minutes: int = 3) -> None:
        from .monitors import resource_alert

        def job() -> None:
            alert = resource_alert()
            if alert:
                self.deliver("Jarvis — system", alert)

        self.scheduler.add_job(
            job, IntervalTrigger(minutes=minutes), id="resources", replace_existing=True
        )

    def start(self) -> None:
        self.scheduler.start()

    def jobs(self):
        return self.scheduler.get_jobs()
