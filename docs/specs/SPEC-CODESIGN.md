# SPEC-CODESIGN · 三平台代码签名 / 公证评估

状态：评估稿（QUOT-549，只评估，未执行、未付费、未开通任何商业服务）
关联：SPEC-PACKAGING（QUOT-525）、DEC-076

## 1. 为什么需要

QUOT-525 产物未签名，普通用户双击时：

| 平台 | 拦截机制 | 用户体验 |
|---|---|---|
| Windows | SmartScreen「未知发布者」 | 蓝屏警告，需点「更多信息 → 仍要运行」 |
| macOS | Gatekeeper +公证检查 | 「无法验证开发者」，需右键打开或系统设置放行 |
| Linux | 无强制签名机制 | AppImage 需 `chmod +x`，无拦截 |

## 2. Windows

**流程**：
1. 购买 OV（组织验证）或 IV（个人验证）代码签名证书。传统 OV 证书从 SSL.com / Sectigo / DigiCert 等购买，约 **$70–$200/年**（SSL.com OV 约 $119/年，Sectigo 约 $80–$130/年）。
2. 2023-06 后 CA/Browser Forum 要求私钥必须存放在硬件介质（HSM / 云签名服务），不能用纯软证书文件。两条路径：
   - **云签名服务**（推荐）：SSL.com eSigner / Azure Trusted Signing（`Microsoft Azure Trusted Signing`，**$9.99/月**，个人也可开，集成 `signtool` 最简单）。
   - **实体 USB token**：证书写进 USB Key，签发时插 Key，CI 上无法自动化（或用远程签名服务）。
3. CI 集成：`signtool sign /fd SHA256 /tr <TSA> /td SHA256 <exe>`；Azure Trusted Signing 用 `azure-trusted-signing` GitHub Action。

**推荐**：Azure Trusted Signing（$9.99/月 ≈ ¥500+/年），GitHub Actions 原生支持， secrets 只放 Azure 凭据（env 注入，不进 yaml）。

**注意**：SmartScreen 信誉需要累积下载量（一般数千次安装后警告减轻），签名不会立刻消除提示，但会从「未知发布者」变为显示公司名。

## 3. macOS

**流程**：
1. 加入 Apple Developer Program：**$99/年**（个人）或 **$299/年**（企业 Developer Enterprise Program，企业内部分发才需要；对外分发用普通 $99 档即可）。
2. 创建 Developer ID Application 证书（用于分发到 App Store 之外）。
3. 签名：`codesign --deep --force --options runtime --sign "Developer ID Application: <name>" LadderStock.app`。
4. 公证：`xcrun notarytool submit LadderStock.dmg --keychain-profile <profile> --wait`，再 `xcrun stapler staple`。
5. CI 集成：App Store Connect API Key 放 secrets（env 注入），`notarytool` 支持 key profile。

**成本**：仅 $99/年（个人）。.dmg 打包可用 `hdiutil create` 或 `create-dmg`（开源）。

**注意**：py2app 产物必须启用 hardened runtime，且需用 entitlements 文件放行 JIT（如依赖需要）；第一次做要单独验真一轮。

## 4. Linux

无强制签名。可选增强：
- **AppImage**：可用 GPG 签名 AppImage（`appimagetool --sign`），成本为零，只是让高级用户可校验。
- 发行版包（.deb/.rpm）走发行版仓库签名，暂不必要。

**推荐**：暂不做，文档中提供 GPG 校验说明即可。

## 5. 成本汇总

| 项目 | 一次性 | 年费 | 必要性 |
|---|---|---|---|
| Azure Trusted Signing | 0 | ~$120（$9.99/月） | Win 普通人体验 |
| Apple Developer Program | 0 | $99 | macOS Gatekeeper |
| SSL.com OV 证书（替代方案） | USB token 硬件费 ~$100+ | ~$119 | Win（替代 Azure） |
| Linux GPG | 0 | 0 | 可选 |

**合计最低**：约 **$219/年**（Win Azure + macOS Apple），约 ¥1,600/年。

## 6. 三平台差异要点

1. **签名时机**：Win 在 zip 之前签 exe（zip 后校验哈希会变）；macOS 在 .app 打包 dmg 之前签，公证对象是 dmg/zip；Linux 无签名步骤。
2. **CI 自动化难度**：Azure Trusted Signing 与 GitHub Actions 集成最顺；macOS 公证需要 App Store Connect API Key；两者都需要 secrets 管理（env 注入，不进 yaml/日志/提交）。
3. **未签名兜底文案**：在 README-dist / 官网下载页写清楚「SmartScreen 提示 → 更多信息 → 仍要运行」「macOS → 右键打开」，作为不签名的过渡方案。

## 7. 决策建议（报批项，本单不执行）

1. **批准购买 Azure Trusted Signing**（Win）——性价比最高，CI 集成最简单。
2. **批准加入 Apple Developer Program**（$99/年）——macOS 分发的前提。
3. Linux 暂不做签名，只提供 GPG 校验文档。
4. 批准后再开签名接入子单（一条子单 = 一平台），不在本单内执行任何购买/开通。
