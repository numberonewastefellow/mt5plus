package com.xauorderpad.data

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * The built-in list of MT5 broker servers offered as a dropdown when adding an account, plus the
 * one default account the app ships pre-filled.
 *
 * ── Why a JSON asset ──
 * Server names must be typed CHARACTER-FOR-CHARACTER ("Exness-MT5Trial16", "VTMarkets-Live 2" -- a
 * "Trail"/"Trial" swap or a missing space is a silent -6 at the broker). A curated dropdown removes
 * the typing. It lives in `assets/servers.json` so the list is edited without touching code.
 *
 * ── Custom servers ──
 * The dropdown always offers "Custom…" so a server not in the list can still be typed. A name typed
 * that way is remembered in prefs and reappears in the dropdown next time -- so it is a one-time typo
 * risk, not an every-login one.
 */
object ServerCatalog {

    @Serializable
    data class ServerEntry(val name: String, val broker: String = "")

    @Serializable
    data class DefaultAccount(val login: Long, val server: String, val label: String = "")

    @Serializable
    data class Catalog(
        val servers: List<ServerEntry> = emptyList(),
        val defaultAccount: DefaultAccount? = null,
    )

    private const val ASSET = "servers.json"
    private const val PREFS = "xau_servers"
    private const val KEY_CUSTOM = "custom_servers"

    private val json = Json { ignoreUnknownKeys = true }

    // The asset never changes at runtime, so parse it once.
    @Volatile
    private var cached: Catalog? = null

    /** The bundled catalog. Returns an empty catalog (no crash) if the asset is missing/unparseable. */
    fun load(context: Context): Catalog {
        cached?.let { return it }
        val parsed = try {
            val text = context.applicationContext.assets.open(ASSET).use { it.readBytes() }
                .toString(Charsets.UTF_8)
            json.decodeFromString<Catalog>(text)
        } catch (_: Exception) {
            Catalog()
        }
        cached = parsed
        return parsed
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Server names the user has typed via "Custom…", newest last. */
    fun customServers(context: Context): List<String> {
        val raw = prefs(context).getString(KEY_CUSTOM, null) ?: return emptyList()
        return try {
            json.decodeFromString<List<String>>(raw)
        } catch (_: Exception) {
            emptyList()
        }
    }

    /** Remember a user-typed server so it shows in the dropdown next time. No-op if already known. */
    fun addCustomServer(context: Context, name: String) {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return
        val known = load(context).servers.map { it.name }.toSet()
        if (trimmed in known) return                       // already a built-in
        val current = customServers(context)
        if (trimmed in current) return
        val updated = current + trimmed
        prefs(context).edit().putString(KEY_CUSTOM, json.encodeToString(updated)).apply()
    }

    /**
     * The full dropdown list: built-in servers first, then remembered customs, de-duplicated while
     * preserving order.
     */
    fun serverNames(context: Context): List<String> {
        val builtin = load(context).servers.map { it.name }
        val customs = customServers(context)
        return (builtin + customs).distinct()
    }
}
