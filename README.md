# Figma Plugin Unlocker

为 Figma 桌面版的只读设计文件恢复插件菜单入口。

Figma 在 view-only 模式下会省略插件列表，显示 “Unavailable in view-only”。这个项目创建一个独立的桌面应用副本，在加载网页编辑器时修改插件相关的菜单判断，恢复 **Plugins → Development → Import plugin from manifest…** 等入口。

**已验证菜单恢复；尚未验证插件在只读文件中执行、读取节点或连接 MCP 服务。** 文档自身仍保持只读，补丁不会获得服务端的文件编辑权限。

## 支持范围

- macOS；需要系统自带的 `ditto` 和 `codesign`。
- Python 3 和 Node.js；脚本使用标准库，无需安装项目依赖。
- 当前仅适配已检查过的 Figma **126.11.7** 构建。桌面主脚本和网页 bundle 均固定校验 SHA-256；其他构建和架构未验证。

Figma 可以独立更新网页代码。遇到未适配的 bundle，应用会继续加载原始脚本，并记录 `[plugin-experiment] frontend unchanged`，此时插件菜单补丁不会生效。

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

- 桌面端：启用插件运行菜单项，转发时移除这些操作携带的 `showError` 标记。
- 网页编辑器：应用内部通过 Electron 调试接口拦截指定的公开 `figma_app` bundle，修改五处插件相关的选择器和菜单判断，仅针对普通 Design 编辑器。

插件选择器使用局部副本处理只读标记，不改变全局文档状态。其他编辑器模式和服务端权限保持原有行为。

打包脚本会重新计算 ASAR 文件偏移、内容及分块哈希、头部哈希，以及原生加载器要求的八字节 base-36 长度标记，再对副本重新签名。原生库加载需要副本使用 `disable-library-validation` entitlement。

网络拦截使用应用内部调试接口，默认不开放远程调试端口。诊断环境变量 `FIGMA_PLUGIN_EXPERIMENT_DEBUG=1` 会启用本机 `127.0.0.1:19322` 调试端口；`FIGMA_PLUGIN_EXPERIMENT_PROFILE` 可以指定独立的测试配置目录。

## 重建与验证

退出实验副本后，重新构建：

```bash
python3 scripts/patch-figma.py --refresh
```

`--refresh` 会先核对现有副本是否与上次报告一致。可用 `--source` 和 `--output` 指定源应用与输出位置；自定义输出需要自行启动。

构建后执行桌面菜单验证：

```bash
node scripts/verify-menu-patch.mjs
codesign --verify --deep --strict ".local/Figma Plugin Experiment.app"
```

前端验证需要下载已适配版本的公开网页脚本：

```bash
node scripts/fetch-frontend.mjs
node scripts/verify-frontend-patch.mjs
```

也可以将本地 bundle 路径传给 `verify-frontend-patch.mjs`。下载工具在保存前验证哈希与补丁匹配情况；验证脚本检查编译结果、未知 bundle 拒绝、文档状态不变，以及其他编辑器模式、未登录状态和 Dev Mode 导出限制的原行为。

目前在本机实际只读文件中验证过：

- Development 菜单显示可用的新建和导入入口。
- 文档的 `isReadOnly` 仍为 `true`。
- 原版 ASAR 哈希未变，副本 101 个 ASAR 条目及 macOS 签名通过校验。

`.local/patch-report.json` 保存应用修改前后的哈希。生成的应用、下载的网页代码、本地配置与调试数据均位于被 Git 忽略的 `.local/` 中。
