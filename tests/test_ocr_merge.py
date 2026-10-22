"""Task 21：两个 OCR 引擎逐词拼接。

词框数据取自快照 debug/20261012-144932-ee6360（一个聊天网页，英文里夹中文）与另两份快照、bench 的真实识别结果
（scratchpad/dump_ocr_json.py 导出），反例尽量用真实出现过的误识别。每个守卫都有只触发它的夹具。"""
from shotrans import ocr
from shotrans.models import Box, OcrLine, OcrWord
from shotrans.ocr import combine_results, is_garbled, patch_en_with_zh, patch_zh_with_en


def W(text, x, y, w, h):
    return OcrWord(text, Box(x, y, w, h))


def L(text, box, *words):
    """text、box 照真实引擎给的写死，不从词算。"""
    return OcrLine(text, Box(*box), tuple(words))


def texts(lines):
    return [l.text for l in lines]


# ---------------------------------------------------------------- is_garbled
def test_garbled_real_en_misreads():
    assert is_garbled('"6ßE')          # 非 ASCII 字母
    assert is_garbled('iRB%lJ"')       # % 夹在字母间
    assert is_garbled('"ÉAAERB&IJ')
    assert is_garbled('"?f"')          # 剥掉引号后核心是「?f」：? 不在可剥的标点里


def test_garbled_clean_words():
    assert not is_garbled("Blood")
    assert not is_garbled("I")
    assert not is_garbled("(What")      # 首尾括号剥掉
    assert not is_garbled("{Submitted")  # arXiv 快照 en 的真实词：首部的 { 剥掉，不算乱码
    assert not is_garbled("[cs.CL]")


def test_garbled_trailing_question_mark_is_punctuation():
    # 评审 I1：词尾的 ? 是标点（What?、this?)），词首、词中的 ? 才算乱码
    assert not is_garbled("10-13?")
    assert not is_garbled("What?")
    assert not is_garbled("this?)")
    assert is_garbled("?f")
    assert is_garbled("a?b")


def test_garbled_symbol_only_word_is_not_garbled():
    # bench 16「asyncio — Asynchronous」：en 的「—」被 zh 认成「一」；arXiv「next >」的 > 被认成「冫」
    assert not is_garbled("—")
    assert not is_garbled(">")
    assert not is_garbled('"')
    assert not is_garbled("&")        # arXiv「References & Citations」的 &：单独的符号没有可换的
    assert not is_garbled("?")


def test_garbled_non_ascii_symbol_is_not_garbled():
    assert not is_garbled("x—y")        # 非 ASCII 的标点符号不算，只有非 ASCII 字母算
    assert not is_garbled("50°C")
    assert is_garbled("aßc")


def test_garbled_legit_percent_ampersand_plus():
    assert not is_garbled("15%")        # bench 05「15%」被 zh 认成「巧」
    assert not is_garbled("R&D")
    assert not is_garbled("C++")
    assert not is_garbled("A+")
    assert is_garbled("a%b")
    assert is_garbled("r&d")
    assert is_garbled("C+x")


# ---------------------------------------------------------------- 英文底稿补中文：真实例子
EN_CASCADE = L('Cascade journal (IC): added a task "6ßE cascade iRB%lJ" due 10-24.', (57, 189, 601, 22),
               W("task", 321, 191, 37, 15), W('"6ßE', 363, 189, 43, 19), W("cascade", 411, 192, 70, 14),
               W('iRB%lJ"', 486, 189, 80, 19), W("due", 572, 192, 32, 14))
ZH_CASCADE = L("task '确定cascade投稿期刊\" due", (57, 189, 838, 22),
               W("task", 321, 191, 37, 15), W("'", 366, 192, 3, 6), W("确", 370, 189, 18, 18),
               W("定", 388, 191, 18, 18), W("cascade", 411, 192, 70, 14), W("投", 486, 191, 18, 16),
               W("稿", 505, 189, 18, 19), W("期", 523, 191, 17, 16), W("刊", 542, 190, 17, 18),
               W('"', 561, 191, 6, 6), W("due", 572, 192, 32, 14))


def test_cascade_garbled_words_replaced_keeping_en_quotes():
    (out,) = patch_en_with_zh([EN_CASCADE], [ZH_CASCADE])
    assert out.text == 'task "确定cascade投稿期刊" due'
    assert [w.text for w in out.words] == ["task", '"确', "定", "cascade", "投", "稿", "期", '刊"', "due"]
    assert out.words[1].box == Box(370, 189, 18, 18)       # 框用 zh 的


EN_GROUP = L('I also added "ÉAAERB&IJ 10-13" to the', (58, 357, 330, 20),
             W("added", 105, 359, 54, 14), W('"ÉAAERB&IJ', 164, 357, 116, 18), W('10-13"', 285, 358, 48, 15),
             W("to", 338, 360, 17, 14))
ZH_GROUP = L('| also added "组会是否改到10 · 13 “ to', (58, 357, 510, 20),
             W("added", 105, 359, 54, 14), W('"', 164, 359, 6, 6), W("组", 171, 357, 18, 17), W("会", 189, 357, 18, 18),
             W("是", 207, 358, 18, 17), W("否", 226, 358, 18, 17), W("改", 245, 358, 17, 16), W("到", 263, 357, 17, 18),
             W("10", 285, 359, 18, 14), W("·", 303, 365, 6, 2), W("13", 309, 359, 16, 14), W("“", 327, 358, 6, 6),
             W("to", 338, 360, 17, 14))


def test_group_meeting_replaced_and_en_number_kept():
    (out,) = patch_en_with_zh([EN_GROUP], [ZH_GROUP])
    assert out.text == 'added "组会是否改到10-13" to'


EN_REPLY = L('needs your 0K. Reply "?f" and I\'ll', (271, 439, 279, 20),
             W("0K.", 370, 440, 30, 14), W("Reply", 407, 439, 48, 20), W('"?f"', 459, 439, 32, 16),
             W("and", 497, 439, 31, 15))
ZH_REPLY = L('your OK. Reply “改" and', (327, 439, 201, 20),
             W("OK.", 370, 440, 30, 14), W("Reply", 407, 439, 48, 20), W("“", 459, 439, 6, 6),
             W("改", 467, 439, 18, 16), W('"', 486, 439, 6, 6), W("and", 497, 439, 31, 15))


def test_reply_single_hanzi_replaces_garbled_word():
    (out,) = patch_en_with_zh([EN_REPLY], [ZH_REPLY])
    assert out.text == '0K. Reply "改" and'


EN_BLOOD = L("Blood project in the thesis (2C): added a task I", (58, 248, 864, 20),
             W("Blood", 58, 249, 48, 15), W("added", 342, 249, 53, 15), W("a", 400, 253, 10, 11),
             W("task", 414, 249, 37, 15), W("I", 920, 250, 2, 14))
ZH_BLOOD = L('引00d project … task "写开题报告初稿前决定血液抑郁项目是否纳入博士论文"，|', (58, 247, 864, 22),
             W("引", 58, 249, 15, 14), W("00d", 74, 249, 32, 15), W("added", 342, 249, 53, 15),
             W("a", 400, 253, 10, 11), W("task", 414, 249, 37, 15), W('"', 456, 249, 6, 6),
             *[W(c, 463 + 18 * i, 247, 17, 18) for i, c in enumerate("写开题报告初稿前决定血液抑郁项目是否纳入博士论文")],
             W('"', 905, 249, 6, 6), W("，", 911, 261, 2, 4), W("|", 920, 250, 2, 14))


def test_blood_dropped_quote_inserted_with_quotes_and_blood_kept():
    (out,) = patch_en_with_zh([EN_BLOOD], [ZH_BLOOD])
    assert out.text == 'Blood added a task "写开题报告初稿前决定血液抑郁项目是否纳入博士论文" I'
    assert out.box == Box(58, 247, 864, 21)                 # 行框取并集


EN_PPT = L("answer: should the PPT task's", (206, 412, 298, 20),
           W("should", 277, 412, 58, 15), W("the", 340, 413, 30, 14), W("PPT", 415, 413, 32, 14),
           W("task's", 452, 412, 52, 15))
ZH_PPT = L("shOUld the组会P PT task", (277, 412, 213, 20),
           W("shOUld", 277, 412, 58, 15), W("the", 340, 413, 30, 14), W("组", 374, 412, 18, 17),
           W("会", 393, 412, 17, 18), W("P", 415, 413, 10, 14), W("PT", 427, 413, 20, 14), W("task", 452, 412, 38, 15))


def test_ppt_dropped_two_hanzi_inserted_in_gap():
    (out,) = patch_en_with_zh([EN_PPT], [ZH_PPT])
    assert out.text == "should the组会PPT task's"


# ---------------------------------------------------------------- 英文底稿补中文：反例
def test_clean_en_word_overlapping_two_hanzi_not_replaced():
    # arXiv 快照「Connected Papers (What is」：zh 把「(What」认成「巾颔」，(What 是干净英文，保留
    en = [L("Papers (What is", (172, 737, 88, 13), W("Papers", 172, 737, 40, 13), W("(What", 217, 737, 32, 13),
            W("is", 252, 737, 8, 13))]
    zh = [L("Papers巾颔is", (172, 737, 88, 13), W("Papers", 172, 737, 40, 13), W("巾", 228, 737, 10, 13),
            W("颔", 239, 737, 10, 13), W("is", 252, 737, 8, 13))]
    assert texts(patch_en_with_zh(en, zh)) == ["Papers (What is"]


def test_blood_vs_yin00d_not_replaced():
    en = [L("Blood project", (58, 249, 118, 20), W("Blood", 58, 249, 48, 15), W("project", 112, 249, 64, 20))]
    zh = [L("引00d project", (58, 249, 118, 20), W("引", 58, 249, 15, 14), W("00d", 74, 249, 32, 15),
            W("project", 112, 249, 64, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ["Blood project"]


def test_what_i_changed_vs_noise_shu_not_replaced():
    en = [L("What I changed:", (16, 152, 142, 19), W("What", 16, 152, 48, 14), W("I", 69, 152, 4, 14),
            W("changed:", 78, 152, 80, 19))]
    zh = [L("What丨changed：", (16, 152, 142, 19), W("What", 16, 152, 48, 14), W("丨", 69, 152, 4, 14),
            W("changed", 78, 152, 75, 19), W("：", 155, 157, 3, 10))]
    assert texts(patch_en_with_zh(en, zh)) == ["What I changed:"]


def test_garbled_word_overlapping_run_less_than_half_not_replaced():
    # 「"6ßE」宽 43，汉字串只盖住它 15 像素：不算同一个词；串碰到了 en 词，也不插入
    en = [L('task "6ßE due', (321, 189, 283, 19), W("task", 321, 191, 37, 15), W('"6ßE', 363, 189, 43, 19),
            W("due", 572, 192, 32, 14))]
    zh = [L("确定", (391, 189, 40, 18), W("确", 391, 189, 18, 18), W("定", 409, 191, 18, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ['task "6ßE due']


def test_two_hanzi_partly_touching_clean_word_not_inserted():
    # 串和 en 词有重叠（不到一半）：en 不是「什么都没认出」，不插入
    en = [L("Bibliographic Explorer (What", (104, 698, 164, 13), W("Bibliographic", 104, 698, 75, 13),
            W("Explorer", 183, 698, 49, 13), W("(What", 236, 698, 32, 13))]
    zh = [L("地图", (220, 698, 30, 13), W("地", 220, 698, 9, 13), W("图", 232, 698, 3, 13))]
    assert texts(patch_en_with_zh(en, zh)) == ["Bibliographic Explorer (What"]


def test_single_hanzi_in_gap_not_inserted():
    en = [L("should the PPT", (277, 412, 170, 20), W("should", 277, 412, 58, 15), W("the", 340, 413, 30, 14),
            W("PPT", 415, 413, 32, 14))]
    zh = [L("组", (374, 412, 18, 17), W("组", 374, 412, 18, 17))]
    assert texts(patch_en_with_zh(en, zh)) == ["should the PPT"]


def test_noise_shu_does_not_count_as_hanzi():
    # 「组丨」只有 1 个真汉字：不插入
    en = [L("should the PPT", (277, 412, 170, 20), W("should", 277, 412, 58, 15), W("the", 340, 413, 30, 14),
            W("PPT", 415, 413, 32, 14))]
    zh = [L("组丨", (374, 412, 30, 17), W("组", 374, 412, 18, 17), W("丨", 397, 412, 2, 17))]
    assert texts(patch_en_with_zh(en, zh)) == ["should the PPT"]


def test_cjk_punct_inside_run_joins_it():
    en = [L("should the PPT", (277, 412, 170, 20), W("should", 277, 412, 58, 15), W("the", 340, 413, 30, 14),
            W("PPT", 415, 413, 32, 14))]
    zh = [L("组，会", (374, 412, 40, 17), W("组", 374, 412, 12, 17), W("，", 388, 420, 3, 6),
            W("会", 396, 412, 12, 17))]
    assert texts(patch_en_with_zh(en, zh)) == ["should the组，会PPT"]


def test_non_quote_neighbour_not_inserted():
    # 串后面紧挨着「，」不是引号：不跟着插；行尾的「，」也不算进汉字串
    en = [L("added a task I", (342, 249, 580, 20), W("task", 414, 249, 37, 15), W("I", 920, 250, 2, 14))]
    zh = [L("写开，", (463, 248, 60, 18), W("写", 463, 248, 17, 18), W("开", 481, 248, 18, 17),
            W("，", 501, 260, 3, 4))]
    assert texts(patch_en_with_zh(en, zh)) == ["task写开I"]


def test_non_quote_before_run_not_inserted():
    # 串前面紧挨着的是「：」不是引号：不跟着插
    en = [L("added a task I", (342, 249, 580, 20), W("task", 414, 249, 37, 15), W("I", 920, 250, 2, 14))]
    zh = [L("：写开", (455, 248, 44, 18), W("：", 455, 255, 3, 10), W("写", 463, 248, 17, 18), W("开", 481, 248, 18, 17))]
    assert texts(patch_en_with_zh(en, zh)) == ["task写开I"]


def test_quote_overlapping_kept_en_word_not_inserted():
    # zh 的引号落在 en 保留的词上（en 把引号认进了词里）：不再插一个
    en = [L('task" I', (414, 249, 508, 20), W('task"', 414, 249, 46, 15), W("I", 920, 250, 2, 14))]
    zh = [L('"写开', (456, 248, 43, 18), W('"', 456, 249, 6, 6), W("写", 463, 248, 17, 18), W("开", 481, 248, 18, 17))]
    assert texts(patch_en_with_zh(en, zh)) == ['task"写开I']


def test_zh_line_not_vertically_overlapping_is_ignored():
    en = [L("should the PPT", (277, 412, 170, 20), W("should", 277, 412, 58, 15), W("the", 340, 413, 30, 14),
            W("PPT", 415, 413, 32, 14))]
    zh = [L("组会", (374, 425, 36, 18), W("组", 374, 425, 18, 18), W("会", 393, 425, 17, 18))]   # 只重叠 7/18
    assert texts(patch_en_with_zh(en, zh)) == ["should the PPT"]


def test_run_far_from_en_line_not_inserted():
    # 同一高度、离 en 行 300 像素（另一栏）：不插到这一行
    en = [L("should the PPT", (277, 412, 170, 20), W("should", 277, 412, 58, 15), W("the", 340, 413, 30, 14),
            W("PPT", 415, 413, 32, 14))]
    zh = [L("组会", (747, 412, 36, 18), W("组", 747, 412, 18, 18), W("会", 766, 412, 17, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ["should the PPT"]


def test_run_just_past_line_end_is_inserted():
    # en 丢了行尾的一段：串在 en 行框外 6 像素（< 1 倍行高）照样补上
    en = [L("added a task", (342, 249, 109, 20), W("added", 342, 249, 53, 15), W("task", 414, 249, 37, 15))]
    zh = [L("写开", (457, 248, 36, 18), W("写", 457, 248, 17, 18), W("开", 475, 248, 18, 17))]
    assert texts(patch_en_with_zh(en, zh)) == ["added task写开"]


def test_run_goes_to_nearest_of_two_columns():
    left = L("left col", (20, 412, 300, 20), W("left", 20, 412, 40, 15), W("col", 290, 412, 30, 15))
    right = L("right col", (340, 412, 300, 20), W("right", 340, 412, 40, 15), W("col", 610, 412, 30, 15))
    zh = [L("组会", (330, 412, 8, 18), W("组", 330, 412, 4, 18), W("会", 334, 412, 4, 18))]   # 离左 10、离右 2
    assert texts(patch_en_with_zh([left, right], zh)) == ["left col", "组会right col"]


def test_pure_english_image_unchanged():
    en = [L("The quick brown fox", (20, 30, 300, 20), W("The", 20, 30, 40, 20), W("quick", 70, 30, 70, 20)),
          L("jumps over", (20, 60, 200, 20), W("jumps", 20, 60, 80, 20))]
    zh = [L("The quick", (20, 30, 300, 20), W("The", 20, 30, 40, 20), W("quick", 70, 30, 70, 20)),
          L("jumps", (20, 60, 200, 20), W("jumps", 20, 60, 80, 20))]
    out = combine_results(en, zh)
    assert all(a is b for a, b in zip(out, en)) and len(out) == 2


# ---------------------------------------------------------------- 中文底稿补英文
def test_zh_base_split_latin_word_fixed_from_en():
    # bench 01：zh 把 target_t 认成「tar、get_t」，en 认对了
    zh = [L("tar、get_t", (20, 30, 120, 20), W("tar", 20, 30, 30, 20), W("、", 52, 40, 4, 6), W("get_t", 58, 30, 62, 20))]
    en = [L("target_t", (20, 30, 100, 20), W("target_t", 20, 30, 100, 20))]
    assert texts(patch_zh_with_en(zh, en)) == ["target_t"]


def test_zh_base_odd_case_fixed_from_en():
    zh = [L("记录bOth选项", (0, 0, 120, 20), W("记", 0, 0, 18, 18), W("录", 18, 0, 18, 18), W("bOth", 40, 0, 38, 14),
            W("选", 82, 0, 18, 18), W("项", 100, 0, 18, 18))]
    en = [L("both", (40, 0, 38, 14), W("both", 40, 0, 38, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["记录both选项"]


def test_zh_base_extra_space_removed():
    # bench 02：「2层Bi LSTM」→ en 的 BiLSTM
    zh = [L("2层Bi LSTM能", (0, 0, 130, 20), W("2", 0, 0, 8, 14), W("层", 10, 0, 18, 18), W("Bi", 30, 0, 16, 14),
            W("LSTM", 50, 0, 50, 14), W("能", 104, 0, 18, 18))]
    en = [L("BiLSTM", (30, 0, 70, 14), W("BiLSTM", 30, 0, 70, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["2层BiLSTM能"]


def test_zh_base_leading_cjk_punct_not_swallowed():
    # bench 02「模式，2层Bi LSTM」：词段前面的「，」不能算进词段，否则换成 en 的词时被丢掉
    zh = [L("式，Bi LSTM能", (0, 0, 130, 20), W("式", 0, 0, 18, 18), W("，", 20, 10, 4, 6), W("Bi", 30, 0, 16, 14),
            W("LSTM", 50, 0, 50, 14), W("能", 104, 0, 18, 18))]
    en = [L("BiLSTM", (30, 0, 70, 14), W("BiLSTM", 30, 0, 70, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["式，BiLSTM能"]


def test_zh_base_trailing_cjk_punct_not_swallowed():
    zh = [L("式Bi LSTM，能", (0, 0, 130, 20), W("式", 0, 0, 18, 18), W("Bi", 30, 0, 16, 14),
            W("LSTM", 50, 0, 50, 14), W("，", 100, 10, 4, 6), W("能", 110, 0, 18, 18))]
    en = [L("BiLSTM", (30, 0, 70, 14), W("BiLSTM", 30, 0, 70, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["式BiLSTM，能"]


def test_zh_base_different_letters_kept():
    # bench 02：en 把「1层」的 1 认成 I；bench 20：en 把 OK 认成 0K——字母不同，信 zh
    zh = [L("1层", (5, 161, 30, 14), W("1", 5, 161, 8, 14), W("层", 15, 161, 18, 14))]
    en = [L("I", (5, 161, 8, 14), W("I", 5, 161, 8, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["1层"]


def test_zh_base_different_letters_kept_even_with_odd_case():
    # zh 的大小写怪（bOth），但 en 认的是另一串字母：不换
    zh = [L("记录bOth选项", (0, 0, 120, 20), W("记", 0, 0, 18, 18), W("录", 18, 0, 18, 18), W("bOth", 40, 0, 38, 14),
            W("选", 82, 0, 18, 18), W("项", 100, 0, 18, 18))]
    en = [L("bath", (40, 0, 38, 14), W("bath", 40, 0, 38, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["记录bOth选项"]


def test_combine_uses_en_base_for_english_image():
    assert texts(combine_results([EN_REPLY], [ZH_REPLY])) == ['0K. Reply "改" and']


def test_zh_base_case_only_difference_with_normal_case_kept():
    # bench 08「A-V-G+P」：en 认成「A-V-G+p」，只差大小写、zh 的大小写正常，信 zh
    zh = [L("A-V-G+P", (0, 0, 80, 14), W("A-V-G+P", 0, 0, 80, 14))]
    en = [L("A-V-G+p", (0, 0, 80, 14), W("A-V-G+p", 0, 0, 80, 14))]
    assert texts(patch_zh_with_en(zh, en)) == ["A-V-G+P"]


def test_zh_base_en_word_reaching_outside_segment_kept():
    # en 词伸出 zh 词段太多（盖住了旁边的汉字）：不换
    zh = [L("tar、get定", (20, 30, 120, 20), W("tar", 20, 30, 30, 20), W("、", 52, 40, 4, 6), W("get", 58, 30, 30, 20),
            W("定", 92, 30, 18, 18))]
    en = [L("target", (20, 30, 90, 20), W("target", 20, 30, 90, 20))]
    assert texts(patch_zh_with_en(zh, en)) == ["tar、get定"]


def test_zh_base_en_line_not_vertically_overlapping_ignored():
    zh = [L("tar、get_t", (20, 30, 120, 20), W("tar", 20, 30, 30, 20), W("、", 52, 40, 4, 6), W("get_t", 58, 30, 62, 20))]
    en = [L("target_t", (20, 45, 100, 20), W("target_t", 20, 45, 100, 20))]    # 只重叠 5/20
    assert texts(patch_zh_with_en(zh, en)) == ["tar、get_t"]


def test_combine_uses_zh_base_for_chinese_image():
    zh = [L("抑郁症的识别tar、get_t", (0, 30, 240, 20), *[W(c, 18 * i, 30, 18, 18) for i, c in enumerate("抑郁症的识别")],
            W("tar", 110, 30, 30, 20), W("、", 142, 40, 4, 6), W("get_t", 148, 30, 62, 20))]
    en = [L("target_t", (110, 30, 100, 20), W("target_t", 110, 30, 100, 20))]
    assert texts(combine_results(en, zh)) == ["抑郁症的识别target_t"]


def test_recognize_returns_combined_result(monkeypatch):
    seen = {}

    def fake_combine(en, zh):
        seen["args"] = (en, zh)
        return ["combined"]

    monkeypatch.setattr(ocr, "combine_results", fake_combine)
    from PIL import Image
    assert ocr.recognize(Image.new("RGB", (200, 60), "white")) == ["combined"]
    assert "args" in seen


# ---------------------------------------------------------------- 评审修复（Task 21 fix）
def test_clean_word_with_trailing_question_mark_not_replaced():
    # 评审 I1：en「this?)」是干净英文（词尾 ? 是标点），zh 在同一位置认成「巾颔」：不换
    en = [L("Papers is this?)", (172, 737, 128, 13), W("Papers", 172, 737, 40, 13), W("is", 252, 737, 8, 13),
            W("this?)", 265, 737, 35, 13))]
    zh = [L("巾颔", (268, 737, 28, 13), W("巾", 268, 737, 14, 13), W("颔", 282, 737, 14, 13))]
    assert texts(patch_en_with_zh(en, zh)) == ["Papers is this?)"]


def test_wide_garbled_word_keeps_latin_neighbour_from_zh():
    # 评审 I2：en 把「用户名Python」认成一个乱码词「ßPython」：换的时候 zh 的「Python」也要换进来，不能丢
    en = [L("I use ßPython daily", (0, 0, 180, 20), W("I", 0, 0, 5, 20), W("use", 10, 0, 30, 20),
            W("ßPython", 45, 0, 90, 20), W("daily", 140, 0, 40, 20))]
    zh = [L("I use用户名Python daily", (0, 0, 180, 20), W("I", 0, 0, 5, 20), W("use", 10, 0, 30, 20),
            W("用", 45, 0, 16, 20), W("户", 61, 0, 16, 20), W("名", 77, 0, 16, 20), W("Python", 95, 0, 40, 20),
            W("daily", 140, 0, 40, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ["I use用户名Python daily"]


def test_wide_garbled_word_covering_two_runs_not_inserted_twice():
    # 一个乱码词盖住两串汉字：第一串换的时候已经把第二串带进来了，第二串不能再插一次
    en = [L("ßÉÄ", (100, 0, 60, 20), W("ßÉÄ", 100, 0, 60, 20))]
    zh = [L("确定a投稿", (100, 0, 60, 20), W("确", 100, 0, 20, 20), W("定", 120, 0, 20, 20), W("a", 141, 0, 2, 20),
            W("投", 144, 0, 8, 20), W("稿", 152, 0, 8, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ["确定a投稿"]


def test_partly_overlapping_garbled_word_is_replaced_too():
    # 评审 M-a：「"6ßE」只和汉字串重叠 46%（不到一半），另一个乱码词重叠过半——两个一起换掉，不留重复
    en = [L('a "6ßE iRB%l" b', (0, 0, 144, 20), W("a", 0, 0, 10, 20), W('"6ßE', 14, 0, 43, 20),
            W('iRB%l"', 58, 0, 72, 20), W("b", 134, 0, 10, 20))]
    zh = [L('a "确定投稿期 b', (0, 0, 144, 20), W("a", 0, 0, 10, 20), W('"', 14, 0, 4, 6),
            *[W(c, 37 + 18 * i, 0, 18, 20) for i, c in enumerate("确定投稿期")], W("b", 134, 0, 10, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ['a "确定投稿期" b']


def test_hit_with_one_clean_word_is_not_replaced():
    # 评审 M1：汉字串盖住两个 en 词，一个乱码、一个干净（ok）：宁可保留 en，两个都不换
    en = [L('task "6ßE ok', (321, 189, 109, 19), W("task", 321, 191, 37, 15), W('"6ßE', 363, 189, 43, 19),
            W("ok", 408, 191, 22, 15))]
    zh = [L("确定投", (365, 189, 61, 18), W("确", 365, 189, 18, 18), W("定", 383, 189, 18, 18), W("投", 408, 189, 18, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ['task "6ßE ok']


def test_trailing_quote_taken_from_last_hit_word():
    # 评审 M2：两个乱码词，只有后一个带尾引号：引号接在换进来的最后一个字后面
    en = [L('x ÉA ßB" y', (60, 0, 130, 20), W("x", 60, 0, 10, 20), W("ÉA", 100, 0, 30, 20),
            W('ßB"', 132, 0, 38, 20), W("y", 180, 0, 10, 20))]
    zh = [L("组会是否", (100, 0, 64, 20), W("组", 100, 0, 16, 20), W("会", 116, 0, 16, 20), W("是", 132, 0, 16, 20),
            W("否", 148, 0, 16, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ['x组会是否" y']


def test_lines_of_very_different_height_still_overlap():
    # 评审 M5：en 行高 10、zh 行高 40，重叠 10 像素（≥ 矮行的一半，< 高行的一半）：算同一行
    en = [L("should the PPT", (277, 412, 170, 10), W("should", 277, 412, 58, 10), W("the", 340, 412, 30, 10),
            W("PPT", 415, 412, 32, 10))]
    zh = [L("组会", (374, 397, 36, 40), W("组", 374, 397, 18, 40), W("会", 393, 397, 17, 40))]
    assert texts(patch_en_with_zh(en, zh)) == ["should the组会PPT"]


def test_box_like_misread_hanzi_not_inserted():
    # 评审 M-c：图标被 zh 认成「回田」，en 那里没词：不插
    en = [L("Settings Profile", (100, 10, 150, 20), W("Settings", 130, 10, 60, 20), W("Profile", 200, 10, 50, 20))]
    zh = [L("回田", (100, 10, 25, 20), W("回", 100, 10, 12, 20), W("田", 113, 10, 12, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ["Settings Profile"]


def test_run_with_some_box_like_hanzi_still_inserted():
    # 只要有一个不是方框形的字（「回语」），照常插入
    en = [L("Settings Profile", (100, 10, 150, 20), W("Settings", 130, 10, 60, 20), W("Profile", 200, 10, 50, 20))]
    zh = [L("回语", (100, 10, 25, 20), W("回", 100, 10, 12, 20), W("语", 113, 10, 12, 20))]
    assert texts(patch_en_with_zh(en, zh)) == ["回语Settings Profile"]


def test_tie_goes_to_line_with_larger_vertical_overlap():
    # 评审 M-f：两个 en 行离汉字串都是 0，竖直重叠 10 对 18：给重叠大的那行
    a = L("alpha beta", (0, 100, 300, 20), W("alpha", 0, 100, 50, 20), W("beta", 250, 100, 50, 20))
    b = L("gamma delta", (0, 112, 300, 20), W("gamma", 0, 112, 50, 20), W("delta", 250, 112, 50, 20))
    zh = [L("组会", (120, 110, 38, 20), W("组", 120, 110, 18, 20), W("会", 140, 110, 18, 20))]
    assert texts(patch_en_with_zh([a, b], zh)) == ["alpha beta", "gamma组会delta"]


def test_zh_base_en_word_reaching_left_outside_segment_kept():
    # 评审 M4：en 词向左伸出 zh 词段 20 像素（盖住了左边的汉字）：不换
    zh = [L("定tar、get_t", (0, 30, 120, 20), W("定", 0, 30, 18, 18), W("tar", 40, 30, 30, 20), W("、", 72, 40, 4, 6),
            W("get_t", 78, 30, 42, 20))]
    en = [L("target_t", (20, 30, 100, 20), W("target_t", 20, 30, 100, 20))]
    assert texts(patch_zh_with_en(zh, en)) == ["定tar、get_t"]


def test_zh_base_fullwidth_punct_must_match():
    # 评审 M-d：zh「1．0」、en「10」（en 漏了小数点）：全角标点不忽略，字母不同，不换
    zh = [L("版本1．0发布", (0, 30, 102, 20), W("版", 0, 30, 18, 18), W("本", 18, 30, 18, 18), W("1", 40, 30, 8, 20),
            W("．", 49, 40, 4, 4), W("0", 54, 30, 8, 20), W("发", 66, 30, 18, 18), W("布", 84, 30, 18, 18))]
    en = [L("10", (40, 30, 22, 20), W("10", 40, 30, 22, 20))]
    assert texts(patch_zh_with_en(zh, en)) == ["版本1．0发布"]

def test_partly_overlapping_clean_word_is_kept():
    # 只碰到一点的干净词（task）不跟着乱码词一起换掉
    en = [L('task "6ßE', (321, 189, 82, 19), W("task", 321, 191, 37, 15), W('"6ßE', 360, 189, 43, 19))]
    zh = [L("确定投", (350, 189, 53, 18), W("确", 350, 189, 18, 18), W("定", 368, 189, 18, 18), W("投", 386, 189, 17, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ['task "确定投']

# ---------------------------------------------------------------- 复审 I7 / m1
def test_strong_garbled_examples():
    from shotrans.ocr import is_strong_garbled
    for t in ['"6ßE', 'iRB%lJ"', '"ÉAAERB&IJ', '"?f"', "ßEAA", '"ÉBB"']:
        assert is_strong_garbled(t), t
    for t in ["a+b", "x=1", "café", "naïve", "José", "Zürich", "#include", "arr[i]", "C#", "foo@bar.com",
              "15%", "R&D", "Blood", "&", "?"]:   # 单独的符号不是乱码（is_garbled 就不算）
        assert not is_strong_garbled(t), t


def test_touching_code_notation_word_not_merged():
    # 复审 I7(1)：汉字串命中「ßE」，旁边碰到 2 像素的「a+b」只是代码记号，不并入（否则换成 zh 的「a十b」）
    en = [L("ßE a+b ok", (0, 0, 110, 20), W("ßE", 0, 0, 36, 18), W("a+b", 38, 0, 30, 14), W("ok", 90, 0, 20, 14))]
    zh = [L("用户 a十b ok", (0, 0, 110, 20), W("用", 0, 0, 18, 18), W("户", 18, 0, 22, 18),
            W("a十b", 40, 0, 28, 14), W("ok", 90, 0, 20, 14))]
    assert texts(patch_en_with_zh(en, zh)) == ["用户a+b ok"]


def test_touching_accented_word_not_merged():
    # 复审 I7(1)：碰到一点的「café」是正常拼写，不并入（否则换成 zh 的「cafe」）
    en = [L("ßE café ok", (0, 0, 110, 20), W("ßE", 0, 0, 36, 18), W("café", 38, 0, 40, 14), W("ok", 90, 0, 20, 14))]
    zh = [L("用户 cafe ok", (0, 0, 110, 20), W("用", 0, 0, 18, 18), W("户", 18, 0, 22, 18),
            W("cafe", 40, 0, 38, 14), W("ok", 90, 0, 20, 14))]
    assert texts(patch_en_with_zh(en, zh)) == ["用户café ok"]


def test_zh_latin_word_with_hanzi_not_brought_in():
    # 复审 I7(2)：宽乱码词「ßE00d」范围里的 zh「引00d」夹着汉字，不带进来，只换汉字串
    en = [L("ßE00d project", (0, 0, 140, 20), W("ßE00d", 0, 0, 70, 18), W("project", 80, 0, 60, 18))]
    zh = [L("血液 引00d project", (0, 0, 140, 20), W("血", 0, 0, 18, 18), W("液", 18, 0, 18, 18),
            W("引00d", 38, 0, 32, 14), W("project", 80, 0, 60, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ["血液project"]


def test_zh_latin_word_with_odd_case_not_brought_in():
    # 复审 I7(2)：宽乱码词范围里的 zh「bOth」大小写怪，不带进来
    en = [L("x ßEboth y", (0, 0, 130, 20), W("x", 0, 0, 8, 14), W("ßEboth", 20, 0, 70, 18), W("y", 120, 0, 8, 14))]
    zh = [L("x 选择bOth y", (0, 0, 130, 20), W("x", 0, 0, 8, 14), W("选", 20, 0, 18, 18), W("择", 38, 0, 18, 18),
            W("bOth", 58, 0, 42, 14), W("y", 120, 0, 8, 14))]
    assert texts(patch_en_with_zh(en, zh)) == ["x选择y"]


def test_middle_hit_word_keeps_its_quotes():
    # 复审 m1：两个命中词，后一个「"ÉBB"」的左引号也要保留（接在它左边之后的第一个新词前面）
    en = [L('ßEAA "ÉBB" end', (0, 0, 230, 20), W("ßEAA", 0, 0, 80, 18), W('"ÉBB"', 100, 0, 60, 18),
            W("end", 200, 0, 30, 14))]
    zh = [L("确定 PPT 组会是否", (0, 0, 154, 20), W("确", 0, 0, 18, 18), W("定", 18, 0, 18, 18), W("PPT", 40, 0, 30, 14),
            W("组", 64, 0, 18, 18), W("会", 100, 0, 18, 18), W("是", 118, 0, 18, 18), W("否", 136, 0, 18, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ['确定PPT组"会是否" end']


def test_middle_hit_word_trailing_quote_kept():
    # 前一个命中词带尾引号「ÉA"」：引号接在中心早于它右边的最后一个新词后面
    en = [L('ÉA" ßB end', (0, 0, 230, 20), W('ÉA"', 0, 0, 60, 18), W("ßB", 70, 0, 60, 18), W("end", 200, 0, 30, 14))]
    zh = [L("组会是否", (0, 0, 130, 20), W("组", 0, 0, 25, 18), W("会", 30, 0, 25, 18), W("是", 72, 0, 25, 18),
            W("否", 100, 0, 25, 18))]
    assert texts(patch_en_with_zh(en, zh)) == ['组会"是否end']