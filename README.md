# Figma Plugin Unlocker

为 Figma 桌面版的只读设计文件恢复插件菜单入口。

Figma 在 view-only 模式下会省略插件列表，显示 “Unavailable in view-only”。这个项目创建一个独立的桌面应用副本，在加载网页编辑器时修改插件相关的菜单判断，恢复 **Plugins → Development → Import plugin from manifest…** 等入口。

**已验证菜单恢复；尚未验证插件在只读文件中执行、读取节点或连接 MCP 服务。** 文档自身仍保持只读，补丁不会获得服务端的文件编辑权限。

## 支持范围

- macOS；需要系统自带的 `ditto` 和 `codesign`。
- Python 3 和 Node.js；脚本使用标准库，无需安装项目依赖。
- 桌面补丁使用共享规则匹配代码，不限制版本号或主脚本哈希。已在 Figma **126.10.13** 和 **126.11.7** 上验证下列行为；其他构建需要自行验证。

本次检查使用 macOS x86_64 安装包，其他架构需通过 `--check` 确认主脚本是否匹配。

| 版本 | 已验证 | 真实只读文件菜单 |
| --- | --- | --- |
| 126.10.13 | 规则匹配、菜单行为、打包、签名、启动、前端拦截 | 待验证 |
| 126.11.7 | 规则匹配、菜单行为、打包、签名、启动、前端拦截 | 已验证入口可用 |

启动与前端拦截在独立空白配置中测试，前端拦截通过在隔离的桌面 shell 渲染进程中加载公开 bundle 验证；不等同于已验证登录后的插件执行。

Figma 可以独立更新网页代码。遇到未适配的 bundle，应用会继续加载原始脚本，并记录 `[plugin-experiment] frontend unchanged`，此时插件菜单补丁不会生效。

## 兼容性检查

检查本机安装包，不创建或修改应用副本：

```bash
python3 scripts/patch-figma.py --check
```

可用 `--source /path/to/Figma.app` 检查其他位置。输出包括版本、主脚本哈希和每条补丁的匹配数量；哈希仅供记录。规则不匹配时会输出 `compatible: false` 并返回退出码 `2`。规则匹配不能保证未知构建的实际行为正确。

桌面应用版本和网页编辑器版本独立发布。`--check` 只检查桌面部分；网页部分在运行时单独检查每条补丁规则是否唯一匹配。

## 使用

先通过 **Figma → Quit** 正常退出 Figma，然后运行：

```bash
git clone https://github.com/kikyous/figma-plugin-unlocker.git
cd figma-plugin-unlocker
python3 scripts/patch-figma.py
bash scripts/launch-experiment.sh
```

默认读取 `/Applications/Figma.app`，生成 `.local/Figma Plugin Experiment.app`。项目目前保留这个实验应用名。原版安装文件不作修改，副本使用独立应用标识和本地临时签名。

打开只读设计文件，进入 **Main menu → Plugins → Development**，即可导入自己的插件 manifest。macOS 屏幕顶部的 Plugins 菜单也可作为入口。

副本默认沿用现有 Figma 本地配置，启动脚本会检查原版是否仍在运行。临时签名可能导致系统重新询问本地权限或登录状态。恢复日常使用时，退出副本并重新打开 `/Applications/Figma.app` 即可。

## 接入 Plumb

本项目可以用于尝试运行 [Plumb MCP](https://github.com/tathagat22/plumb-mcp) 的 Figma 插件。按照上游文档安装或构建插件，然后在 **Development → Import plugin from manifest…** 中选择它的 `figma-plugin/manifest.json`。

导入后运行 Plumb，再进行本地服务器配对。需要分别验证插件 UI 能否打开、节点能否读取、`plumb_status` 能否连接成功。此仓库不包含 Plumb 插件、Figma 应用文件或编译后的应用副本。

## 实现

补丁包括两个部分：

- 桌面端：用 `patches/desktop-rules.json` 中的共享正则规则匹配压缩后的名称。每条规则必须恰好匹配一次。补丁启用插件运行菜单项，转发时移除这些操作携带的 `showError` 标记。
- 网页编辑器：应用内部通过 Electron 调试接口拦截指定的公开 `figma_app` bundle，修改五处插件相关的选择器和菜单判断，仅针对普通 Design 编辑器。两个桌面版本共用这层前端补丁。

插件选择器使用局部副本处理只读标记，不改变全局文档状态。其他编辑器模式和服务端权限保持原有行为。

打包脚本会重新计算 ASAR 文件偏移、内容及分块哈希、头部哈希，以及原生加载器要求的八字节 base-36 长度标记，再对副本重新签名。原生库加载需要副本使用 `disable-library-validation` entitlement。

网络拦截使用应用内部调试接口，默认不开放远程调试端口。诊断环境变量 `FIGMA_PLUGIN_EXPERIMENT_DEBUG=1` 会启用本机 `127.0.0.1:19322` 调试端口；`FIGMA_PLUGIN_EXPERIMENT_PROFILE` 可以指定独立的测试配置目录。

## 重建与验证

退出实验副本后，重新构建：

```bash
python3 scripts/patch-figma.py --refresh
```

`--refresh` 会先核对现有副本是否与上次报告一致。可用 `--source` 和 `--output` 指定源应用与输出位置；自定义输出需要自行启动。每个输出路径在 `.local/builds/` 下保存独立的主脚本与报告，因此可以分别构建多个版本，不会覆盖其他副本的重建依据。旧版默认报告格式仍可用于刷新原有副本。

构建后执行桌面菜单验证和规则测试：

```bash
python3 -m unittest discover -s tests -v
node scripts/verify-menu-patch.mjs
codesign --verify --deep --strict ".local/Figma Plugin Experiment.app"
```

自定义输出的构建报告会打印 `artifactDirectory`。可将对应的报告传给验证脚本，脚本会按该版本的菜单符号执行测试：

```bash
node scripts/verify-menu-patch.mjs .local/builds/<output-id>/patch-report.json
```

前端验证需要下载已适配版本的公开网页脚本：

```bash
node scripts/fetch-frontend.mjs
node scripts/verify-frontend-patch.mjs
```

也可以将本地 bundle 路径传给 `verify-frontend-patch.mjs`。下载工具在保存前验证补丁匹配情况；验证脚本检查编译结果、缺失或重复的补丁目标、文档状态不变，以及其他编辑器模式、未登录状态和 Dev Mode 导出限制的原行为。

目前在本机实际只读文件中验证过：

- Development 菜单显示可用的新建和导入入口。
- 文档的 `isReadOnly` 仍为 `true`。
- 原版 ASAR 哈希未变，副本 101 个 ASAR 条目及 macOS 签名通过校验。

默认输出的报告仍保存在 `.local/patch-report.json`，每个输出的独立报告保存在其 `artifactDirectory`。报告记录实际版本、源文件与修改后文件的哈希。生成的应用、安装包、下载的网页代码、本地配置与调试数据均位于被 Git 忽略的 `.local/` 中。

## 适配其他版本

共享规则使用正则捕获压缩后的名称；每条规则必须唯一匹配，否则构建会中止。如果代码结构改变，需要先适配共享规则。检查安装包后，还应验证归档、签名、菜单行为和启动；真实文件菜单与插件执行需要分别验证。

新桌面版本可以复用前端补丁。若 Figma 更新了公开网页 bundle 且现有规则不再匹配，则还需独立适配 `scripts/plugin-access-hook.cjs` 的前端规则。
