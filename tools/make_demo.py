"""生成 README 用的演示动图 docs/demo.gif 和静态对比图 docs/demo-compare.png。

做法：用 Pillow 画一段自己写的「论文样」英文（标题、Abstract、两段正文，正文里有一个灰底圆角的
行内代码胶囊 `torch.compile()`；原创示例文字，不是任何真实论文），然后走和托盘程序同一条流水线——
Windows OCR 识别 → 分段 → 按你配置的服务链翻译（shotrans.app.build_router）→ 原位重绘——得到译文图，
最后拼成「原图 1.6 秒 → 从左往右擦出译文 → 译文 2.6 秒 → 擦回原图」循环播放的动图，底部加一行中英说明。

配置照常从数据目录读（%APPDATA%\\截图翻译\\config.toml，或环境变量 SHOTRANS_HOME 指的目录），
术语表用空的；不写任何配置文件，不打印配置内容、地址或密钥。有任何一段没译出来就不写动图，退出码 1。

用法（在仓库根目录）：
  python -m tools.make_demo                       真翻译，写到 docs/
  python -m tools.make_demo --fake --out 目录      不联网，用固定的假译文走一遍（检查画面用），写到「目录」
  --frames 目录                                    另存几张关键帧 PNG 方便检查"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FONTS = Path(r"C:\Windows\Fonts")
MAX_BYTES = int(2.5 * 1024 * 1024)

WIDTH = 820
MARGIN = 40
BODY_PX = 17
LINE_H = 28
INK = (28, 28, 28)
PILL_BG = (236, 236, 236)

TITLE = "Faster Training with Adaptive Kernel Fusion"
HEADING = "Abstract"
# `...` 包起来的是行内代码胶囊
PARAS = [
    "Modern deep learning workloads spend a surprising share of their runtime moving data between "
    "memory and compute units rather than doing arithmetic. We study how automatic kernel fusion can "
    "reduce this overhead without changes to model code. Our approach wraps an existing training loop "
    "with `torch.compile()` and picks fusion boundaries using a lightweight cost model learned from "
    "a few profiling runs.",
    "Across image classification and language modeling benchmarks, the method shortens training time "
    "by 18% on average while keeping accuracy unchanged. Most of the gain comes from fusing small "
    "elementwise operations, which suggests a simple rule of thumb for practitioners tuning their own "
    "pipelines.",
]
# --fake 用的固定译文（按段落顺序；{p0} 处贴回第一个胶囊）
FAKE_ZH = [
    "基于自适应算子融合的更快训练",
    "摘要",
    "现代深度学习任务的运行时间里，有相当大一部分花在内存和计算单元之间搬运数据，而不是做算术运算。"
    "我们研究自动算子融合能否在不改动模型代码的前提下降低这部分开销。我们的方法用 {p0} 包装现有的训练循环，"
    "并借助一个从少量性能剖析运行中学到的轻量代价模型来选择融合边界。",
    "在图像分类和语言建模基准上，该方法平均把训练时间缩短 18%，准确率保持不变。大部分收益来自融合细小的"
    "逐元素运算，这给调优自己流水线的从业者提供了一条简单的经验法则。",
]
CAPTION = "Ctrl+Alt+Q → 框选 select → 原位译文 in-place translation"
ROLE_LABEL = {"vllm": "主服务", "fallback": "备用服务", "tencent": "腾讯兜底", "fake": "假译文（--fake）"}


class StepFailed(Exception):
    """某一步失败：消息是给用户看的中文说明（不含配置内容）。"""


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _tokens(para: str) -> list[tuple[str, bool]]:
    """拆成 (词, 是否代码)；代码胶囊整体当一个词。"""
    out: list[tuple[str, bool]] = []
    for i, part in enumerate(re.split(r"`([^`]+)`", para)):
        if i % 2:
            out.append((part, True))
        else:
            out += [(w, False) for w in part.split()]
    return out


def render_source() -> Image.Image:
    """画原文图（像一张 820 宽的论文截图）。"""
    title_f, head_f = _font("georgiab.ttf", 24), _font("georgiab.ttf", 19)
    body_f, code_f = _font("georgia.ttf", BODY_PX), _font("consola.ttf", BODY_PX - 1)
    img = Image.new("RGB", (WIDTH, 1000), "white")
    d = ImageDraw.Draw(img)
    y = 34
    d.text((MARGIN, y), TITLE, font=title_f, fill=INK)
    y += 54
    d.text((MARGIN, y), HEADING, font=head_f, fill=INK)
    y += 38
    space = d.textlength(" ", font=body_f)
    right = WIDTH - MARGIN
    for para in PARAS:
        x = MARGIN
        for word, code in _tokens(para):
            f = code_f if code else body_f
            w = d.textlength(word, font=f) + (12 if code else 0)
            if x + w > right and x > MARGIN:
                x, y = MARGIN, y + LINE_H
            if code:
                # 胶囊：纯色、无描边、不抗锯齿（底色要是同一种颜色才认得出），上下留几像素
                d.rounded_rectangle((x, y - 3, x + w, y + BODY_PX + 5), radius=4, fill=PILL_BG)
                d.text((x + 6, y + 2), word, font=f, fill=(40, 40, 40))
            else:
                d.text((x, y), word, font=f, fill=INK)
            x += w + space
        y += LINE_H + 16
    return img.crop((0, 0, WIDTH, y + 10))


class FakeRouter:
    """不联网：按段落顺序给 FAKE_ZH 里的固定译文，胶囊按真服务的格式标出位置（pill_spans）。"""

    def translate(self, segments, direction, on_result) -> None:
        from shotrans.models import SegmentResult

        todo = [s for s in segments if not s.skip]
        for n, s in enumerate(todo):
            tpl = FAKE_ZH[n] if n < len(FAKE_ZH) else "（示例译文）"
            spans, text = [], tpl
            if "{p0}" in tpl and s.pills:
                head, tail = tpl.split("{p0}", 1)
                text = head + s.pills[0].text + tail
                spans.append((len(head), len(head) + len(s.pills[0].text), 0))
            else:
                text = tpl.replace("{p0}", "")
            on_result(SegmentResult(s.idx, text, "fake", pill_spans=tuple(spans)))


def _real_router():
    from shotrans import paths
    from shotrans.app import build_router
    from shotrans.config import load_config
    from shotrans.glossary import Glossary

    cfg = paths.config_path()
    if not cfg.exists():
        raise StepFailed("读取配置失败：数据目录里没有 config.toml，请先在托盘程序里配好翻译服务。")
    try:
        c = load_config(cfg)
    except Exception as e:  # 只报错误类型，不带内容
        raise StepFailed(f"读取配置失败（{type(e).__name__}），请在托盘程序里打开设置检查。") from None
    usable = [s for s in (c.primary, c.backup) if s.base_url.strip()]
    if any(s.key_unreadable and not s.api_key for s in usable):
        print("提示：有服务的 API key 解不开（换了电脑或 Windows 用户），这个服务可能会报认证失败。")
    return build_router(c, Glossary())


def translate(img: Image.Image, fake: bool) -> tuple[Image.Image, dict]:
    """和 tools.run_bench 的 full 关卡同一条路：识别 → 分段 → 翻译 → 重绘。失败的段重试一次。"""
    from shotrans.layout import detect_direction, group_lines
    from shotrans.ocr import recognize
    from shotrans.render import render

    try:
        segs = group_lines(recognize(img), "para", img)
    except Exception as e:
        raise StepFailed(f"文字识别（Windows OCR）失败：{type(e).__name__}。请确认系统装了英文 OCR 语言包。") from None
    todo = [s for s in segs if not s.skip]
    if not todo:
        raise StepFailed("文字识别（Windows OCR）没认出任何文字。")
    direction = detect_direction("\n".join(s.text for s in segs))
    router = FakeRouter() if fake else _real_router()
    results = {}

    def run(batch):
        router.translate(batch, direction, lambda r: results.__setitem__(r.idx, r))

    run(todo)
    bad = [s for s in todo if not (s.idx in results and results[s.idx].is_translation)]
    if bad:
        print(f"有 {len(bad)} 段没译出来，重试一次……")
        run(bad)
    good = {i: r for i, r in results.items() if r.is_translation}
    missing = [s.idx for s in todo if s.idx not in good]
    if missing:
        raise StepFailed(f"翻译失败：{len(todo)} 段里有 {len(missing)} 段没译出来（主服务、备用服务、腾讯兜底都没成功）。"
                         "请在托盘程序里点「测试连接」检查服务，没写动图。")
    no_zh = [i for i, r in good.items() if direction == "en2zh" and not re.search(r"[\u4e00-\u9fff]", r.text)]
    if no_zh:
        raise StepFailed(f"翻译结果不对：有 {len(no_zh)} 段译文里没有中文，没写动图。")
    out = render(img, segs, {i: r.text for i, r in good.items()}, "zh" if direction == "en2zh" else "en",
                 marked=frozenset(i for i, r in results.items() if r.note),
                 pill_spans={i: r.pill_spans for i, r in good.items() if r.pill_spans})
    info = {
        "segments": len(todo),
        "services": sorted({ROLE_LABEL.get(r.service, "其他") for r in good.values()}),
        "pills": sum(len(s.pills) for s in todo),
        "pills_pasted": sum(len(r.pill_spans) for r in good.values()),
        "truncated": sum(p.truncated for p in out.placements.values()),
        "noted": sum(1 for r in good.values() if r.note),
    }
    return out.image, info


def caption_strip(width: int) -> Image.Image:
    strip = Image.new("RGB", (width, 34), (245, 246, 248))
    d = ImageDraw.Draw(strip)
    d.line((0, 0, width, 0), fill=(214, 217, 222))
    f = _font("msyh.ttc", 14)
    tw = d.textlength(CAPTION, font=f)
    d.text(((width - tw) / 2, 8), CAPTION, font=f, fill=(70, 76, 86))
    return strip


def wipe(a: Image.Image, b: Image.Image, frac: float) -> Image.Image:
    """左边 frac 部分显示 b，其余 a，交界处一条蓝线。"""
    x = round(a.width * frac)
    f = a.copy()
    if x > 0:
        f.paste(b.crop((0, 0, x, b.height)), (0, 0))
    if 0 < x < a.width:
        ImageDraw.Draw(f).rectangle((x - 1, 0, x + 1, a.height), fill=(0, 120, 215))
    return f


def build_frames(src: Image.Image, dst: Image.Image, steps: int = 11):
    """返回 (帧, 每帧毫秒, 关键帧)：原图 1.6 s → 擦出 steps 帧（约 0.8 s）→ 译文 2.6 s → 擦回。"""
    cap = caption_strip(src.width)

    def framed(im):
        f = Image.new("RGB", (im.width, im.height + cap.height), "white")
        f.paste(im, (0, 0))
        f.paste(cap, (0, im.height))
        return f

    fwd = [framed(wipe(src, dst, k / (steps + 1))) for k in range(1, steps + 1)]
    back = [framed(wipe(src, dst, 1 - k / (steps + 1))) for k in range(1, steps + 1)]
    first, full = framed(src), framed(dst)
    frames = [first, *fwd, full, *back]
    durations = [1600] + [73] * steps + [2600] + [73] * steps
    keys = {"original": first, "wipe_mid": fwd[steps // 2], "translated": full}
    return frames, durations, keys


def write_gif(frames, durations, path: Path) -> None:
    # 全部帧共用一个调色板（从原图帧 + 译文帧取），不抖动：文字边缘干净、文件小
    h = frames[0].height
    both = Image.new("RGB", (frames[0].width, h * 2))
    both.paste(frames[0], (0, 0))
    both.paste(frames[len(frames) // 2], (0, h))
    pal = both.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(path, format="GIF", save_all=True, append_images=q[1:], duration=durations, loop=0,
              optimize=True, disposal=1)


def compare(src: Image.Image, dst: Image.Image, path: Path) -> None:
    gap = 16
    im = Image.new("RGB", (src.width * 2 + gap, src.height), (214, 217, 222))
    im.paste(src, (0, 0))
    im.paste(dst, (src.width + gap, 0))
    im.save(path, optimize=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 README 演示动图 demo.gif 和对比图 demo-compare.png")
    ap.add_argument("--out", type=Path, default=None, help="输出目录（默认仓库的 docs/；--fake 时必须另给）")
    ap.add_argument("--fake", action="store_true", help="不联网，用固定假译文走一遍（检查画面用）")
    ap.add_argument("--frames", type=Path, help="另存关键帧 PNG 的目录（检查用）")
    args = ap.parse_args(argv)
    out = args.out or DOCS
    if args.fake and out.resolve() == DOCS.resolve():
        print("--fake 的结果不能写进 docs/，请用 --out 指定别的目录。")
        return 2
    print("1/4 画原文图……")
    src = render_source()
    print("2/4 识别并翻译……" + ("（假译文，不联网）" if args.fake else ""))
    try:
        dst, info = translate(src, args.fake)
    except StepFailed as e:
        print(f"失败：{e}")
        return 1
    print("3/4 合成动图……")
    frames, durations, keys = build_frames(src, dst)
    out.mkdir(parents=True, exist_ok=True)
    gif = out / "demo.gif"
    tmp = out / "demo.gif.tmp"
    write_gif(frames, durations, tmp)
    size = tmp.stat().st_size
    if size > MAX_BYTES:
        tmp.unlink()
        print(f"失败：动图 {size / 1024 / 1024:.2f} MB，超过 2.5 MB，没写。")
        return 1
    tmp.replace(gif)
    compare(src, dst, out / "demo-compare.png")
    if args.frames:
        args.frames.mkdir(parents=True, exist_ok=True)
        src.save(args.frames / "source.png")
        dst.save(args.frames / "translated_raw.png")
        for name, f in keys.items():
            f.save(args.frames / f"{name}.png")
    print("4/4 完成")
    print(f"  译出段落：{info['segments']} / {info['segments']}；服务：{'、'.join(info['services'])}")
    print(f"  代码胶囊：识别 {info['pills']} 个，原样贴回 {info['pills_pasted']} 个；被截断的段：{info['truncated']}；"
          f"带提醒角标的段：{info['noted']}")
    print(f"  {gif}：{size / 1024:.0f} KB，{len(frames)} 帧，一轮 {sum(durations) / 1000:.1f} 秒，"
          f"{frames[0].width}×{frames[0].height}")
    print(f"  {out / 'demo-compare.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
