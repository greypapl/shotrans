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
    "wizard.env.cmd_note": "「设置」里下载失败（比如报错 0x80070005）时：在开始菜单搜索 PowerShell，右键选「以管理员身份运行」，"
                           "把下面的命令粘贴进去回车，装好后点「重新检测」。",
    "wizard.env.copy_cmd": "复制命令",
    "wizard.env.copied": "已复制",
    "wizard.env.denied": "还是报「拒绝访问」：先关掉「设置」窗口再运行一次；不行就重启电脑，重启后先别打开「设置」，直接再运行；"
                         "再不行就暂时关掉杀毒软件再试。公司或学校管理的电脑可能要找管理员处理。",
    "wizard.env.recheck": "重新检测",
    "wizard.env.continue_anyway": "仍然继续（没有 OCR 语言包时翻译会失败）",
    "wizard.service.title": "翻译服务",
    "wizard.service.mode_free": "免费：微软翻译（不想注册就选这个，装好就能用）",
    "wizard.service.free_desc": "识别出的文字会发给微软翻译。这是微软 Edge 浏览器网页翻译用的接口，不是正式开放的服务，"
                                "哪天可能用不了（那时可以改用上面的大模型服务，智谱有免费模型）。普通机器翻译，术语表照样生效。"
                                "点「测试连接」通过后才能进入下一步。",
    "wizard.service.mode_llm": "大模型服务（推荐：翻得更好、懂上下文；要 API key，智谱免费）",
    "wizard.service.note": "选一家服务、填上你自己的 API key，点「测试连接」通过后才能进入下一步。"
                           "智谱 GLM-4.7-Flash 是免费模型，用手机号注册就能拿 key。"
                           "有独立显卡、不想让文字出电脑，可以选「Ollama（本机）」在自己电脑上跑模型。",
    "wizard.service.free_backup": "主服务连不上时，用微软免费翻译兜底（这时文字会发给微软）",
    "wizard.free.test": "测试连接",
    "wizard.free.testing": "测试中…",
    "wizard.free.ok": "✅ 能用（{msg}）",
    "wizard.free.bad": "❌ {msg}",
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
    "wizard.env.cmd_note": "If the download fails in Settings (for example error 0x80070005): search for PowerShell "
                           "in the Start menu, right-click it, choose Run as administrator, paste the command below "
                           "and press Enter, then click Check again.",
    "wizard.env.copy_cmd": "Copy command",
    "wizard.env.copied": "Copied",
    "wizard.env.denied": "Still \"access denied\"? Close Settings and run it again. If that fails, restart the PC "
                         "and run it again before opening Settings. If it still fails, pause your antivirus and "
                         "try again. A PC managed by your company or school may need its administrator.",
    "wizard.env.recheck": "Check again",
    "wizard.env.continue_anyway": "Continue anyway (translation will fail without an OCR language pack)",
    "wizard.service.title": "Translation service",
    "wizard.service.mode_free": "Free: Microsoft Translator (if you don't want to sign up; works right away)",
    "wizard.service.free_desc": "Recognized text is sent to Microsoft Translator. This is the endpoint Microsoft Edge "
                                "uses to translate web pages, not an official public service, so it may stop working "
                                "some day (you can then switch to the AI model service above; Zhipu has a free "
                                "model). Plain machine translation; your glossary still applies. Pass Test "
                                "connection to continue.",
    "wizard.service.mode_llm": "AI model service (recommended: better, context-aware translations; needs an "
                               "API key, Zhipu is free)",
    "wizard.service.note": "Pick a service, paste your own API key and pass Test connection to continue. "
                           "Zhipu GLM-4.7-Flash is a free model; sign up with a phone number to get a key. With a "
                           "dedicated GPU, or if text must not leave your computer, choose \"Ollama (local)\" "
                           "to run a model on your own machine.",
    "wizard.service.free_backup": "If the primary service can't be reached, fall back to Microsoft's free "
                                  "translation (text is then sent to Microsoft)",
    "wizard.free.test": "Test connection",
    "wizard.free.testing": "Testing…",
    "wizard.free.ok": "✅ Works ({msg})",
    "wizard.free.bad": "❌ {msg}",
    "wizard.finish.title": "All set",
    "wizard.finish.hotkey": "Hotkey",
    "wizard.finish.autostart": "Start with Windows",
    "wizard.finish.autostart_trial": "Trial mode (temporary data folder) doesn't change start-with-Windows.",
    "wizard.finish.usage": "How to use: 1. press the hotkey 2. select text on screen 3. hover a paragraph to see the "
                           "original (Space toggles original / translation).",
    "wizard.save_failed": "Saving failed: {error}",
}
