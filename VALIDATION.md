# PDF Reader 0.7.0 验证

Windows 本机回归已通过 174 项，1 项仅适用 macOS 的真实钥匙串测试在 Windows 跳过。包含认证加密篡改检测、密钥丢失或拒绝访问时不写明文、跨平台目录、Finder 事件队列及原有 PDF / OCR / AI 回归。

每个平台的最终结果由原生构建产生，不用 Windows 结果代替 Mac 结果。GitHub Releases 的 `PDFReader-validation-*.json` 记录精确提交、操作系统、架构、测试计数、源码与打包版的集成检查；Actions 保留原始 JUnit 和截图。macOS 的实际钥匙串测试必须在 Mac 执行。

构建流程运行真实 OCR 与附件子进程、真实后台 AI 线程及本机模拟 HTTP；测试从不使用私人密钥或外部 AI 服务。原有 0.6.2 的高 DPI 数学符号对照和 17 页只读 PDF 检查记录仍可在 Git 标签中查看。

Mac 包采用 ad-hoc 签名，未获 Apple Developer ID 签名或公证；首次打开流程见 README。物理打印机、所有 PDF 变体以及所有系统版本未逐一验证。
