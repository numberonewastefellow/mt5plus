import Foundation

// Wire models for the XauOrderPad server. Ported 1:1 from the Android client's
// net/Frames.kt — that file is the source of truth; keep them in sync.
//
// ── EVERY FIELD IS OPTIONAL-OR-DEFAULTED, TOLERANT OF MISSING KEYS. NOT DEFENSIVE STYLE. ──
//
// The server does not send a fixed schema. `Mt5Worker._poll_state` builds the dict
// incrementally and returns EARLY when the terminal is not connected, so a degraded frame
// contains only:
//
//     {"ts":…, "symbol":"XAUUSD", "connected":false, "healthy":false,
//      "logged_out":true, "error":"logged out", "strategy":{…}}
//
// `account`, `positions`, `bid`, `ask`, `digits`, `point`, `volume_min` … are ABSENT --
// not null, absent. Decoding a degraded frame must succeed, or the feed freezes on the last
// good snapshot the first time the box restarts, someone logs out, or the broker blips.
//
// Two Swift facts drive the shape below:
//  • Synthesized Decodable uses `decodeIfPresent` for OPTIONAL properties, so a missing key
//    yields nil and never throws -- so all-optional structs need no custom decoder.
//  • Synthesized Decodable IGNORES default values for NON-optional properties and throws
//    `keyNotFound` when the key is missing. So every struct that keeps non-optional defaulted
//    fields gets a custom `init(from:)` using `Dec.val`/`Dec.opt` below (mirrors the Android
//    parser's ignoreUnknownKeys + coerceInputValues + defaults). Encoding stays synthesized.
//
// Codable ignores unknown keys on decode by default, covering the forward direction: a field
// added to the server later must not break a phone that cannot be updated on the spot.

private extension KeyedDecodingContainer {
    /// Missing key OR wrong type -> `def`. `decodeIfPresent` returns nil for a missing key and
    /// throws on a type mismatch; `try?` folds the throw into nil too, so both degrade to `def`.
    func val<T: Decodable>(_ key: Key, _ def: T) -> T {
        ((try? decodeIfPresent(T.self, forKey: key)) ?? nil) ?? def
    }
    /// Optional variant: missing OR wrong type -> nil (never throws).
    func opt<T: Decodable>(_ key: Key) -> T? {
        (try? decodeIfPresent(T.self, forKey: key)) ?? nil
    }
}

// MARK: - Snapshot (the /ws frame, and GET /api/state) — all-optional, synthesized decode

struct Snapshot: Codable, Equatable {
    var ts: Double?
    var symbol: String?

    var connected: Bool?
    var healthy: Bool?
    var tradeAllowed: Bool?
    var symbolOk: Bool?
    /// Present ONLY when true. Its absence does not mean "logged in".
    var loggedOut: Bool?
    var error: String?

    var digits: Int?
    var point: Double?
    var volumeMin: Double?
    var volumeStep: Double?

    var bid: Double?
    var ask: Double?
    /// CAUTION: a PRICE DIFFERENCE (ask - bid), not points. Divide by `point` to show it the
    /// way a trader reads it -- rendering it raw prints "0.22" where the user expects "22".
    /// Use `spreadPoints`.
    var spread: Double?
    var tickTime: Int64?

    var account: Account?
    var positions: [Position]?
    var orders: [PendingOrder]?

    var netLots: Double?
    /// Server-side sum of positions[].profit. Broker-computed. Never recompute.
    var floatingPl: Double?

    /// Status of every server-side strategy engine, keyed by id. Rides the /ws snapshot
    /// rather than a REST poll. The strategies RUN on the server; the phone only watches/toggles.
    var strategies: [String: StrategyStatus]?

    /// The account P&L guard the SERVER is enforcing (auto-close-all at a target). See `Guard`.
    /// (`guard` is a Swift keyword, so the property is backtick-escaped.)
    var `guard`: Guard?

    enum CodingKeys: String, CodingKey {
        case ts, symbol, connected, healthy, error, digits, point, bid, ask, spread
        case account, positions, orders, strategies, `guard`
        case tradeAllowed = "trade_allowed"
        case symbolOk = "symbol_ok"
        case loggedOut = "logged_out"
        case volumeMin = "volume_min"
        case volumeStep = "volume_step"
        case tickTime = "tick_time"
        case netLots = "net_lots"
        case floatingPl = "floating_pl"
    }

    /// Spread in points -- the unit the UI shows. Nil when either half is missing.
    var spreadPoints: Double? {
        guard let s = spread, let p = point, p > 0 else { return nil }
        return s / p
    }

    var openPositions: [Position] { positions ?? [] }

    /// True only when the server explicitly said so. Absence != logged in.
    var isLoggedOut: Bool { loggedOut == true }
}

// MARK: - Strategy

/// One server-side strategy engine, as the server reports it. Only the fields the phone
/// RENDERS — the phone is a remote control, not a config editor. `error`/`warning` are
/// computed server-side from real measurements and must be shown, never swallowed.
/// `enabled` is the SERVER's answer: a POST can return 200 and `enabled:false` because the
/// engine refused. Trust this, not the request that was sent.
struct StrategyStatus: Codable, Equatable {
    var id: String = ""
    var name: String = ""
    var enabled: Bool = false
    var state: String = ""
    var error: String?
    var killed: Bool = false
    // ladder-only; nil on engines that do not have them
    var paper: Bool?
    var spread: Double?
    var warning: String?
    var params: StrategyParams?

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.val(.id, "")
        name = c.val(.name, "")
        enabled = c.val(.enabled, false)
        state = c.val(.state, "")
        error = c.opt(.error)
        killed = c.val(.killed, false)
        paper = c.opt(.paper)
        spread = c.opt(.spread)
        warning = c.opt(.warning)
        params = c.opt(.params)
    }
}

struct StrategyParams: Codable, Equatable {
    var side: String?
    var trigger: Double?
    var target: Double?
    var retrace: Double?
    var volume: Double?
    var maxPositions: Int?
    var paper: Bool?

    enum CodingKeys: String, CodingKey {
        case side, trigger, target, retrace, volume, paper
        case maxPositions = "max_positions"
    }
}

// MARK: - Guard

/// The account-level P&L guard, as the SERVER reports it (rides the /ws snapshot). The worker
/// enforces it — auto-closes the whole book when FLOATING P&L reaches `targetPl` — so this is
/// authoritative; the phone reflects it. `fired` latches for one breach, clears when flat.
struct Guard: Codable, Equatable {
    var enabled: Bool = false
    var targetPl: Double = 0
    /// "profit" (close at >= +target) | "loss" (close at <= -target).
    var side: String = "profit"
    var fired: Bool = false

    enum CodingKeys: String, CodingKey {
        case enabled, side, fired
        case targetPl = "target_pl"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = c.val(.enabled, false)
        targetPl = c.val(.targetPl, 0)
        side = c.val(.side, "profit")
        fired = c.val(.fired, false)
    }
}

/// The /api/guard POST response: `{"ok":true,"guard":{…}}`.
struct GuardResp: Codable, Equatable {
    var ok: Bool = false
    var `guard`: Guard?

    enum CodingKeys: String, CodingKey { case ok, `guard` }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.val(.ok, false)
        `guard` = c.opt(.`guard`)
    }
}

// MARK: - Account (rides the snapshot; all-optional, synthesized decode)

struct Account: Codable, Equatable {
    var balance: Double?
    var equity: Double?
    var currency: String?
    var login: Int64?
    var server: String?
    /// MT5 enum: 0=demo 1=contest 2=real
    var tradeMode: Int?
    var isDemo: Bool?
    /// 0=netting 2=hedging
    var marginMode: Int?
    var dailyRealized: Double?
    var wins: Int?
    var losses: Int?

    enum CodingKeys: String, CodingKey {
        case balance, equity, currency, login, server, wins, losses
        case tradeMode = "trade_mode"
        case isDemo = "is_demo"
        case marginMode = "margin_mode"
        case dailyRealized = "daily_realized"
    }
    // NOTE: margin / margin_free / margin_level are NOT exposed by the server (_poll_state
    // copies only the fields above). Adding them here yields nils.
}

// MARK: - Position / PendingOrder (ride the snapshot; only `ticket` is non-optional)

struct Position: Codable, Equatable, Identifiable {
    var ticket: Int64                    // the one field the server always sends
    var side: String?                    // "BUY" | "SELL" (uppercase)
    var volume: Double?
    var priceOpen: Double?
    /// ABSOLUTE PRICE here (0.0 = none) -- the OPPOSITE of the order REQUEST, where sl/tp are
    /// point distances. Same field names, different units, in the same app.
    var sl: Double?
    var tp: Double?
    /// Broker-computed floating P&L in account currency. NEVER recompute this.
    var profit: Double?
    var time: Int64?
    /// Int64, NOT Int32. The MT5 magic is a **uint32**, and mt5_worker.py sends it raw. Any EA
    /// or copier using a magic above 2^31 (timestamps and hashes routinely are) would overflow
    /// a 32-bit type and make the WHOLE FRAME fail to decode -- the feed then freezes on the
    /// last good snapshot, presenting a dead book as live. That is the worst failure here.
    var magic: Int64?
    /// Present on the account-wide history view (many symbols); absent/nil on the live poll.
    var symbol: String?

    var id: Int64 { ticket }

    enum CodingKeys: String, CodingKey {
        case ticket, side, volume, sl, tp, profit, time, magic, symbol
        case priceOpen = "price_open"
    }

    var isBuy: Bool { side?.caseInsensitiveCompare("BUY") == .orderedSame }
    var hasSl: Bool { (sl ?? 0) > 0 }
    var hasTp: Bool { (tp ?? 0) > 0 }
}

struct PendingOrder: Codable, Equatable, Identifiable {
    var ticket: Int64
    var side: String?
    var volume: Double?
    var priceOpen: Double?
    var sl: Double?
    var tp: Double?
    var type: String?
    var time: Int64?
    var symbol: String?

    var id: Int64 { ticket }

    enum CodingKeys: String, CodingKey {
        case ticket, side, volume, sl, tp, type, time, symbol
        case priceOpen = "price_open"
    }

    var isBuy: Bool { side?.caseInsensitiveCompare("BUY") == .orderedSame }
}

// MARK: - Config / accounts / history

/// GET /api/config -- unauthenticated probe. Says THAT a token is needed, never what.
struct ServerConfig: Codable, Equatable {
    var authRequired: Bool = false
    var pollHz: Int = 15

    enum CodingKeys: String, CodingKey {
        case authRequired = "auth_required"
        case pollHz = "poll_hz"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        authRequired = c.val(.authRequired, false)
        pollHz = c.val(.pollHz, 15)
    }
}

struct AccountsResponse: Codable, Equatable {
    var accounts: [Profile] = []

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        accounts = c.val(.accounts, [])
    }
}

/// GET /api/history -- today's trading activity for the logged-in account (broker-side, on
/// demand). All fields defaulted-tolerant: a partial/failed body must still decode rather than
/// blank the screen.
struct HistoryStats: Codable, Equatable {
    var closedCount: Int = 0
    var wins: Int = 0
    var losses: Int = 0
    var flat: Int = 0
    var grossProfit: Double = 0
    var grossLoss: Double = 0
    var net: Double = 0
    var biggestWin: Double = 0
    var biggestLoss: Double = 0
    var avgWin: Double = 0
    var avgLoss: Double = 0
    /// wins / closed_count, 0..1 (matches the broker's "81%").
    var winRate: Double = 0

    enum CodingKeys: String, CodingKey {
        case wins, losses, flat, net
        case closedCount = "closed_count"
        case grossProfit = "gross_profit"
        case grossLoss = "gross_loss"
        case biggestWin = "biggest_win"
        case biggestLoss = "biggest_loss"
        case avgWin = "avg_win"
        case avgLoss = "avg_loss"
        case winRate = "win_rate"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        closedCount = c.val(.closedCount, 0)
        wins = c.val(.wins, 0)
        losses = c.val(.losses, 0)
        flat = c.val(.flat, 0)
        grossProfit = c.val(.grossProfit, 0)
        grossLoss = c.val(.grossLoss, 0)
        net = c.val(.net, 0)
        biggestWin = c.val(.biggestWin, 0)
        biggestLoss = c.val(.biggestLoss, 0)
        avgWin = c.val(.avgWin, 0)
        avgLoss = c.val(.avgLoss, 0)
        winRate = c.val(.winRate, 0)
    }
}

/// One closed trade: a closing deal paired with its opening deal (entry -> exit).
struct ClosedTrade: Codable, Equatable, Identifiable {
    var positionId: Int64 = 0
    var ticket: Int64 = 0
    var side: String?                    // the POSITION's direction, "BUY" | "SELL"
    var symbol: String?
    var volume: Double?
    /// nil when the position was opened BEFORE today (no IN leg in range) -> render "-> exit".
    var entryPrice: Double?
    var exitPrice: Double?
    var entryTime: Int64?
    var exitTime: Int64?
    /// profit + swap + commission of the closing leg, account currency.
    var pnl: Double = 0

    var id: Int64 { ticket }

    enum CodingKeys: String, CodingKey {
        case ticket, side, symbol, volume, pnl
        case positionId = "position_id"
        case entryPrice = "entry_price"
        case exitPrice = "exit_price"
        case entryTime = "entry_time"
        case exitTime = "exit_time"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        positionId = c.val(.positionId, 0)
        ticket = c.val(.ticket, 0)
        side = c.opt(.side)
        symbol = c.opt(.symbol)
        volume = c.opt(.volume)
        entryPrice = c.opt(.entryPrice)
        exitPrice = c.opt(.exitPrice)
        entryTime = c.opt(.entryTime)
        exitTime = c.opt(.exitTime)
        pnl = c.val(.pnl, 0)
    }

    var isBuy: Bool { side?.caseInsensitiveCompare("BUY") == .orderedSame }
}

struct HistoryResponse: Codable, Equatable {
    var ok: Bool = false
    var asOf: Int64 = 0
    var dayStart: Int64 = 0
    var stats: HistoryStats = HistoryStats()
    var closed: [ClosedTrade] = []
    var open: [Position] = []
    var pending: [PendingOrder] = []
    var error: String?

    enum CodingKeys: String, CodingKey {
        case ok, stats, closed, open, pending, error
        case asOf = "as_of"
        case dayStart = "day_start"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.val(.ok, false)
        asOf = c.val(.asOf, 0)
        dayStart = c.val(.dayStart, 0)
        stats = c.val(.stats, HistoryStats())
        closed = c.val(.closed, [])
        open = c.val(.open, [])
        pending = c.val(.pending, [])
        error = c.opt(.error)
    }
}

struct Profile: Codable, Equatable, Identifiable {
    var id: String                       // "<login>@<server>"
    var label: String?
    var login: Int64?
    var server: String?
    var lastTradeMode: Int?

    enum CodingKeys: String, CodingKey {
        case id, label, login, server
        case lastTradeMode = "last_trade_mode"
    }
}

struct LoginResult: Codable, Equatable {
    var ok: Bool = false
    var login: Int64?
    var server: String?
    /// 0 = demo, 1 = contest, 2 = REAL. Keep the raw value: "not demo" and "real money" want
    /// to be distinguishable in a log after the fact.
    var tradeMode: Int?
    var isDemo: Bool?
    /// Positions left open on the PREVIOUS account when switching. Must be surfaced.
    var prevOpen: Int?
    /// Set ONLY when the client asked to save (ad-hoc login with save=true); nil otherwise.
    /// `false` means the login worked but the password was NOT stored on the server -- so this
    /// account cannot be auto-restored, and the user must be told plainly.
    var saved: Bool?
    var saveError: String?

    enum CodingKeys: String, CodingKey {
        case ok, login, server, saved
        case tradeMode = "trade_mode"
        case isDemo = "is_demo"
        case prevOpen = "prev_open"
        case saveError = "save_error"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.val(.ok, false)
        login = c.opt(.login)
        server = c.opt(.server)
        tradeMode = c.opt(.tradeMode)
        isDemo = c.opt(.isDemo)
        prevOpen = c.opt(.prevOpen)
        saved = c.opt(.saved)
        saveError = c.opt(.saveError)
    }
}

/// POST /api/logout. MT5 has no true "log out" -- the terminal stays logged in; the server
/// simply refuses to drive it. So `prevOpen` is not cosmetic: those positions are STILL OPEN
/// on the account, now with nothing watching them. The UI must say so.
struct LogoutResult: Codable, Equatable {
    var ok: Bool = false
    var prevOpen: Int?

    enum CodingKeys: String, CodingKey {
        case ok
        case prevOpen = "prev_open"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.val(.ok, false)
        prevOpen = c.opt(.prevOpen)
    }
}

/// DELETE /api/accounts/{id}. `deleted=false` means the id was not there -- the server still
/// answers 200. `active=true` means we just forgot the password for the account the terminal
/// is CURRENTLY logged into: it keeps trading, but the session can no longer be restored if a
/// later login fails. The UI must say so.
struct DeleteResult: Codable, Equatable {
    var deleted: Bool = false
    var active: Bool = false

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        deleted = c.val(.deleted, false)
        active = c.val(.active, false)
    }
}

/// POST /order response. All-optional -> synthesized decode.
struct OrderResult: Codable, Equatable {
    var ticket: Int64?
    var price: Double?
    var state: String?                   // "open" | "pending"
}

/// POST /close_where and POST /close_all.
///
/// ─────────────────────────────────────────────────────────────────────────────
///  A FAILED BULK CLOSE COMES BACK AS HTTP 200. THE BODY IS THE ONLY TRUTH.
/// ─────────────────────────────────────────────────────────────────────────────
/// `_close_where` gives up after 5 retry passes and returns {ok:false, closed:0, remaining:8};
/// a disconnected terminal returns {ok:false, error:"terminal not connected"} with `closed`/
/// `remaining` ABSENT. Both are 200, because both are well-formed answers rather than protocol
/// errors -- which is exactly why `closed`/`remaining` are defaulted-tolerant here (a missing
/// key must not throw). Trusting the status means an emergency CLOSE ALL that shut NOTHING
/// reports success. See Api.closeWhere.
struct CloseResult: Codable, Equatable {
    var ok: Bool = false
    var filter: String?
    var closed: Int = 0
    /// Positions still matching the FILTER -- not the whole book. After filter="losing", a
    /// surviving winner is not a failure and is not counted here.
    var remaining: Int = 0
    var error: String?

    enum CodingKeys: String, CodingKey { case ok, filter, closed, remaining, error }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.val(.ok, false)
        filter = c.opt(.filter)
        closed = c.val(.closed, 0)
        remaining = c.val(.remaining, 0)
        error = c.opt(.error)
    }
}
