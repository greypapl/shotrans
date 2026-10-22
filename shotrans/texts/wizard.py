"""首次运行引导的文字。"""

ZH = {
    "wizard.title": "截图翻译 首次设置",
    "wizard.back": "上一步",
    "wizard.next": "下一步",
    "wizard.finish": "完成",
    "wizard.try_now": "现在试一下",
    "wizard.welcome.title": "欢迎使用截图翻译",
    "wizard.welcome.text": "先选界面语言，接下来检查环境、填写翻译服务。",
    "wizard.welcome.language": "界面语言",
    "wizard.env.title": "环境检查",
    "wizard.env.ocr_en": "英文 OCR 语言包（en-US）",
    "wizard.env.ocr_zh": "简体中文 OCR 语言包（zh-Hans-CN）",
    "wizard.env.have": "✅ {name}：已安装",
    "wizard.env.missing": "❌ {name}：未安装",
    "wizard.env.steps": "安装方法：打开「设置 → 时间和语言 → 语言和区域」，点「添加语言」，选「英语（美国）」或"
                        "「中文（简体，中国）」，安装时勾选「光学字符识别」；装好后点「重新检测」。"
                        "只装一个也能用，只是另一种语言认得差。",
    "wizard.env.open_settings": "打开系统语言设置",
    "wizard.env.recheck": "重新检测",
    "wizard.env.continue_anyway": "仍然继续（没有 OCR 语言包时翻译会失败）",
    "wizard.service.title": "翻译服务",
    "wizard.service.note": "选一家服务、填上你自己的 API key，点「测试连接」通过后才能进入下一步。",
    "wizard.finish.title": "完成",
    "wizard.finish.hotkey": "快捷键",
    "wizard.finish.autostart": "开机自启",
    "wizard.finish.autostart_trial": "试用模式（用临时数据目录启动）不会改动开机自启。",
    "wizard.finish.usage": "用法：① 按快捷键 ② 框选屏幕上的文字 ③ 鼠标停在某段上看原文（空格切换原文 / 译文）。",
    "wizard.save_failed": "保存失败：{error}",
}

EN = {
    "wizard.title": "ShotTrans setup",
    "wizard.back": "Back",
    "wizard.next": "Next",
    "wizard.finish": "Finish",
    "wizard.try_now": "Try it now",
    "wizard.welcome.title": "Welcome to ShotTrans",
    "wizard.welcome.text": "Choose the interface language; next we check your system and set up a translation service.",
    "wizard.welcome.language": "Interface language",
    "wizard.env.title": "System check",
    "wizard.env.ocr_en": "English OCR language pack (en-US)",
    "wizard.env.ocr_zh": "Simplified Chinese OCR language pack (zh-Hans-CN)",
    "wizard.env.have": "✅ {name}: installed",
    "wizard.env.missing": "❌ {name}: not installed",
    "wizard.env.steps": "How to install: open Settings → Time & language → Language & region, click Add a language, "
                        "choose English (United States) or Chinese (Simplified, China) and tick Optical character "
                        "recognition; then click Check again. One pack is enough to start; the other language will "
                        "just be recognized less well.",
    "wizard.env.open_settings": "Open language settings",
    "wizard.env.recheck": "Check again",
    "wizard.env.continue_anyway": "Continue anyway (translation will fail without an OCR language pack)",
    "wizard.service.title": "Translation service",
    "wizard.service.note": "Pick a service, paste your own API key and pass Test connection to continue.",
    "wizard.finish.title": "All set",
    "wizard.finish.hotkey": "Hotkey",
    "wizard.finish.autostart": "Start with Windows",
    "wizard.finish.autostart_trial": "Trial mode (temporary data folder) doesn't change start-with-Windows.",
    "wizard.finish.usage": "How to use: 1. press the hotkey 2. select text on screen 3. hover a paragraph to see the "
                           "original (Space toggles original / translation).",
    "wizard.save_failed": "Saving failed: {error}",
}
