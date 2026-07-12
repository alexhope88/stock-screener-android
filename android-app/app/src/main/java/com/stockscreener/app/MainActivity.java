package com.stockscreener.app;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * 极简原生 WebView 壳：加载 assets/index.html（含离线内置数据），
 * 启动 / 点“刷新”时由原生层下载远程最新 stocks.json 并注入页面，
 * 从而绕开浏览器 file:// 的跨域限制。
 */
public class MainActivity extends Activity {

    private WebView webView;
    // 远程数据地址，由 app/build.gradle 的 buildConfigField 注入
    private final String REMOTE_DATA_URL = BuildConfig.DATA_URL;
    private boolean remoteTriggered = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webview);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            settings.setAlgorithmicDarkeningAllowed(true);
        }

        // 暴露原生桥给页面 JS：window.NativeBridge.refresh() / getDataUrl()
        webView.addJavascriptInterface(new NativeBridge(), "NativeBridge");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                // 页面加载完成后自动拉取一次远程数据（仅触发一次）
                if (!remoteTriggered) {
                    remoteTriggered = true;
                    refreshRemote();
                }
            }
        });

        webView.loadUrl("file:///android_asset/index.html");
    }

    /** 下载远程最新数据并注入页面；失败时保持内置离线数据 */
    private void refreshRemote() {
        if (REMOTE_DATA_URL == null || REMOTE_DATA_URL.isEmpty()) return;
        new Thread(() -> {
            try {
                final String json = download(REMOTE_DATA_URL);
                if (json == null || json.trim().isEmpty() || !json.trim().startsWith("{")) {
                    throw new Exception("数据格式异常");
                }
                // 将整个 JSON 作为对象字面量直接传入，避免逐字符转义
                final String js = "window.__applyRemoteData(" + json + ");";
                runOnUiThread(() -> webView.evaluateJavascript(js, null));
            } catch (final Exception e) {
                runOnUiThread(() -> Toast.makeText(
                        MainActivity.this,
                        "远程数据更新失败，继续使用内置数据",
                        Toast.LENGTH_SHORT).show());
            }
        }).start();
    }

    private String download(String u) throws Exception {
        URL url = new URL(u);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");
        conn.connect();
        int code = conn.getResponseCode();
        if (code != HttpURLConnection.HTTP_OK) {
            conn.disconnect();
            throw new Exception("HTTP " + code);
        }
        InputStream in = conn.getInputStream();
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
        }
        conn.disconnect();
        return sb.toString();
    }

    /** 页面 JS 可调用的原生方法 */
    public class NativeBridge {
        @JavascriptInterface
        public void refresh() { refreshRemote(); }

        @JavascriptInterface
        public String getDataUrl() { return REMOTE_DATA_URL; }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}
