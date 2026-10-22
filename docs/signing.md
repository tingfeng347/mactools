# macOS 签名与公证

未签名的应用会被 Gatekeeper 拦截，Homebrew 安装后首次启动也需要右键「打开」。正式分发需要 Apple **Developer ID Application** 证书签名，并通过 Apple 公证（notarization）。

## 前置条件

| 项目 | 说明 |
|---|---|
| Apple Developer Program | 个人或公司账号，99 USD/年（中国区 ¥688/年） |
| Developer ID Application 证书 | 用于分发，不是 `Apple Development` 或 `Mac App Distribution` |
| Team ID | 开发者账号 Membership 页面中的 10 位标识 |
| 公证凭据 | Apple ID + App 专用密码，或 App Store Connect API Key |

公司账号注册需要 D-U-N-S 编号；个人账号需要实名与身份验证。证书创建需要 Account Holder 或 Admin 权限。

## 1. 创建 Developer ID Application 证书

1. 打开「钥匙串访问」→「证书助理」→「从证书颁发机构请求证书」，选择「存储到磁盘」，得到 `CertificateSigningRequest.certSigningRequest`。
2. 登录 [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/certificates/list)，点击 `+`，选择 **Developer ID Application**，上传 CSR。
3. 下载 `.cer` 并双击安装到「登录」钥匙串。
4. 在「钥匙串访问」中找到该证书及其私钥，右键导出为 `.p12`，设置导出密码。

确认证书可用：

```bash
security find-identity -v -p codesigning
```

输出中应包含：

```text
"Developer ID Application: Your Name (TEAMID)"
```

## 2. 准备公证凭据

### 方式 A：Apple ID + App 专用密码

1. 在 [Apple ID](https://account.apple.com) 的「登录与安全」中生成 App 专用密码。
2. 记录 Apple ID、专用密码和 Team ID。

### 方式 B：App Store Connect API Key

1. 在 App Store Connect →「用户和访问」→「集成」→ App Store Connect API 创建 Team Key，权限选择 Developer。
2. 下载 `.p8`（只能下载一次），记录 Issuer ID 与 Key ID。

## 3. 本地构建并自动签名、公证

Tauri 会在构建时完成签名、公证和 stapling：

```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID="you@example.com"
export APPLE_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="TEAMID"

cd gui
PATH="$HOME/.cargo/bin:$PATH" CI=true npm run tauri -- build \
  --target universal-apple-darwin --bundles dmg
```

证书不在本机钥匙串时，额外提供 `.p12`，Tauri 会导入临时钥匙串：

```bash
export APPLE_CERTIFICATE="$(openssl base64 -A -in DeveloperID.p12)"
export APPLE_CERTIFICATE_PASSWORD="p12-password"
```

使用 API Key 时，以 `APPLE_API_ISSUER`、`APPLE_API_KEY`、`APPLE_API_KEY_PATH` 替代 `APPLE_ID`、`APPLE_PASSWORD`、`APPLE_TEAM_ID`。

## 4. 验证

```bash
APP="gui/src-tauri/target/universal-apple-darwin/release/bundle/macos/mactools.app"
DMG="gui/src-tauri/target/universal-apple-darwin/release/bundle/dmg/mactools_<version>_universal.dmg"

codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv --verbose=4 "$APP"
xcrun stapler validate "$DMG"
spctl -a -t open --context context:primary-signature -v "$DMG"
```

`codesign` 输出应包含 `Authority=Developer ID Application` 与 `TeamIdentifier`；`spctl` 应返回 `accepted`。

## 5. GitHub Actions

在仓库 Secrets 中配置：

| Secret | 内容 |
|---|---|
| `APPLE_CERTIFICATE` | `.p12` 的 Base64：`openssl base64 -A -in DeveloperID.p12` |
| `APPLE_CERTIFICATE_PASSWORD` | `.p12` 导出密码 |
| `APPLE_SIGNING_IDENTITY` | `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | Apple ID |
| `APPLE_PASSWORD` | App 专用密码 |
| `APPLE_TEAM_ID` | Team ID |

然后手动运行 `Build and release GUI` workflow，填入已有 tag。workflow 会校验版本、构建通用 DMG、签名、公证并上传到 Release。

签名完成后，删除并重装 cask 可验证全新安装体验：

```bash
brew uninstall --cask mactools-gui
brew install --cask tingfeng347/tap/mactools-gui
```

## 6. 更新 Homebrew cask

发布新 DMG 后同步更新 tap 中 `Casks/mactools-gui.rb` 的 `version` 与 `sha256`：

```bash
shasum -a 256 gui/src-tauri/target/universal-apple-darwin/release/bundle/dmg/mactools_<version>_universal.dmg
```

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `code object is not signed at all` | 未设置 `APPLE_SIGNING_IDENTITY`，或证书未安装 |
| `The specified item could not be found in the keychain` | 证书不在默认钥匙串；改用 `APPLE_CERTIFICATE` 让 Tauri 导入临时钥匙串 |
| notarytool 返回 401 | 使用了账号密码；必须使用 App 专用密码或 API Key |
| 公证成功但 Gatekeeper 仍拦截 | 未 stapling；执行 `xcrun stapler staple <dmg>` 后重新验证 |
| `spctl` 提示 `source=Unnotarized Developer ID` | 只签名未公证，或公证未通过 |
| 修改 `.app` 内容后签名失效 | 任何改动都要重新签名并重新公证 |

> Developer ID 证书和公证凭据属于账号机密，不要提交到仓库。CI 中只通过 Secrets 注入。
