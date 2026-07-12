# 智能选股 · 安卓 App（原生 WebView 封装）

把现有的「纯静态前端 + 本地数据包」选股工具，封装成一个**安卓原生 APK**。
手机里内置一份离线数据兜底；App 启动（或点「刷新」）时，由**原生层**去下载最新
`stocks.json` 并注入网页，从而绕开浏览器 `file://` 的跨域限制。

---

## 架构

```
┌─────────────────────────────────────────────┐
│  Android App (原生 WebView 壳)                │
│                                               │
│   assets/index.html   ← 前端页面（已改）       │
│   assets/data/data.js ← 内置离线数据（兜底）   │
│                                               │
│   MainActivity.java                           │
│     ├─ WebView 加载 index.html（file://）     │
│     ├─ 暴露 window.NativeBridge 给页面 JS      │
│     └─ 启动/刷新 → 原生下载远程 stocks.json     │
│            └─ evaluateJavascript 注入页面      │
└───────────────────┬─────────────────────────┘
                    │ HTTPS / HTTP
                    ▼
          你的托管地址（stocks.json）
          （GitHub Pages / 对象存储 / 任意静态托管）
```

关键点：**数据抓取 `build_dataset.py` 仍然跑在电脑或云端**，手机不跑 Python。
手机只负责"消费"最新的 `stocks.json`。

---

## 目录结构

```
android-app/
├── settings.gradle
├── build.gradle                 # 顶层：声明 AGP 8.5.2
├── gradle.properties
├── app/
│   ├── build.gradle             # ★ 改这里配置远程数据地址 DATA_URL
│   ├── proguard-rules.pro
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── java/com/stockscreener/app/MainActivity.java
│       ├── res/
│       │   ├── layout/activity_main.xml
│       │   └── values/{strings.xml, themes.xml}
│       └── assets/
│           ├── index.html       # 已改造：支持远程数据 + 刷新按钮
│           └── data/
│               ├── data.js      # 内置离线数据（兜底）
│               └── stocks.json
└── README.md
```

---

## 快速开始（在 Android Studio 出包）

1. 安装 **Android Studio**（含 JDK 17 与 Android SDK 34）。
2. 打开本项目：`File → Open` 选择 `android-app/` 目录。
   Android Studio 会提示下载 Gradle / AGP，**按提示 Sync 即可**（首次较慢）。
3. 连上安卓手机（开启 USB 调试），或新建一个模拟器。
4. 点击 ▶ `Run 'app'`，即可在手机上看到选股界面。
5. 出正式 APK：`Build → Build Bundle(s) / APK(s) → Build APK(s)`，
   生成的 `.apk` 在 `app/build/outputs/apk/debug/` 或 `release/`。

> 本环境无法编译 APK（缺 Android SDK），工程脚手架已就绪，出包需在你本地 Android Studio 完成。

---

## ★ 配置远程数据地址（必做）

打开 `app/build.gradle`，修改一行：

```gradle
buildConfigField "String", "DATA_URL", "\"https://your-host.example.com/stocks.json\""
```

把它换成你实际托管的 `stocks.json` 地址。例如：

- 阿里云 OSS：`"https://your-bucket.oss-cn-hangzhou.aliyuncs.com/stocks.json"`
- GitHub Pages：`"https://your-user.github.io/your-repo/stocks.json"`
- 腾讯云 COS / 七牛等任意静态托管均可

> 留空 `""` 时，App 仅使用内置离线数据，「刷新」按钮无效果。

### 如何托管 stocks.json（无需担心跨域）

因为数据是由**原生层 `HttpURLConnection` 下载**的（不是网页 `fetch`），
**托管服务器不需要配置 CORS**。你只要让这个 URL 能被公开访问即可：

- 最简单的白嫖：把仓库里的 `data/stocks.json` 上传到 GitHub 的 `gh-pages` 分支，
  或任意对象存储的公开 bucket。
- `AndroidManifest.xml` 已开启 `usesCleartextTraffic="true"`，
  所以即使你用 `http://`（非 https）也能拉取。

### 更新远程数据

在电脑上重跑抓取脚本：

```bash
C:/Users/16532/.workbuddy/binaries/python/envs/default/Scripts/python build_dataset.py
```

然后把新生成的 `data/stocks.json` 重新上传到上面的托管地址即可。
手机上打开 App 会自动拉取，或点右上角「↻ 刷新」手动更新。

---

## 如何更新内置离线数据

若想让 App 即使断网也有较新的数据，把最新 `data/data.js` 与 `data/stocks.json`
覆盖到 `app/src/main/assets/data/` 下，重新 Build 即可。

---

## 数据刷新机制

- App 启动 → WebView 加载内置 `index.html`（含离线 `data.js`）→ 立即可用。
- 页面加载完成 → 原生层下载远程 `stocks.json`：
  - 成功 → 注入页面，右上角状态变绿「实时已更新 · <时间>」。
  - 失败（无网/地址错）→ Toast 提示，继续用内置数据，状态保持「内置数据」。
- 点「↻ 刷新」→ 重新触发上述下载。

---

## 已知限制 / 注意

1. **技术形态覆盖口径不变**：`ma_bullish` 仅对市值前 400 只非空（与电脑端一致）。
2. **数据非实时**：抓取脚本取最近交易日收盘，手机拉到的也是该快照。
3. **大包注入**：当前 `stocks.json` 约 2.6MB，通过 `evaluateJavascript` 注入，
   单次打开完全可行；若日后数据涨到 10MB+，建议改用 `WebViewAssetLoader`
   把下载文件映射为 `https://appassets.androidplatform.net/...` 再用 `<script>` 加载。
4. **iOS**：本工程仅安卓。如需 iOS，可把同一套 `index.html` 放进 WKWebView
   壳（思路一致：原生下载 JSON → `evaluateJavaScript` 注入），可另开工程。

---

## 备选：PWA（想更快上手机）

如果你不想装 Android Studio，也可以把 `index.html` 改造成 PWA
（加 `manifest.webmanifest` + `service-worker.js`），用任意静态托管部署后，
安卓 Chrome 访问 → 「添加到主屏幕」即可像 App 一样使用，且天然离线。
当前工程已为 PWA 留好退路：`DEFAULT_REMOTE_URL` 填托管地址、用浏览器 `fetch` 拉取，
无需原生壳也能工作。
