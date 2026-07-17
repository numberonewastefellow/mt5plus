import Foundation

/// The single app-scoped `URLSession` shared by the REST client and the WebSocket feed — the Swift
/// counterpart of Android's shared OkHttpClient in data/Feed.kt (one connection pool, one delegate).
///
/// Unlike Android — where the SSLSocketFactory is baked into the client and the client must be
/// rebuilt when a certificate is uploaded — our `TradingURLSessionDelegate` reads the current certs
/// live on each TLS challenge. So ONE session serves the whole lifetime and picks up cert changes
/// automatically; there is nothing to rebuild.
enum SessionProvider {

    /// Default per-request timeout for light calls (config/state/accounts). Trade/login/history
    /// calls override this to `tradeTimeout` per request — the server has no timeout of its own and
    /// a close storm or a cold MT5 start can outlast the short budget.
    static let defaultTimeout: TimeInterval = 15
    static let tradeTimeout: TimeInterval = 60

    static let shared: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = defaultTimeout
        // We drive reconnect/backoff ourselves (mirrors Android's network-available collapse), so
        // don't let URLSession silently park requests waiting for connectivity.
        config.waitsForConnectivity = false
        config.httpMaximumConnectionsPerHost = 6      // ~OkHttp's 5-in-flight/host for scalping bursts
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.httpShouldUsePipelining = true

        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 4
        queue.name = "xau.urlsession.delegate"

        return URLSession(configuration: config,
                          delegate: TradingURLSessionDelegate(),
                          delegateQueue: queue)
    }()
}
