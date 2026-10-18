# 第三方组件

| 组件 | 固定版本或来源 | 随附许可 |
| --- | --- | --- |
| MXU桌面前端 | 基于 [2.7.1](https://github.com/MistEO/MXU/tree/9fa8cc51e8ff8cd89d99f3ea55fe3a7a82e6ede3)，定制公告记忆复选框与可选启动任务 | [MXU.LICENSE](licenses/MXU.LICENSE) |
| MaaFramework与设备辅助程序 | [5.14.3官方发行版](https://github.com/MaaXYZ/MaaFramework/releases/tag/v5.14.3) | [MaaFramework.LICENSE.md](licenses/MaaFramework.LICENSE.md)及下载包内设备辅助程序许可 |
| PPOCR v5中文识别资源 | [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) | [MaaCommonAssets.LICENSE](licenses/MaaCommonAssets.LICENSE) |
| Python（Windows 完整包） | [3.13.6 官方嵌入式发行版](https://www.python.org/downloads/release/python-3136/) | 完整包内 `.venv/Scripts/LICENSE.txt` |

MXU 定制版的可执行程序、完整对应源码及构建说明可从 [MXU-Cangwu-source-and-runtime.zip](https://github.com/robot234/cangwu-maa/releases/download/v0.4.4/MXU-Cangwu-source-and-runtime.zip) 获取；仓库同时保留修改后的组件源码。

运行库不提交到源码仓库，Windows 完整包内包含运行库。版本、来源与文件校验值见 `DEPENDENCIES.json`；Python 依赖及传递依赖固定在 `requirements-windows.lock`，完整包保留各组件的许可证及 `.dist-info` 元信息。`PACKAGE.json` 列出包内文件与 SHA256。

`assets`及`app/resource/image`中的小幅界面裁切图供Maa识别商品、入口和币种使用；不包含完整账号截图。游戏名称、界面与素材属于各自权利人。
