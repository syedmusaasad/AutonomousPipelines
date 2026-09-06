"""Unit tests for pipeline.tui.conversation: the read-only sqlite reader over the
devpass-code DB (session/message/part), chunked backlog load, streamed polling by
time_updated, and the read-only Composer. Registered into the main suite by
tests/test_suite.py."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.harness import Estate
from pipeline.tui import conversation as conv


def _make_db(path: Path):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE session (id text PRIMARY KEY)")
    c.execute("""CREATE TABLE message (id text PRIMARY KEY, session_id text,
                 time_created integer, time_updated integer, data text)""")
    c.execute("""CREATE TABLE part (id text PRIMARY KEY, message_id text, session_id text,
                 time_created integer, time_updated integer, data text)""")
    return c


def _add_message(c, mid, sid, role, t):
    c.execute("INSERT INTO message VALUES (?,?,?,?,?)", (mid, sid, t, t, json.dumps({"role": role})))


def _add_part(c, pid, mid, sid, t, data, tu=None):
    c.execute("INSERT INTO part VALUES (?,?,?,?,?,?)", (pid, mid, sid, t, tu if tu is not None else t, json.dumps(data)))


class TestOpenDb(unittest.TestCase):

    def test_missing_file_returns_none_without_raising(self):
        self.assertIsNone(conv.open_db(Path("/no/such/db/here.db")))

    def test_existing_file_opens_readonly(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.commit()
            c.close()
            conn = conv.open_db(db)
            self.assertIsNotNone(conn)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO session VALUES ('s1')")
            conn.close()


class TestRenderPartLines(unittest.TestCase):

    def test_tool_part_collapses_to_one_line(self):
        lines = conv.render_part_lines("tool", {"tool": "bash", "state": {"status": "completed", "input": {"command": "ls -la"}}})
        self.assertEqual(len(lines), 1)
        self.assertIn("bash", lines[0])
        self.assertIn("ls -la", lines[0])

    def test_reasoning_part_collapses_to_one_line(self):
        lines = conv.render_part_lines("reasoning", {"text": "thinking hard about this problem in great detail"})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("  (thinking:"))

    def test_text_part_renders_verbatim_multiline(self):
        lines = conv.render_part_lines("text", {"text": "line one\nline two"})
        self.assertEqual(lines, ["line one", "line two"])

    def test_empty_text_part_yields_one_blank_line(self):
        lines = conv.render_part_lines("text", {"text": ""})
        self.assertEqual(lines, [""])

    def test_silent_types_yield_nothing(self):
        for t in conv.SILENT_TYPES:
            self.assertEqual(conv.render_part_lines(t, {}), [])

    def test_unknown_type_yields_nothing(self):
        self.assertEqual(conv.render_part_lines("something-new", {"x": 1}), [])


class TestConversationStateLifecycle(unittest.TestCase):

    def test_absent_db_file(self):
        cs = conv.ConversationState("s1", Path("/no/such.db"))
        cs.open()
        self.assertEqual(cs.status, conv.STATUS_ABSENT)
        self.assertIn("no conversation database", cs.banner())

    def test_absent_session_in_existing_db(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.commit()
            c.close()
            cs = conv.ConversationState("nope", db)
            cs.open()
            self.assertEqual(cs.status, conv.STATUS_ABSENT)

    def test_empty_session_has_zero_parts(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.execute("INSERT INTO session VALUES ('s1')")
            c.commit()
            c.close()
            cs = conv.ConversationState("s1", db)
            cs.open()
            self.assertEqual(cs.status, conv.STATUS_EMPTY)
            self.assertIn("no messages yet", cs.banner())

    def test_session_with_parts_starts_loading(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.execute("INSERT INTO session VALUES ('s1')")
            _add_message(c, "m1", "s1", "user", 1)
            _add_part(c, "p1", "m1", "s1", 1, {"type": "text", "text": "hi"})
            c.commit()
            c.close()
            cs = conv.ConversationState("s1", db)
            cs.open()
            self.assertEqual(cs.status, conv.STATUS_LOADING)
            self.assertEqual(cs.total_count, 1)


class TestChunkedLoad(unittest.TestCase):

    def _seed(self, n=1200):
        db = self.E.tmp / "x.db"
        c = _make_db(db)
        c.execute("INSERT INTO session VALUES ('s1')")
        for i in range(n):
            mid = f"m{i}"
            _add_message(c, mid, "s1", "user" if i % 2 == 0 else "assistant", i)
            _add_part(c, f"p{i}", mid, "s1", i, {"type": "text", "text": f"line{i}"})
        c.commit()
        c.close()
        return db, n

    def setUp(self):
        self._estate_cm = Estate()
        self.E = self._estate_cm.__enter__()

    def tearDown(self):
        self._estate_cm.__exit__(None, None, None)

    def test_load_more_returns_true_while_backlog_remains(self):
        db, n = self._seed(1200)
        cs = conv.ConversationState("s1", db, chunk_size=500)
        cs.open()
        self.assertTrue(cs.load_more())  # 500/1200 loaded
        self.assertEqual(cs.status, conv.STATUS_LOADING)
        self.assertTrue(cs.load_more())  # 1000/1200 loaded
        self.assertFalse(cs.load_more())  # 1200/1200 loaded -> done
        self.assertEqual(cs.status, conv.STATUS_READY)
        self.assertEqual(cs.loaded_count, n)
        cs.close()

    def test_progress_text_shows_counts_while_loading_then_empty(self):
        db, n = self._seed(1200)
        cs = conv.ConversationState("s1", db, chunk_size=500)
        cs.open()
        cs.load_more()
        self.assertIn("500/1200", cs.progress_text())
        while cs.load_more():
            pass
        self.assertEqual(cs.progress_text(), "")
        cs.close()

    def test_never_reads_the_whole_backlog_in_one_call(self):
        db, n = self._seed(1200)
        cs = conv.ConversationState("s1", db, chunk_size=500)
        cs.open()
        cs.load_more()
        self.assertLess(cs.loaded_count, n)
        cs.close()

    def test_full_replay_reconstructs_turn_order_and_role_headers(self):
        db, n = self._seed(30)
        cs = conv.ConversationState("s1", db, chunk_size=7)
        cs.open()
        while cs.load_more():
            pass
        lines = cs.lines()
        self.assertIn("\u00bb you", lines)
        self.assertIn("\u00ab assistant", lines)
        self.assertIn("line0", lines)
        self.assertIn(f"line{n - 1}", lines)
        cs.close()


class TestPoll(unittest.TestCase):

    def test_poll_only_active_once_ready(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.execute("INSERT INTO session VALUES ('s1')")
            _add_message(c, "m1", "s1", "user", 1)
            _add_part(c, "p1", "m1", "s1", 1, {"type": "text", "text": "hi"}, tu=1)
            c.commit()
            c.close()
            cs = conv.ConversationState("s1", db)
            cs.open()
            cs.poll()  # still loading: no-op
            self.assertEqual(cs.lines(), [])
            while cs.load_more():
                pass
            self.assertEqual(cs.status, conv.STATUS_READY)

    def test_poll_picks_up_new_rows_after_time_updated(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.execute("INSERT INTO session VALUES ('s1')")
            _add_message(c, "m1", "s1", "user", 1)
            _add_part(c, "p1", "m1", "s1", 1, {"type": "text", "text": "first"}, tu=1)
            c.commit()
            cs = conv.ConversationState("s1", db)
            cs.open()
            while cs.load_more():
                pass
            self.assertIn("first", cs.lines())
            # a streamed second part arrives, with a later time_updated
            _add_message(c, "m2", "s1", "assistant", 2)
            _add_part(c, "p2", "m2", "s1", 2, {"type": "text", "text": "second"}, tu=2)
            c.commit()
            c.close()
            cs.poll()
            self.assertIn("second", cs.lines())

    def test_poll_updates_a_growing_streamed_part_in_place(self):
        with Estate() as E:
            db = E.tmp / "x.db"
            c = _make_db(db)
            c.execute("INSERT INTO session VALUES ('s1')")
            _add_message(c, "m1", "s1", "assistant", 1)
            _add_part(c, "p1", "m1", "s1", 1, {"type": "text", "text": "partial"}, tu=1)
            c.commit()
            cs = conv.ConversationState("s1", db)
            cs.open()
            while cs.load_more():
                pass
            self.assertIn("partial", cs.lines())
            self.assertNotIn("partial and more", cs.lines())
            c.execute("UPDATE part SET data = ?, time_updated = ? WHERE id = 'p1'",
                      (json.dumps({"type": "text", "text": "partial and more"}), 2))
            c.commit()
            c.close()
            cs.poll()
            self.assertEqual(len(cs.turns), 1)  # same turn, not a new one
            self.assertIn("partial and more", cs.lines())


class TestComposer(unittest.TestCase):

    def test_no_writer_hint_is_read_only(self):
        c = conv.Composer()
        self.assertFalse(c.has_writer())
        self.assertIn("read-only", c.hint())

    def test_typing_is_captured_even_without_a_writer(self):
        c = conv.Composer()
        c.type_char("h")
        c.type_char("i")
        self.assertEqual(c.buffer, "hi")

    def test_backspace_removes_last_char(self):
        c = conv.Composer()
        c.type_char("h")
        c.type_char("i")
        c.backspace()
        self.assertEqual(c.buffer, "h")

    def test_backspace_on_empty_buffer_is_a_noop(self):
        c = conv.Composer()
        c.backspace()
        self.assertEqual(c.buffer, "")

    def test_submit_without_writer_returns_none_and_keeps_buffer(self):
        c = conv.Composer()
        c.type_char("x")
        result = c.submit()
        self.assertIsNone(result)
        self.assertEqual(c.buffer, "x")
        self.assertEqual(c.sent_marker, 0)

    def test_submit_with_writer_sends_clears_buffer_and_bumps_marker(self):
        sent = []

        class FakeWriter:
            def send(self, text):
                sent.append(text)

        c = conv.Composer(writer=FakeWriter())
        c.type_char("h")
        c.type_char("i")
        result = c.submit()
        self.assertEqual(result, "hi")
        self.assertEqual(sent, ["hi"])
        self.assertEqual(c.buffer, "")
        self.assertEqual(c.sent_marker, 1)
        self.assertIn("Enter", c.hint())

    def test_submit_empty_buffer_with_writer_sends_nothing(self):
        class FakeWriter:
            def send(self, text):
                raise AssertionError("should not be called for empty buffer")

        c = conv.Composer(writer=FakeWriter())
        result = c.submit()
        self.assertIsNone(result)
        self.assertEqual(c.sent_marker, 0)

    def test_clear_wipes_buffer(self):
        c = conv.Composer()
        c.type_char("x")
        c.clear()
        self.assertEqual(c.buffer, "")


if __name__ == "__main__":
    unittest.main()
