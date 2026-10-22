"""Task 22：调试快照保存胶囊位置，tools.replay 重放时贴回胶囊；旧快照（没存位置）重放时重算。期望值写死。"""
import json

from shotrans import snapshot
from shotrans.models import NOTE_UNPROTECTED, Box, Pill, SegmentResult
from shotrans.session import Session
from tests.test_pills import SPEC, scene
from tools.replay import guess_pill_spans, remap_pill_spans, replay_report

TR = "按 Ctrl+Alt+Q 开始"


def _session(note=None, spans=((2, 12, 0),), text=TR):
    img, ln = scene(SPEC, pill=(95, 36, 195, 57))
    s = Session(img)
    s.set_lines([ln])
    assert [p.text for p in s.segments[0].pills] == ["Ctrl+Alt+Q"]   # 夹具自检：胶囊认出来了
    s.apply(s.generation, SegmentResult(1, text, "vllm", note, spans))
    return s


def _write(tmp_path, s):
    return snapshot.write_snapshot(snapshot.build_snapshot(s, now=1.0), tmp_path)


def _tamper(folder, fn):
    f = folder / "snapshot.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    fn(d)
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_snapshot_stores_pill_spans_and_pill_count():
    d = snapshot.build_snapshot(_session(), now=1.0).data
    assert d["schema_version"] == 3
    assert d["results"][0]["pill_spans"] == [[2, 12, 0]]
    assert d["placements"][0]["pills"] == 1
    assert d["placements"][0]["lines"] == [TR]


def test_snapshot_result_without_spans_stores_empty_list():
    d = snapshot.build_snapshot(_session(spans=()), now=1.0).data
    assert d["results"][0]["pill_spans"] == [] and d["placements"][0]["pills"] == 0


def test_replay_v3_uses_saved_spans(tmp_path):
    # 译文里胶囊文字出现两次（重算猜不出是哪一处）；存下的位置是第二处——重放照样贴回
    s = _session(spans=((16, 26, 0),), text="按 Ctrl+Alt+Q，不是 Ctrl+Alt+Q")
    assert s._rendered().placements[1].pills == 1
    rep = replay_report(_write(tmp_path, s))
    assert rep.result.placements[1].pills == 1
    assert [g.status for g in rep.segs] == ["same"]


def test_replay_v3_saved_empty_spans_are_not_guessed(tmp_path):
    # 版本 3 存的是「没有位置」（当时就是当文字画的）：重放照样当文字画，不去猜
    rep = replay_report(_write(tmp_path, _session(spans=())))
    assert rep.result.placements[1].pills == 0


def test_replay_old_snapshot_guesses_spans(tmp_path):
    # 版本 2 没存位置：胶囊文字在译文里恰好一处，重算出来照样贴回
    folder = _write(tmp_path, _session())

    def to_v2(d):
        d["schema_version"] = 2
        for r in d["results"]:
            r.pop("pill_spans")

    _tamper(folder, to_v2)
    assert replay_report(folder).result.placements[1].pills == 1


def test_replay_old_snapshot_does_not_guess_for_noted_result(tmp_path):
    # 腾讯不带占位符重翻的结果（带 note）：胶囊没受保护，不猜
    folder = _write(tmp_path, _session(note=NOTE_UNPROTECTED, spans=()))
    _tamper(folder, lambda d: [r.pop("pill_spans") for r in d["results"]])
    rep = replay_report(folder)
    assert rep.result.placements[1].pills == 0 and rep.result.placements[1].lines == [TR]


def test_guess_unique_occurrence_only():
    pills = (Pill("now.md", Box(0, 0, 9, 9)), Pill("id", Box(0, 0, 9, 9)))
    assert guess_pill_spans("见 now.md 和 id、id", pills) == ((2, 8, 0),)


def test_guess_missing_text_gives_nothing():
    assert guess_pill_spans("见 now 和", (Pill("now.md", Box(0, 0, 9, 9)),)) == ()


def test_guess_uses_fixed_key_combo_spelling():
    assert guess_pill_spans("按 Ctrl+K 键", (Pill("Ctr1+K", Box(0, 0, 9, 9)),)) == ((2, 8, 0),)


def test_guess_skips_occurrence_inside_a_word():
    # 评审 m4：胶囊「id」只在 identifier 里出现——不是它，不给位置
    pills = (Pill("id", Box(0, 0, 9, 9)),)
    assert guess_pill_spans("设置 identifier 字段", pills) == ()


def test_guess_counts_only_word_bounded_occurrences():
    # identifier 里那处不算，独立的 id 只有一处：给它
    pills = (Pill("id", Box(0, 0, 9, 9)),)
    assert guess_pill_spans("把 identifier 设成 id", pills) == ((16, 18, 0),)


def test_guess_cjk_pill_not_restricted_by_neighbours():
    # 不是 ASCII 字母数字开头结尾的胶囊，两边挨着什么都行
    assert guess_pill_spans("按键盘", (Pill("键", Box(0, 0, 9, 9)),)) == ((1, 2, 0),)


def test_remap_follows_pill_identity_not_index():
    # 评审 m3：快照里这段的胶囊顺序是 [B, A]，重新分段后是 [A, B]：编号按文字和框换过来
    saved = [{"text": "bb", "box": [50, 0, 9, 9]}, {"text": "aa", "box": [10, 0, 9, 9]}]
    new = (Pill("aa", Box(10, 0, 9, 9)), Pill("bb", Box(50, 0, 9, 9)))
    assert remap_pill_spans(((0, 2, 0), (5, 7, 1)), saved, new) == ((0, 2, 1), (5, 7, 0))


def test_remap_drops_pill_missing_from_new_segment():
    # 同名胶囊、但框不同（新分段里是另一个胶囊）：不对过去，照旧当文字画
    saved = [{"text": "id", "box": [50, 0, 9, 9]}]
    new = (Pill("id", Box(10, 0, 9, 9)),)
    assert remap_pill_spans(((0, 2, 0),), saved, new) == ()


def test_remap_drops_out_of_range_index():
    assert remap_pill_spans(((0, 2, 3),), [], (Pill("id", Box(10, 0, 9, 9)),)) == ()


def test_replay_v3_remaps_when_regrouped_pills_differ(tmp_path):
    # 快照里存的胶囊框被改成别处（相当于重新分段后这一段的胶囊不是当时那个）：位置不套用，当文字画
    folder = _write(tmp_path, _session())
    _tamper(folder, lambda d: d["segments"][0]["pills"][0].__setitem__("box", [1, 1, 5, 5]))
    assert replay_report(folder).result.placements[1].pills == 0


def test_guess_skips_occurrence_glued_on_the_left_only():
    # rapid 里的 id 只有左边粘着字母：也不算，独立的 id 只有一处
    assert guess_pill_spans("把 rapid 设成 id", (Pill("id", Box(0, 0, 9, 9)),)) == ((11, 13, 0),)


def test_remap_requires_same_text_too():
    # 框一样但文字不同（不是同一个胶囊）：不对过去
    saved = [{"text": "aa", "box": [10, 0, 9, 9]}]
    assert remap_pill_spans(((0, 2, 0),), saved, (Pill("bb", Box(10, 0, 9, 9)),)) == ()
