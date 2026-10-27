from pdfreader.terminal import TerminalPanel


def test_late_events_from_stopped_terminal_do_not_clear_new_process(qapp):
    panel = TerminalPanel()
    sentinel = object()
    panel.generation = 3
    panel.process = sentinel
    panel.receive(2, "old output")
    panel.ended(2, "old exit")
    assert panel.process is sentinel
    assert "old output" not in "\n".join(panel.screen.display)
    panel.receive(3, "current output")
    assert "current output" in "\n".join(panel.screen.display)
    panel.ended(3, "current exit")
    assert panel.process is None
    panel.close()
