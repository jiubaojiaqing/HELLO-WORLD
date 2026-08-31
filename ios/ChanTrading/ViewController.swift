import UIKit
import WebKit

class ViewController: UIViewController, WKNavigationDelegate {
    private var webView: WKWebView!

    override func loadView() {
        let cfg = WKWebViewConfiguration()
        // 持久化数据存储: IndexedDB / localStorage 数据随 App 保留 (用户导入的 CSV 会话)
        cfg.websiteDataStore = WKWebsiteDataStore.default()

        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.navigationDelegate = self
        webView.scrollView.contentInsetAdjustmentBehavior = .never  // viewport-fit=cover, 页面自行处理安全区
        webView.backgroundColor = .black
        view = webView
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        // 加载 App 包内 Web/index.html (内含离线引擎 engine_offline.js + 预装数据 data_bundle.js)
        guard let webDir = Bundle.main.url(forResource: "Web", withExtension: nil) else {
            failAlert()
            return
        }
        webView.loadFileURL(webDir.appendingPathComponent("index.html"), allowingReadAccessTo: webDir)
    }

    private func failAlert() {
        let alert = UIAlertController(title: "资源缺失", message: "Web 资源未打包进 App", preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "确定", style: .default))
        present(alert, animated: true)
    }

    // 页面内 window.open / target=_blank 兜底
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        webView.load(navigationAction.request)
        return nil
    }
}
