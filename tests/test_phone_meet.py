"""Focused unit tests for phone mirror diagnostics and Meet state helpers.

These tests do not start scrcpy, ADB, Chrome, or join a meeting.
"""

import asyncio
import unittest
from unittest.mock import Mock, patch

from jarvis.integrations import apps, meet_bot


class PhoneAndMeetTests(unittest.TestCase):
    def test_phone_mirror_reports_adb_server_failure(self):
        with patch.object(apps.shutil, "which", return_value="/usr/bin/tool"), patch.object(
            apps, "adb_status", return_value="server-error"
        ):
            ok, message = apps.phone_mirror()
        self.assertFalse(ok)
        self.assertIn("ADB could not start", message)

    def test_phone_mirror_launches_only_after_ready_transport(self):
        with patch.object(apps.shutil, "which", return_value="/usr/bin/tool"), patch.object(
            apps, "adb_status", return_value="ready"
        ), patch.object(apps, "_spawn") as spawn:
            self.assertEqual(apps.phone_mirror(), (True, "Phone is on screen."))
        spawn.assert_called_once_with(["scrcpy", "--window-title=JARVIS Phone", "--always-on-top", "--stay-awake"])

    def test_blank_meet_url_uses_default_without_browser_detection(self):
        def fake_create_task(coro):
            # The public method normally starts a background recorder. Close its
            # coroutine here so this unit test never starts a browser or thread.
            coro.close()
            task = Mock()
            task.done.return_value = True
            return task

        async def run():
            with patch.object(meet_bot, "_notes_file", return_value=meet_bot.Path("/tmp/test-meet-notes.txt")), patch.object(
                meet_bot, "_detect_active_meet", side_effect=AssertionError("must not detect")
            ), patch.object(meet_bot.asyncio, "create_task", side_effect=fake_create_task):
                result = await meet_bot.join_meet("", "soon")
            return result

        self.assertEqual(asyncio.run(run()), "/tmp/test-meet-notes.txt")
        self.assertEqual(meet_bot.status()["url"], meet_bot.DEFAULT_MEET_URL)
        meet_bot._bot_task = None

    def test_admission_requires_an_in_call_control(self):
        page = Mock()
        page.locator.return_value.first.is_visible.return_value = False
        self.assertEqual(meet_bot._admission_state(page)[0], "unknown")

    def test_admission_detects_leave_call_control(self):
        page = Mock()

        def visible(selector):
            item = Mock()
            item.first.is_visible.return_value = "Leave call" in selector
            return item

        page.locator.side_effect = visible
        self.assertEqual(meet_bot._admission_state(page), ("admitted", "In call"))

    def test_meet_bot_has_no_chat_sender(self):
        self.assertFalse(hasattr(meet_bot, "_send_chat_message"))
