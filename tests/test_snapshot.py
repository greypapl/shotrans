"""浮窗「导出」调试快照 + tools.replay：不连网络，不需要真的识别和翻译服务。"""
import json
import sys

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QToolButton

from shotrans import snapshot
from shotrans.models import Box, OcrLine, OcrWord, SegmentResult
from shotrans.overlay import OverlayWindow
from tools.replay import load_snapshot, replay

FAKE_KEY = "sk-FAKEKEY-9f3a1c77"


def _image():
    im = Image.new("RGB", (320, 140), "white")
    d = ImageDraw.Draw(im)
    for y in (10, 34, 80):
        d.rectangle([10, y, 200, y + 14], fill=(30, 30, 30))
    d.rectangle([5, 120, 9, 124], fill=(1, 2, 3))  # 一个特征像素：原图必须逐像素一致
    return im


LINES = [OcrLine("Hello world", Box(10, 10, 190, 16)), OcrLine("second line", Box(10, 34, 190, 16)),
         OcrLine("Other para", Box(10, 80, 190, 16))]


class Router:
    api_key = FAKE_KEY  # 路由里放一把假密钥：快照绝不能带出来

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        texts = {1: "你好，世界，这是一段很长很长很长很长很长很长很长很长很长的译文", 2: "第二段"}
        for s in segments:
            on_result(SegmentResult(s.idx, texts[s.idx], "vllm", "引用丢失" if s.idx == 2 else None))


class Tip:
    shown = []

    @staticmethod
    def showText(pos, text, widget=None):
        Tip.shown.append(text)

    @staticmethod
    def hideText():
        pass


@pytest.fixture
def win(qtbot, track, monkeypatch, tmp_path):
    Tip.shown = []
    monkeypatch.setattr("shotrans.overlay.QToolTip", Tip)
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path))
    from shotrans.session import Session
    s = Session(_image())
    w = OverlayWindow(s, QRect(100, 200, 320, 140), lambda im: list(LINES), Router(), None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: len(s.results) == 2, timeout=5000)
    return w


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def _export_button(w):
    return next(b for b in w._bar.findChildren(QToolButton) if b.text() == "导出")


def _folder(tmp_path):
    (folder,) = list((tmp_path / "debug").iterdir())
    return folder


def test_button_has_tooltip(win):
    assert _export_button(win).toolTip() == "导出调试快照：原图、识别结果、译文"


def test_click_writes_three_files(qtbot, win, tmp_path):
    _export_button(win).click()
    qtbot.waitUntil(lambda: any(t.startswith("已导出到 debug/") for t in Tip.shown), timeout=5000)
    folder = _folder(tmp_path)
    assert Tip.shown[-1] == f"已导出到 debug/{folder.name}"
    assert len(folder.name.split("-")) == 3 and folder.name.endswith(win.session.id[:6])
    assert sorted(p.name for p in folder.iterdir()) == ["original.png", "rendered.png", "snapshot.json"]
    # 原图逐像素一致；译文图和当前显示的译文视图一致，且确实不同于原图
    assert Image.open(folder / "original.png").tobytes() == win.session.image.tobytes()
    assert Image.open(folder / "rendered.png").tobytes() == win.session._rendered().image.tobytes()
    assert Image.open(folder / "rendered.png").tobytes() != win.session.image.tobytes()


def test_exports_translated_view_even_when_showing_original(qtbot, win, tmp_path):
    win.toggle_view()
    _export_button(win).click()
    qtbot.waitUntil(lambda: bool(Tip.shown), timeout=5000)
    rendered = Image.open(_folder(tmp_path) / "rendered.png")
    assert rendered.tobytes() == win.session._rendered().image.tobytes()


def test_snapshot_content_round_trip(qtbot, win, tmp_path):
    _export_button(win).click()
    qtbot.waitUntil(lambda: bool(Tip.shown), timeout=5000)
    folder = _folder(tmp_path)
    raw = (folder / "snapshot.json").read_text(encoding="utf-8")
    assert "你好，世界" in raw  # ensure_ascii=False
    assert raw.startswith("{\n \"")  # indent=1
    snap = load_snapshot(folder)
    s = win.session
    assert snap["image_size"] == [320, 140]
    assert (snap["mode"], snap["direction"], snap["target"]) == ("para", "en2zh", "zh")
    assert snap["ocr_lines"] == [{"text": ln.text, "box": [ln.box.x, ln.box.y, ln.box.w, ln.box.h], "words": []}
                                 for ln in LINES]  # 手写的行没有词级框
    assert [(g["idx"], g["text"], g["skip"], g["box"]) for g in snap["segments"]] == \
        [(g.idx, g.text, g.skip, [g.box.x, g.box.y, g.box.w, g.box.h]) for g in s.segments]
    assert snap["segments"][0]["lines"][1]["text"] == "second line"
    assert [(r["idx"], r["text"], r["service"], r["note"]) for r in snap["results"]] == \
        [(r.idx, r.text, r.service, r.note) for _, r in sorted(s.results.items())]
    assert snap["results"][1]["note"] == "引用丢失"
    assert snap["service_status"] == "vllm"
    assert [p["idx"] for p in snap["placements"]] == [1]  # 第 2 段带 note（原文，不画译文）
    assert snap["placements"][0]["box"] == [10, 10, 190, 40]
    assert snap["render_constants"] == {"px_per_line_h": 0.95, "min_px": 9, "line_spacing": 1.25}
    assert "git" in snap["app"] and snap["time"]
    assert snap["schema_version"] == 3
    assert snap["generation"] == s.generation and snap["generation"] >= 1
    assert snap["pending"] is False


def test_no_key_in_snapshot(qtbot, win, tmp_path):
    _export_button(win).click()
    qtbot.waitUntil(lambda: bool(Tip.shown), timeout=5000)
    raw = (_folder(tmp_path) / "snapshot.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in raw
    # 不靠子串黑名单猜 "config"：快照里所有键名都不像密钥，值里也没有那把假密钥的任何片段
    assert not [k for k in _all_keys(json.loads(raw)) if any(w in k.lower() for w in ("key", "token", "secret", "password"))]
    assert "FAKEKEY" not in raw and "9f3a1c77" not in raw


def test_export_failure_toasts_and_does_not_raise(qtbot, win, monkeypatch, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("我是个文件，不是文件夹")
    monkeypatch.setenv("SHOTRANS_HOME", str(blocker))  # 在文件下面建文件夹：必定失败
    _export_button(win).click()
    qtbot.waitUntil(lambda: any(t.startswith("导出失败：") for t in Tip.shown), timeout=5000)
    assert win.isVisible()


def test_build_failure_toasts_and_does_not_raise(win, monkeypatch):
    def boom(_s):
        raise RuntimeError("坏了")

    monkeypatch.setattr("shotrans.overlay.build_snapshot", boom)
    win.export_snapshot()
    assert Tip.shown[-1] == "导出失败：坏了"


def test_replay_reproduces_saved_placements(qtbot, win, tmp_path):
    _export_button(win).click()
    qtbot.waitUntil(lambda: bool(Tip.shown), timeout=5000)
    folder = _folder(tmp_path)
    got = replay(folder)
    saved = win.session._rendered()
    assert sorted(got.placements) == sorted(saved.placements) and got.placements
    for idx, p in saved.placements.items():
        assert got.placements[idx] == p
    assert got.image.tobytes() == saved.image.tobytes()


def test_replay_cli_writes_png_and_prints_summary(qtbot, win, tmp_path, capsys):
    _export_button(win).click()
    qtbot.waitUntil(lambda: bool(Tip.shown), timeout=5000)
    folder = _folder(tmp_path)
    from tools.replay import main
    assert main([str(folder)]) == 0
    assert (folder / "replay.png").exists()
    out = capsys.readouterr().out
    assert "段1: 字号=" in out and "截断=" in out and "框=10,10,190,40" in out
    assert json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))["segments"]
    assert "与快照：same" in out and "警告 0 条" in out


# ---------- 原始识别行 / 重放用当前分组 / 按原文匹配译文（不需要 Qt） ----------
def _session(lines=None, results=True):
    from shotrans.session import Session
    s = Session(_image())
    s.set_lines(list(LINES if lines is None else lines))
    if results:
        s.apply(s.generation, SegmentResult(1, "你好，世界，这是一段很长很长很长很长很长很长很长很长很长的译文", "vllm"))
        s.apply(s.generation, SegmentResult(2, "第二段", "vllm"))
    return s


def _written(tmp_path, session=None):
    s = session or _session()
    return snapshot.write_snapshot(snapshot.build_snapshot(s, now=1_700_000_000.123), tmp_path / "out")


def _tamper(folder, fn):
    p = folder / "snapshot.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fn(d)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_snapshot_stores_raw_ocr_lines_not_merged_rows():
    frags = [OcrLine("Hello", Box(10, 10, 40, 16)), OcrLine("world", Box(55, 10, 50, 16))]  # 同一行的两个碎片
    d = snapshot.build_snapshot(_session(frags, results=False), now=1_700_000_000).data
    assert d["ocr_lines"] == [{"text": "Hello", "box": [10, 10, 40, 16], "words": []},
                              {"text": "world", "box": [55, 10, 50, 16], "words": []}]
    assert d["segments"][0]["lines"] == [{"text": "Hello world", "box": [10, 10, 95, 16]}]


def test_stamp_has_milliseconds_and_schema_and_pending():
    s = _session(results=False)
    snap = snapshot.build_snapshot(s, now=1_700_000_000.123)
    assert snap.stamp.endswith(f"123-{s.id[:6]}") and snap.stamp.count("-") == 2
    assert snap.data["schema_version"] == 3 and snap.data["pending"] is True and snap.data["generation"] == 1
    assert snapshot.build_snapshot(_session(), now=1.0).data["pending"] is False


def test_pending_ignores_skipped_formula_segments():
    s = _session([OcrLine("E = mc^2", Box(10, 10, 150, 20))], results=False)
    assert s.segments[0].skip
    assert snapshot.build_snapshot(s, now=1.0).data["pending"] is False


class _Run:
    def __init__(self, outs):
        self.outs, self.calls = outs, []

    def __call__(self, cmd, **kw):
        import subprocess
        self.calls.append((cmd, kw))
        code, out = self.outs[cmd[1]]
        return subprocess.CompletedProcess(cmd, code, out, "")


def test_git_subprocess_flags_and_dirty_marker(monkeypatch):
    import subprocess
    run = _Run({"rev-parse": (0, "abc1234\n"), "status": (0, " M shotrans/x.py\n")})
    monkeypatch.setattr(snapshot.subprocess, "run", run)
    assert snapshot.git_short_sha() == "abc1234+dirty"
    assert [c[0][1] for c in run.calls] == ["rev-parse", "status"] and "--porcelain" in run.calls[1][0]
    for _, kw in run.calls:
        assert kw["stdin"] == subprocess.DEVNULL and kw["creationflags"] == 0x08000000 and kw["timeout"] == 2
    monkeypatch.setattr(snapshot.subprocess, "run", _Run({"rev-parse": (0, "abc1234\n"), "status": (0, "")}))
    assert snapshot.git_short_sha() == "abc1234"
    monkeypatch.setattr(snapshot.subprocess, "run", _Run({"rev-parse": (128, ""), "status": (0, "")}))
    assert snapshot.git_short_sha() is None


def test_double_click_export_writes_one_folder(qtbot, win, monkeypatch, tmp_path):
    import threading
    release, calls = threading.Event(), []
    real = snapshot.write_snapshot

    def slow(snap):
        calls.append(snap.stamp)
        release.wait(5)
        return real(snap)

    monkeypatch.setattr("shotrans.overlay.write_snapshot", slow)
    _export_button(win).click()
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)
    _export_button(win).click()
    assert Tip.shown[-1] == "正在导出…"
    release.set()
    qtbot.waitUntil(lambda: any(t.startswith("已导出到") for t in Tip.shown), timeout=5000)
    assert len(calls) == 1 and len(list((tmp_path / "debug").iterdir())) == 1
    _export_button(win).click()  # 完成后可以再导出
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=3000)


def test_export_guard_released_after_failure(qtbot, win, monkeypatch):
    def boom(snap):
        raise OSError("磁盘满")

    monkeypatch.setattr("shotrans.overlay.write_snapshot", boom)
    _export_button(win).click()
    qtbot.waitUntil(lambda: "导出失败：磁盘满" in Tip.shown, timeout=3000)
    _export_button(win).click()
    qtbot.waitUntil(lambda: Tip.shown.count("导出失败：磁盘满") == 2, timeout=3000)


def test_replay_same_grouping_gives_identical_placements(tmp_path):
    from tools.replay import replay_report
    s = _session()
    folder = _written(tmp_path, s)
    rep = replay_report(folder)
    saved = s._rendered()
    assert rep.warnings == [] and [(g.idx, g.status, g.how) for g in rep.segs] == [(1, "same", "text"), (2, "same", "text")]
    assert rep.result.placements == saved.placements and rep.result.image.tobytes() == saved.image.tobytes()
    assert rep.result.placements[1].box == Box(10, 10, 190, 40)


def test_replay_uses_current_group_lines_on_raw_ocr_lines(tmp_path, monkeypatch):
    import tools.replay as R
    folder = _written(tmp_path)
    seen = []
    real = R.group_lines

    def spy(lines, mode, image=None):  # Task 19：重放把截图也传给分组（放宽合并要看间隙）
        seen.append(([ln.text for ln in lines], mode))
        return real(lines, mode, image)

    monkeypatch.setattr(R, "group_lines", spy)
    R.replay(folder)
    assert seen == [(["Hello world", "second line", "Other para"], "para")]


def test_replay_changed_saved_mode_changes_output(tmp_path):
    from tools.replay import replay_report
    folder = _written(tmp_path)
    before = replay_report(folder)
    _tamper(folder, lambda d: d.__setitem__("mode", "line"))
    after = replay_report(folder)
    assert len(before.segs) == 2 and len(after.segs) == 3
    assert after.result.image.tobytes() != before.result.image.tobytes()


def test_replay_ignores_tampered_saved_segments_except_report(tmp_path):
    from tools.replay import replay_report
    folder = _written(tmp_path)
    base = replay_report(folder)

    def tamper(d):
        for sg in d["segments"]:  # 文字和编号不动，框和行全改掉
            sg["box"] = [0, 0, 5, 5]
            sg["lines"] = [{"text": "垃圾", "box": [1, 1, 1, 1]}]
        d["segments"].reverse()

    _tamper(folder, tamper)
    rep = replay_report(folder)
    assert rep.result.placements == base.result.placements
    assert rep.result.image.tobytes() == base.result.image.tobytes()
    assert [g.status for g in base.segs] == ["same", "same"] and [g.status for g in rep.segs] == ["changed", "changed"]


def test_replay_reordered_segments_follow_text_not_old_idx(tmp_path, monkeypatch):
    import tools.replay as R
    folder = _written(tmp_path)
    real = R.group_lines

    def swapped(lines, mode, image=None):
        a, b = real(lines, mode, image)
        a.idx, b.idx = 2, 1  # 编号对调
        return [b, a]

    monkeypatch.setattr(R, "group_lines", swapped)
    rep = R.replay_report(folder)
    assert rep.warnings == []
    p = rep.result.placements
    assert p[1].box == Box(10, 80, 190, 16) and "".join(p[1].lines) == "第二段"
    assert p[2].box == Box(10, 10, 190, 40) and "你好" in "".join(p[2].lines)


def test_replay_merged_segments_warn_and_apply_no_wrong_translation(tmp_path, monkeypatch, capsys):
    import tools.replay as R
    from shotrans.models import Segment
    folder = _written(tmp_path)
    monkeypatch.setattr(R, "group_lines", lambda lines, mode, image=None: [Segment(1, "Hello world Other para", list(lines))])
    assert R.main([str(folder)]) == 0
    out = capsys.readouterr().out
    assert "分段与快照不同：新段 1「Hello world Other para」无对应译文" in out
    assert "段1: 无译文排版 与快照：changed" in out
    assert R.replay(folder).placements == {}


def test_replay_split_reports_each_unmatched_half(tmp_path, monkeypatch, capsys):
    import tools.replay as R
    folder = _written(tmp_path)
    real = R.group_lines
    monkeypatch.setattr(R, "group_lines", lambda lines, mode, image=None: real(lines, "line", image))
    rep = R.replay_report(folder)
    assert rep.warnings == ["分段与快照不同：新段 1「Hello world」无对应译文", "分段与快照不同：新段 2「second line」无对应译文"]
    assert [(g.idx, g.status) for g in rep.segs] == [(1, "changed"), (2, "changed"), (3, "same")]
    assert list(rep.result.placements) == [3] and "".join(rep.result.placements[3].lines) == "第二段"
    assert R.main([str(folder)]) == 0
    out = capsys.readouterr().out
    assert out.count("分段与快照不同：") == 2 and "共 3 段，与快照相同 1 段，警告 2 条" in out


def test_replay_matches_by_box_overlap_when_text_differs(tmp_path, monkeypatch):
    import tools.replay as R
    folder = _written(tmp_path)
    real = R.group_lines

    def reworded(lines, mode, image=None):
        segs = real(lines, mode, image)
        segs[0].text = "Hello wor1d second line"  # 文字差一个字（相似度 0.96），框没变
        return segs

    monkeypatch.setattr(R, "group_lines", reworded)
    rep = R.replay_report(folder)
    assert (rep.segs[0].how, rep.segs[0].status) == ("iou", "changed")
    # Task 19 修正：按位置配上的一律警告
    assert rep.warnings == ["按位置匹配，原文有差异：新段 1「Hello wor1d second line」套用快照段 1 的译文（原文相似度 0.96）"]
    assert "你好" in "".join(rep.result.placements[1].lines)


def test_replay_overlap_below_half_is_not_a_match(tmp_path, monkeypatch):
    import tools.replay as R
    from shotrans.models import Segment
    folder = _written(tmp_path)
    # 新段只有快照第 1 段（190x40）上半截的一行（190x16）：IoU = 0.4 < 0.5，文字也对不上
    monkeypatch.setattr(R, "group_lines", lambda lines, mode, image=None: [Segment(1, "totally other", [lines[0]])])
    rep = R.replay_report(folder)
    assert len(rep.warnings) == 1 and rep.result.placements == {}


def test_replay_reports_changed_when_only_placement_differs(tmp_path):
    from tools.replay import replay_report
    folder = _written(tmp_path)
    _tamper(folder, lambda d: d["placements"][0].__setitem__("px", 99))  # 分组没变，字号和快照不同
    rep = replay_report(folder)
    assert [(g.idx, g.status) for g in rep.segs] == [(1, "changed"), (2, "same")] and rep.warnings == []


# ---------- Task 19 评审修正：按位置配要原文够像、版本校验、颜色也比 ----------

def test_replay_60_40_split_does_not_borrow_whole_translation(tmp_path, monkeypatch):
    # 快照第 1 段（两行，190x40）现在被拆成 60/40：前一半框是 190x24，IoU = 0.6 够了，
    # 但原文「Hello world sec」和整段「Hello world second line」只有 0.65 像——不能把整段的旧译文贴到前一半上
    import tools.replay as R
    from shotrans.models import Segment
    folder = _written(tmp_path)
    monkeypatch.setattr(R, "group_lines", lambda lines, mode, image=None: [
        Segment(1, "Hello world sec", [OcrLine("Hello world sec", Box(10, 10, 190, 24))]),
        Segment(2, "ond line", [OcrLine("ond line", Box(10, 34, 190, 16))]),
        Segment(3, "Other para", [lines[2]])])
    rep = R.replay_report(folder)
    assert 1 not in rep.result.placements and 2 not in rep.result.placements
    assert rep.segs[0].how == "none"
    assert rep.warnings == [
        "分段与快照不同：新段 1「Hello world sec」无对应译文（按位置能对上快照段 1，但原文相似度 0.65 < 0.8，不套用它的译文）",
        "分段与快照不同：新段 2「ond line」无对应译文"]


def _match_one(new_text, saved_text):
    from shotrans.models import Segment
    from tools.replay import _match
    seg = Segment(1, new_text, [OcrLine(new_text, Box(0, 0, 100, 20))])
    warnings = []
    got = _match([seg], [{"idx": 7, "box": [0, 0, 100, 20], "text": saved_text}], warnings)
    return got[1], warnings


def test_replay_iou_match_similarity_boundary():
    # 50 个字差 10 个 = 0.8 还算像；差 11 个 = 0.78 不算
    assert _match_one("a" * 50, "a" * 40 + "b" * 10)[0] == (7, "iou")
    assert _match_one("a" * 50, "a" * 39 + "b" * 11)[0] == (None, "none")


def test_replay_similarity_is_edit_distance_over_longer_length():
    from tools.replay import similarity
    assert similarity("kitten", "sitting") == pytest.approx(1 - 3 / 7)
    assert similarity("a  b", "a b") == 1.0 and similarity("", "") == 1.0


def test_replay_rejects_unknown_schema_version(tmp_path, capsys):
    import tools.replay as R
    folder = _written(tmp_path)
    _tamper(folder, lambda d: d.__setitem__("schema_version", 4))
    with pytest.raises(R.SnapshotError, match="快照格式版本 4 不认得"):
        R.replay_report(folder)
    assert R.main([str(folder)]) == 2
    assert "快照格式版本 4 不认得（本工具只认 1, 2, 3）" in capsys.readouterr().out


# ---------- Task 20：词级框 ----------

WORDY = [OcrLine("Hello world", Box(10, 10, 95, 16),
                 (OcrWord("Hello", Box(10, 10, 40, 16)), OcrWord("world", Box(55, 10, 50, 16))))]


def test_snapshot_v2_stores_word_boxes():
    d = snapshot.build_snapshot(_session(WORDY, results=False), now=1.0).data
    assert d["ocr_lines"] == [{"text": "Hello world", "box": [10, 10, 95, 16],
                               "words": [{"text": "Hello", "box": [10, 10, 40, 16]},
                                         {"text": "world", "box": [55, 10, 50, 16]}]}]


def test_snapshot_segments_list_their_pills():
    from shotrans.models import Pill
    s = _session(results=False)
    s.segments[0].pills = (Pill("world", Box(50, 8, 60, 20)),)
    d = snapshot.build_snapshot(s, now=1.0).data
    assert d["segments"][0]["pills"] == [{"text": "world", "box": [50, 8, 60, 20]}]
    assert d["segments"][1]["pills"] == []


def test_replay_reads_word_boxes(tmp_path, monkeypatch):
    import tools.replay as R
    folder = _written(tmp_path, _session(WORDY, results=False))
    seen = []
    real = R.group_lines

    def spy(lines, mode, image=None):
        seen.append([[(w.text, w.box) for w in ln.words] for ln in lines])
        return real(lines, mode, image)

    monkeypatch.setattr(R, "group_lines", spy)
    R.replay(folder)
    assert seen == [[[("Hello", Box(10, 10, 40, 16)), ("world", Box(55, 10, 50, 16))]]]


def test_replay_accepts_version_1_without_words(tmp_path):
    # 版本 1 的快照识别行没有 words：照常重放（只是认不出行内代码胶囊）
    from tools.replay import replay_report

    def to_v1(d):
        d["schema_version"] = 1
        for ln in d["ocr_lines"]:
            ln.pop("words")

    folder = _written(tmp_path)
    _tamper(folder, to_v1)
    rep = replay_report(folder)
    assert [g.status for g in rep.segs] == ["same", "same"] and rep.warnings == []


def test_replay_accepts_legacy_snapshot_without_version(tmp_path):
    # 加版本号之前导出的快照（10-12 用户导出的两份就是）没有 schema_version，字段与版本 1 相同：照常重放
    from tools.replay import replay_report
    folder = _written(tmp_path)
    _tamper(folder, lambda d: d.pop("schema_version"))
    rep = replay_report(folder)
    assert [g.status for g in rep.segs] == ["same", "same"] and rep.warnings == []


@pytest.mark.parametrize("version", [None, 3])
def test_replay_rejects_snapshot_missing_fields(tmp_path, version):
    import tools.replay as R
    folder = _written(tmp_path)

    def tamper(d):
        d.pop("placements")
        if version is None:
            d.pop("schema_version")

    _tamper(folder, tamper)
    with pytest.raises(R.SnapshotError, match=f"快照缺少字段 placements（格式版本 {version}）"):
        R.replay_report(folder)


@pytest.mark.parametrize("key", ["fg", "bg"])
def test_replay_reports_changed_when_only_colour_differs(tmp_path, key):
    from tools.replay import replay_report
    folder = _written(tmp_path)
    _tamper(folder, lambda d: d["placements"][0].__setitem__(key, [1, 2, 3]))
    rep = replay_report(folder)
    assert [(g.idx, g.status) for g in rep.segs] == [(1, "changed"), (2, "same")]


# ---------- 终审：快照存 area，重放比 area ----------

def test_snapshot_saves_area_of_each_placement(tmp_path):
    s = _session()
    folder = _written(tmp_path, s)
    saved = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))["placements"][0]
    a = s._rendered().placements[1].area
    assert saved["area"] == [a.x, a.y, a.w, a.h] and a.w > 1 and a.h > 1


def test_replay_reports_changed_when_only_area_differs(tmp_path):
    from tools.replay import replay_report
    folder = _written(tmp_path)
    _tamper(folder, lambda d: d["placements"][0].__setitem__("area", [10, 10, 7, 7]))
    rep = replay_report(folder)
    assert [(g.idx, g.status) for g in rep.segs] == [(1, "changed"), (2, "same")] and rep.warnings == []


def test_replay_old_snapshot_without_area_still_same(tmp_path):
    from tools.replay import replay_report
    folder = _written(tmp_path)
    _tamper(folder, lambda d: [p.pop("area") for p in d["placements"]])
    rep = replay_report(folder)
    assert [(g.idx, g.status) for g in rep.segs] == [(1, "same"), (2, "same")] and rep.warnings == []


def test_write_snapshot_records_version_and_skips_git_when_frozen(monkeypatch, tmp_path):
    import shotrans
    called = []
    monkeypatch.setattr(snapshot, "_git", lambda *a: called.append(a))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    snap = snapshot.Snapshot(Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4)),
                             {"app": {"git": None, "python": "3.12"}}, "stamp")
    folder = snapshot.write_snapshot(snap, tmp_path)
    data = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
    assert data["app"]["version"] == shotrans.__version__
    assert data["app"]["git"] is None
    assert called == []


def test_write_snapshot_source_still_records_git(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(snapshot, "git_short_sha", lambda: "abc1234")
    snap = snapshot.Snapshot(Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4)),
                             {"app": {"git": None, "python": "3.12"}}, "stamp2")
    data = json.loads((snapshot.write_snapshot(snap, tmp_path) / "snapshot.json").read_text(encoding="utf-8"))
    assert data["app"]["git"] == "abc1234"