# MXU 界面定制

基于 [MXU v2.7.1](https://github.com/MistEO/MXU/tree/9fa8cc51e8ff8cd89d99f3ea55fe3a7a82e6ede3)，遵循上游 AGPL-3.0 许可证。

`WelcomeDialog.tsx` 替换上游同名组件，为公告底部增加“下次更新前不再弹出”复选框，并按项目版本与公告内容保存用户选择。

`optional-startup.patch` 让新建配置时的“开始游戏”显示在任务列表中，保持默认不勾选。

`CangwuUpdateSection.tsx`、`cangwuUpdateService.ts` 和 `cangwu_update.rs` 提供项目更新设置、空闲安装调度与固定命令桥接；`automatic-update.patch` 将它们接入 MXU。下载校验和文件替换由项目的 `cangwu/updater.py` 执行，保留用户配置与运行记录。

发布版所用 MXU 可执行程序与完整对应源码收录在 [MXU-Cangwu-source-and-runtime.zip](https://github.com/robot234/cangwu-maa/releases/download/v0.4.5/MXU-Cangwu-source-and-runtime.zip)。上游版权及许可证保留在源码包和 `licenses` 目录中。

重新构建需要 Windows x64、Node.js、pnpm、Rust MSVC 工具链和 Windows SDK：

1. 解压源码包，进入 `source/MXU`。
2. 运行 `pnpm install --frozen-lockfile`。
3. 运行 `pnpm tauri build --no-bundle --target x86_64-pc-windows-msvc -- --locked`。
4. 输出为 `src-tauri/target/x86_64-pc-windows-msvc/release/mxu.exe`。

从上游恢复构建时，将 `WelcomeDialog.tsx` 复制到 `src/components/WelcomeDialog.tsx`；将更新组件、服务、Rust 模块分别复制到 `src/components/settings/CangwuUpdateSection.tsx`、`src/services/cangwuUpdateService.ts`、`src-tauri/src/commands/cangwu_update.rs`。依次使用 `git apply` 应用 `welcome-i18n.patch`、`optional-startup.patch`、`automatic-update.patch`，并将 `package.json`、`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml` 及 Cargo.lock 中根包的版本设为 `2.7.1`。
