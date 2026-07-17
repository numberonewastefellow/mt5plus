import Foundation

/// Every call returns this instead of throwing, so the UI always has something to render and a
/// failure can never be swallowed by a missing catch. Ported from Android's net/Api.kt ApiResult.
enum ApiResult<T> {
    case ok(T)

    /// `retcode`/`comment` are populated only for /order, which alone returns them.
    case failed(message: String, retcode: Int? = nil, comment: String? = nil)

    /// HTTP 401 — the token is wrong. The UI must send the user back to Connect.
    case unauthorized

    /// The call timed out client-side. THIS IS NOT A FAILURE — it is an UNKNOWN.
    ///
    /// The server has no timeout of its own: it awaits the worker future indefinitely, serialised
    /// behind a 15 Hz poll loop. So a request that times out here may well still be executing — and,
    /// for /order, may well FILL. Collapsing that into `failed` is how you get a double position:
    /// the app says "order failed", the user taps BUY again, and the first order fills too. On
    /// XAUUSD at 1.0 lot that is a $100/point error nobody chose to take. Separate case precisely so
    /// every call site must decide what to do about it.
    case timedOut(what: String)
}

/// The handful of endpoints this app uses. No second HTTP library: URLSession serves both the
/// WebSocket feed and these calls (one shared session — see SessionProvider), exactly as Android
/// shares one OkHttpClient between TradingClient and Api.
struct Api {

    private let settings: AppSettings
    private let session: URLSession
    private let decoder: JSONDecoder

    init(settings: AppSettings = .shared, session: URLSession = SessionProvider.shared) {
        self.settings = settings
        self.session = session
        self.decoder = JSONDecoder()
        // Frames.swift declares explicit snake_case CodingKeys, so DO NOT also set
        // .convertFromSnakeCase — the two together would double-map and miss keys.
    }

    // MARK: Orders

    /// Market order. `slPoints`/`tpPoints` are POINT DISTANCES, not prices — the server's
    /// `_place_order` hardcodes sl_tp_mode="points" and converts them itself. 0 means "no stop".
    /// Passing an absolute price would silently place a stop miles from market (accepted, not
    /// rejected — which is what makes it dangerous).
    ///
    /// Two fields are deliberately NEVER sent: `auto_test` (demo-only; server 403s it on a live
    /// account) and `symbol` (the server is single-symbol and ignores it).
    func order(side: String, volume: Double, slPoints: Double, tpPoints: Double) async -> ApiResult<OrderResult> {
        await post("/order",
                   body: ["side": side, "type": "market", "volume": volume, "sl": slPoints, "tp": tpPoints],
                   timeout: SessionProvider.tradeTimeout)
    }

    func close(ticket: Int64) async -> ApiResult<CloseResult> {
        await post("/close", body: ["ticket": ticket], timeout: SessionProvider.tradeTimeout)
    }

    /// Bulk close. `filter` is "all" | "losing" | "profit".
    ///
    /// ── A FAILED BULK CLOSE COMES BACK AS HTTP 200. THE BODY IS THE ONLY TRUTH. ──
    /// `_close_where` gives up after 5 retry passes and returns {ok:false, closed:0, remaining:8};
    /// a disconnected terminal returns {ok:false, error:"terminal not connected"}. Both are 200.
    /// Trusting the status means an emergency CLOSE ALL that shut NOTHING reports success — in
    /// green, on a losing book. So `ok == false` is mapped to `.failed`, and the caller can't ignore it.
    func closeWhere(filter: String) async -> ApiResult<CloseResult> {
        let r: ApiResult<CloseResult> = await post("/close_where", body: ["filter": filter],
                                                   timeout: SessionProvider.tradeTimeout)
        if case .ok(let v) = r, !v.ok {
            return .failed(message: v.error ?? "\(v.remaining) position(s) STILL OPEN after closing \(v.closed)")
        }
        return r
    }

    /// Enable/disable or retune one server-side strategy engine. Returns the engine's REAL status,
    /// which is not the same as "the request succeeded": the server can answer 200 with
    /// enabled:false and a reason. Render what comes back, never what was asked for.
    func setStrategy(id: String, enabled: Bool?, params: [String: Any] = [:]) async -> ApiResult<StrategyStatus> {
        var body = params
        if let enabled { body["enabled"] = enabled }
        return await post("/api/strategy/\(id)", body: body, timeout: SessionProvider.tradeTimeout)
    }

    /// Arm/disarm/retune the account P&L guard (server-enforced auto-close-all at a target). The
    /// server VALIDATES and is the authority; a bad value comes back as 400 -> `.failed`.
    func setGuard(enabled: Bool?, targetPl: Double?, side: String?) async -> ApiResult<Guard> {
        var body: [String: Any] = [:]
        if let enabled { body["enabled"] = enabled }
        if let targetPl { body["target_pl"] = targetPl }
        if let side { body["side"] = side }
        let r: ApiResult<GuardResp> = await post("/api/guard", body: body, timeout: SessionProvider.tradeTimeout)
        switch r {
        case .ok(let v): return .ok(v.guard ?? Guard())
        case .failed(let m, let rc, let c): return .failed(message: m, retcode: rc, comment: c)
        case .unauthorized: return .unauthorized
        case .timedOut(let w): return .timedOut(what: w)
        }
    }

    // MARK: Session / read

    /// Unauthenticated probe: reports THAT a token is required, never what it is.
    func config() async -> ApiResult<ServerConfig> { await get("/api/config") }

    func state() async -> ApiResult<Snapshot> { await get("/api/state") }

    func accounts() async -> ApiResult<AccountsResponse> { await get("/api/accounts") }

    /// Today's trading activity. On the 60 s trade budget: history_deals_get over a heavy scalping
    /// day can take real time.
    func history() async -> ApiResult<HistoryResponse> { await get("/api/history", timeout: SessionProvider.tradeTimeout) }

    /// Log the terminal into a SAVED profile. The broker password is never sent from the phone —
    /// the server reads it from its own vault.
    func login(profileId: String) async -> ApiResult<LoginResult> {
        await post("/api/login", body: ["profile_id": profileId], timeout: SessionProvider.tradeTimeout)
    }

    /// Log in with typed credentials, optionally saving them. The ONLY call that carries a broker
    /// password — sent once; with save=true the server vaults it. Uses the 60 s budget (a cold MT5
    /// start can launch terminal64.exe). A timeout here is an UNKNOWN, not a failure.
    func loginWith(login: Int64, password: String, server: String, save: Bool, label: String?) async -> ApiResult<LoginResult> {
        var body: [String: Any] = ["login": login, "password": password, "server": server, "save": save]
        if let label, !label.trimmed().isEmpty { body["label"] = label }
        return await post("/api/login", body: body, timeout: SessionProvider.tradeTimeout)
    }

    /// Forget a saved profile. The id (`<login>@<server>`) is PERCENT-ENCODED as a path segment,
    /// not interpolated raw: a `#`/`?`/`/` in the server name would otherwise truncate the path and
    /// the row could never be removed.
    func deleteAccount(profileId: String) async -> ApiResult<DeleteResult> {
        // Percent-encode the id as ONE path segment — crucially including '/', which .urlPathAllowed
        // would leave intact and which would otherwise split the path and hit a truncated route.
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove("/")
        let seg = profileId.addingPercentEncoding(withAllowedCharacters: allowed) ?? profileId
        guard let url = URL(string: settings.baseURL + "/api/accounts/" + seg) else {
            return .failed(message: "bad URL")
        }
        return await execute(makeRequest(url: url, method: "DELETE", body: nil, timeout: SessionProvider.defaultTimeout),
                             path: "/api/accounts/\(profileId)")
    }

    /// Stop driving the terminal. NOT a broker logout — positions STAY OPEN; `prevOpen` says so.
    func logout() async -> ApiResult<LogoutResult> {
        await post("/api/logout", body: [:], timeout: SessionProvider.tradeTimeout)
    }

    // MARK: Plumbing

    private func get<T: Decodable>(_ path: String, timeout: TimeInterval = SessionProvider.defaultTimeout) async -> ApiResult<T> {
        guard let url = URL(string: settings.baseURL + path) else { return .failed(message: "bad URL") }
        return await execute(makeRequest(url: url, method: "GET", body: nil, timeout: timeout), path: path)
    }

    private func post<T: Decodable>(_ path: String, body: [String: Any], timeout: TimeInterval) async -> ApiResult<T> {
        guard let url = URL(string: settings.baseURL + path) else { return .failed(message: "bad URL") }
        let data = try? JSONSerialization.data(withJSONObject: body)
        return await execute(makeRequest(url: url, method: "POST", body: data, timeout: timeout), path: path)
    }

    private func makeRequest(url: URL, method: String, body: Data?, timeout: TimeInterval) -> URLRequest {
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = timeout
        // Read the token at SEND time, not at construction: it can change under us when the user
        // re-enters it, and a retry must use the new one. Header (not ?token=) keeps it out of logs.
        let tok = settings.token.trimmed()
        if !tok.isEmpty { req.setValue(tok, forHTTPHeaderField: "x-token") }
        if let body {
            req.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }
        return req
    }

    private func execute<T: Decodable>(_ request: URLRequest, path: String) async -> ApiResult<T> {
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                return .failed(message: "no HTTP response")
            }
            if http.statusCode == 401 { return .unauthorized }
            if (200..<300).contains(http.statusCode) {
                do { return .ok(try decoder.decode(T.self, from: data)) }
                catch { return .failed(message: "could not parse server response: \(error.localizedDescription)") }
            }
            return parseError(code: http.statusCode, data: data)
        } catch let err as URLError where err.code == .timedOut {
            // We gave up waiting; the SERVER did not give up working. Never report as a failure.
            return .timedOut(what: path)
        } catch {
            return .failed(message: error.localizedDescription)
        }
    }

    /// The server's error shapes are INCONSISTENT, and all land here:
    ///   /order       -> 400, `detail` is an OBJECT {message, retcode, comment}
    ///   /close, etc. -> 400, `detail` is a STRING
    ///   422          -> `detail` is an ARRAY of {loc, msg, type} (FastAPI/Pydantic validation)
    ///   403          -> plain string (the auto_test live-account guard; we never send that flag)
    /// Assuming one shape throws on the others — which is how a 422 became an opaque
    /// "request failed (HTTP 422)" with no field info. Handle all three.
    private func parseError<T>(code: Int, data: Data) -> ApiResult<T> {
        let fallback = "request failed (HTTP \(code))"
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = root["detail"] else {
            return .failed(message: fallback)
        }

        // detail: String
        if let s = detail as? String { return .failed(message: s) }

        // detail: [ {loc, msg, type}, ... ]  (Pydantic validation)
        if let arr = detail as? [[String: Any]], let first = arr.first {
            let msg = (first["msg"] as? String) ?? fallback
            let loc = (first["loc"] as? [Any])?.compactMap { $0 as? String }
            let field = loc?.last { $0 != "body" }
            return .failed(message: field != nil ? "\(field!): \(msg)" : msg)
        }

        // detail: { message, retcode, comment }  (/order)
        if let obj = detail as? [String: Any] {
            return .failed(message: (obj["message"] as? String) ?? fallback,
                           retcode: obj["retcode"] as? Int,
                           comment: obj["comment"] as? String)
        }

        return .failed(message: fallback)
    }
}
