package com.xauorderpad.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.RiderCard
import com.xauorderpad.net.StrategyStatus
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlin.math.abs

/**
 * The QUICK STRATEGY PANEL: act on an engine without leaving the trade screen.
 *
 * ── Why this exists ──
 * Reaching the ladder used to be ॐ -> Settings -> Strategies -> ladder -> edit -> APPLY -> arm
 * -> confirm, then two backs. The rider was worse: the same walk to reach a suggestion that
 * EXPIRES WITH ITS M5 BAR -- the page even force-closes its own confirm dialog when the card
 * changes underneath you. Six taps to reach a perishable decision is the bug.
 *
 * Worse than the tap count: Settings HIDES THE PRICE. You set a trigger blind and come back to
 * find the market moved. Everything here exists to keep price and trigger on screen together.
 *
 * ── Shape, and why it is not something else ──
 *  * ONE ⚡ icon in the top bar (next to the ॐ), no rail row. In the stacked layouts the positions
 *    grid is the only weight(1f) child, so any fixed row would steal height straight from it -- and
 *    that chrome was already trimmed once to feed the grid. An icon costs nothing.
 *  * An OVERLAY, not an inserted row: price, BUY/SELL and the CLOSE row never move. Position
 *    carries the function on this screen; muscle memory on CLOSE ALL outranks this panel.
 *  * NO horizontal drag gesture. Two hard collisions: `SwipeToDismissBox` in the positions list
 *    already owns horizontal swipe (EndToStart CLOSES A POSITION), and the screen edges belong to
 *    the system back gesture. Tap targets only.
 *  * TABS, not one panel per engine -- that is how the straddle gets added later with no new chrome.
 */
enum class QuickTab { LADDER, SLADDER, RIDER }

fun ladderOf(s: StrategiesUi): StrategyStatus? = s.items.firstOrNull { it.id == "ladder" }
fun sladderOf(s: StrategiesUi): StrategyStatus? = s.items.firstOrNull { it.id == "sladder" }
fun riderOf(s: StrategiesUi): StrategyStatus? = s.items.firstOrNull { it.id == "rider" }

/** Is there a rider suggestion the operator could usefully act on RIGHT NOW? */
private fun riderHot(r: StrategyStatus?): Boolean =
    r != null && r.actionable == true && r.card?.isActionable == true

private fun fmtP(v: Double?, dp: Int = 2): String =
    if (v == null) "" else String.format("%.${dp}f", v)

/**
 * The top-bar entry point. Carries state so you never have to open the panel to learn anything:
 * grey idle, amber armed (paper), RED armed live, green a rider card is waiting -- and when the
 * ladder is armed it prints the DISTANCE TO TRIGGER inline, which is the number you would
 * otherwise be computing in your head.
 */
@Composable
fun QuickIcon(strategies: StrategiesUi, quote: Quote, onOpen: () -> Unit) {
    val ladder = ladderOf(strategies)
    val rider = riderOf(strategies)
    val armed = ladder?.enabled == true
    val livePaper = ladder?.paper == false
    val hot = riderHot(rider)

    val tint = when {
        armed && livePaper -> Red          // armed AND placing real orders: loudest state there is
        armed -> Amber
        hot -> Green
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    // Distance only means something once a level is actually set and we have a price.
    val trg = ladder?.params?.trigger?.takeIf { it > 0.0 }
    val px = quote.bid
    val dist = if (armed && trg != null && px != null) trg - px else null

    TextButton(onClick = onOpen, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp)) {
        Text("⚡", fontSize = 13.sp, color = tint)
        Spacer(Modifier.width(3.dp))
        Box(Modifier.size(6.dp).clip(RoundedCornerShape(3.dp)).background(tint))
        if (dist != null) {
            Spacer(Modifier.width(3.dp))
            Text(
                (if (dist >= 0) "+" else "−") + fmtP(abs(dist)),
                fontFamily = FontFamily.Monospace, fontSize = 9.sp,
                fontWeight = FontWeight.Bold, color = tint,
            )
        }
    }
}

/**
 * The panel itself. Two stages: PEEK carries only what is needed to act; one tap expands to the
 * rest. Peek keeps most of the positions grid visible, which matters -- the grid is what tells you
 * whether the last rung actually filled.
 */
@Composable
fun QuickPanel(
    strategies: StrategiesUi,
    quote: Quote,
    health: Health,
    live: Boolean,
    tab: QuickTab,
    expanded: Boolean,
    pinned: Boolean,
    onTab: (QuickTab) -> Unit,
    onToggleExpand: () -> Unit,
    onTogglePin: () -> Unit,
    onClose: () -> Unit,
    onSet: (String, Boolean?, JsonObject) -> Unit,
    onPlaceCard: (RiderCard, Double?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ladder = ladderOf(strategies)
    val sladder = sladderOf(strategies)
    val rider = riderOf(strategies)

    Column(
        modifier
            .fillMaxWidth()
            // HARD CEILING, and it does two jobs. In PEEK (the fast-trade path) it stays tight at
            // 330dp so the panel never rides up over the real BUY / SELL / CLOSE buttons -- the one
            // thing this design promised it would never do; a long server `warning` once inflated it
            // to ~600dp. EXPANDED is a deliberate "I am configuring, not watching" mode, so it may
            // grow to 430dp to bring the setup fields (the caps, and the top of the legend) above the
            // SET / ARM footer instead of below an invisible scroll. 430 was picked by MEASUREMENT on
            // device: 470 rode up over the bottom half of the real BUY / SELL buttons, 430 clears
            // them -- the reference legend below the caps simply scrolls.
            .heightIn(max = if (expanded) 430.dp else 330.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(14.dp, 14.dp, 0.dp, 0.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        // ---- header: tabs + expand + pin + close -------------------------------------------
        Row(verticalAlignment = Alignment.CenterVertically) {
            QuickTabChip("LADDER", tab == QuickTab.LADDER,
                dot = when {
                    ladder?.enabled == true && ladder.paper == false -> Red
                    ladder?.enabled == true -> Amber
                    else -> null
                }) { onTab(QuickTab.LADDER) }
            Spacer(Modifier.width(6.dp))
            // Straddle-ladder places real orders whenever armed (no paper mode), so armed is the
            // loud RED; a parked run that wants a new level shows Amber.
            QuickTabChip("STR·LAD", tab == QuickTab.SLADDER,
                dot = when {
                    sladder?.enabled == true -> Red
                    sladder?.needsAttention == true -> Amber
                    else -> null
                }) { onTab(QuickTab.SLADDER) }
            Spacer(Modifier.width(6.dp))
            QuickTabChip("RIDER", tab == QuickTab.RIDER,
                dot = if (riderHot(rider)) Green else null) { onTab(QuickTab.RIDER) }
            Spacer(Modifier.weight(1f))
            // Plain clickable glyphs, not TextButtons. `TextButton` enforces a 40dp minimum touch
            // height (plus the chip's own padding), so this header alone cost ~48dp of a 330dp
            // panel -- height stolen from the controls below it. Material's target guidance is
            // written for reaching, and this bar is already under the thumb.
            GlyphButton(if (expanded) "⌄" else "⌃", MaterialTheme.colorScheme.onSurface, onToggleExpand)
            GlyphButton("📌", if (pinned) Amber else MaterialTheme.colorScheme.onSurfaceVariant, onTogglePin)
            GlyphButton("✕", MaterialTheme.colorScheme.onSurface, onClose)
        }

        // A frozen screen must not arm anything: every write below is gated on `live`, the same
        // rule TradingViewModel.setStrategy enforces before it will send.
        if (!live) {
            Text("Feed is stale — controls are disabled until it recovers",
                color = Red, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 6.dp))
        }

        Spacer(Modifier.height(6.dp))
        // NOTE the scroll is NOT here. It used to wrap the whole tab body, which meant the panel
        // stayed a constant size but SET / ARM scrolled off the bottom once the four risk fields
        // were added -- the buttons were on screen but unreachable without a scroll nobody knew
        // was there. Each tab now scrolls its own INFORMATION and pins its ACTIONS below it, so
        // the thing you came here to press is always the thing you can see.
        when (tab) {
            QuickTab.LADDER -> LadderQuick(ladder, quote, health, live, expanded, onSet)
            QuickTab.SLADDER -> SladderQuick(sladder, quote, health, live, expanded, onSet)
            QuickTab.RIDER -> RiderQuick(rider, quote, health, live, expanded, onSet, onPlaceCard)
        }
    }
}

/**
 * A tab that is ~22dp tall instead of ~48dp.
 *
 * The old one nested a `TextButton` inside a padded `Row`: the button contributed its 40dp minimum
 * height and the Row added 8dp on top, so two words of label ate a seventh of the whole panel. The
 * clickable now lives on the Row itself.
 */
@Composable
private fun QuickTabChip(label: String, selected: Boolean, dot: Color?, onClick: () -> Unit) {
    val bg = if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.20f) else ChipBg
    Row(
        Modifier.clip(RoundedCornerShape(6.dp)).background(bg)
            .clickable(onClick = onClick)
            .padding(horizontal = 11.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, fontSize = 10.sp, fontWeight = FontWeight.Bold,
            color = if (selected) MaterialTheme.colorScheme.primary else ChipDim)
        if (dot != null) {
            Spacer(Modifier.width(4.dp))
            Box(Modifier.size(5.dp).clip(RoundedCornerShape(3.dp)).background(dot))
        }
    }
}

@Composable
private fun GlyphButton(glyph: String, tint: Color, onClick: () -> Unit) {
    Text(glyph, fontSize = 13.sp, color = tint,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).clickable(onClick = onClick)
            .padding(horizontal = 9.dp, vertical = 5.dp))
}

/* ============================== LADDER ============================== */

@Composable
private fun ColumnScope.LadderQuick(
    s: StrategyStatus?,
    quote: Quote,
    health: Health,
    live: Boolean,
    expanded: Boolean,
    onSet: (String, Boolean?, JsonObject) -> Unit,
) {
    if (s == null) {
        Text("No ladder engine on this server.", fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    val p = s.params

    // ── The draft-state rule ──
    // Snapshots arrive ~5x/sec. Keying each draft on the SERVER value means an unchanged snapshot
    // leaves the field alone (same key -> same remember), while a change made elsewhere re-seeds
    // it. Without this the trigger field is wiped under your finger mid-type. Same trick the
    // existing LadderTrigger uses.
    var side by remember(p?.side) { mutableStateOf(p?.side?.lowercase() ?: "buy") }
    var trg by remember(p?.trigger) { mutableStateOf(if ((p?.trigger ?: 0.0) > 0.0) fmtP(p?.trigger) else "") }
    var vol by remember(p?.volume) { mutableStateOf(fmtP(p?.volume)) }
    var tgt by remember(p?.target) { mutableStateOf(fmtP(p?.target)) }
    var rtr by remember(p?.retrace) { mutableStateOf(fmtP(p?.retrace)) }
    var hsl by remember(p?.hardSl) { mutableStateOf(fmtP(p?.hardSl)) }
    // The two pyramid CAPS. 0 = uncapped on both, so they format as "0" not blank. Integer count
    // for max_positions; $/oz-of-lots for max_lots.
    var mpos by remember(p?.maxPositions) { mutableStateOf((p?.maxPositions ?: 0).toString()) }
    var mlot by remember(p?.maxLots) { mutableStateOf(fmtP(p?.maxLots)) }
    // tact -- editable text bound to the ladder's `trail_activate` param, on the main LADDER tab.
    //   PURPOSE : let the operator choose WHEN the retrace trail starts working, without a Settings trip.
    //   MEANING : $/oz of profit the run must make before the trail arms. 0 = trail-from-entry (live at
    //             once, CAN close below entry at a loss); >= the trail (retrace) = activate-in-profit
    //             (arms at breakeven, never a directional loss -- only the broker SL protects until then).
    //   USED BY : seeded from the server's echoed value and re-seeded whenever it changes (the remember
    //             key); read back in sendSet and posted as `trail_activate` only when the operator edits it.
    // The engine owns the actual mechanic -- see strategies/ladder.py (LadderState).
    var tact by remember(p?.trailActivate) { mutableStateOf(fmtP(p?.trailActivate)) }

    var confirmArm by remember { mutableStateOf(false) }
    var confirmSet by remember { mutableStateOf(false) }

    val trgNum = trg.trim().toDoubleOrNull()
    // The ENTRY-side price -- ask for a buy, bid for a sell. This is the series `LadderState`
    // arms against (`mark`, ladder.py), so it is the only one whose "crossed" answer matches
    // what the engine will actually do. Reading bid for both under-reported a buy crossing by a
    // whole spread, which is the difference between "about to enter" and "not yet".
    val px = if (side == "buy") quote.ask else quote.bid
    // The APPLIED server level -- what ARM would actually act on. Deliberately NOT the draft: the
    // whole point of the "already crossed" warning is to describe what the engine will do, and the
    // engine knows nothing about text you have not sent yet.
    val applied = p?.trigger?.takeIf { it > 0.0 }
    val crossedBy = { lvl: Double -> if (side == "buy") px != null && px >= lvl else px != null && px <= lvl }
    val appliedCrossed = applied != null && crossedBy(applied)
    val draftCrossed = trgNum != null && trgNum > 0.0 && crossedBy(trgNum)
    val draftDist = if (trgNum != null && px != null) trgNum - px else null
    val paper = s.paper != false      // null (unknown) is treated as paper: fail safe

    // The server's spread guard, decided once server-side and rendered here rather than
    // re-derived. `guardOk == false` means the target or the trail is inside the live spread and
    // the engine WILL refuse to arm -- so ARM is disabled with the server's own sentence, instead
    // of posting and reading the refusal back. Default to allowing when the field is absent (an
    // engine or a server that does not send it), matching the old post-and-see behaviour.
    val guardOk = s.guardOk != false
    val minStop = s.minStop ?: s.spread
    // A dialled value is "inside the spread" -- the thing the guard refuses -- when it is <= the
    // floor. Compared against the DRAFT so the field reddens as you type, before you send.
    val insideSpread = { v: String -> minStop != null && (v.trim().toDoubleOrNull()?.let { it <= minStop } ?: false) }
    // Managing means new entries are blocked. "params_changed" is the routine case: a level typed
    // while trades are open is QUEUED, not dropped -- it goes live only once the book closes.
    val queuedBehindBook = s.managingReason == "params_changed"

    // The write itself, hoisted so the button and its confirmation dialog send BYTE-IDENTICAL
    // bodies. Two copies of this would be two things to keep in step, and the one that drifted
    // would be the one behind the confirmation -- the path that only runs when it matters.
    val sendSet: () -> Unit = {
        val body = buildJsonObject {
            put("side", JsonPrimitive(side))
            trgNum?.let { put("trigger", JsonPrimitive(it)) }
            // Only CHANGED values are sent. `_apply` treats any present key as an edit and,
            // mid-ladder, an edit hands the open book over to managing-only mode -- so echoing
            // back four unchanged numbers would stall a running ladder every time the level
            // was nudged.
            vol.trim().toDoubleOrNull()?.takeIf { it != p?.volume }
                ?.let { put("volume", JsonPrimitive(it)) }
            tgt.trim().toDoubleOrNull()?.takeIf { it != p?.target }
                ?.let { put("target", JsonPrimitive(it)) }
            rtr.trim().toDoubleOrNull()?.takeIf { it != p?.retrace }
                ?.let { put("retrace", JsonPrimitive(it)) }
            hsl.trim().toDoubleOrNull()?.takeIf { it != p?.hardSl }
                ?.let { put("hard_sl", JsonPrimitive(it)) }
            mpos.trim().toIntOrNull()?.takeIf { it != p?.maxPositions }
                ?.let { put("max_positions", JsonPrimitive(it)) }
            mlot.trim().toDoubleOrNull()?.takeIf { it != p?.maxLots }
                ?.let { put("max_lots", JsonPrimitive(it)) }
            tact.trim().toDoubleOrNull()?.takeIf { it != p?.trailActivate }
                ?.let { put("trail_activate", JsonPrimitive(it)) }
        }
        onSet(s.id, null, body)
    }

    // Everything above the action row scrolls; the action row does not. `fill = false` so a short
    // tab (an engine that is simply off) still collapses instead of holding the panel open.
    Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {

    // ---- side ----
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        SideChip("BUY", side == "buy", Green, Modifier.weight(1f), live) { side = "buy" }
        SideChip("SELL", side == "sell", Red, Modifier.weight(1f), live) { side = "sell" }
    }

    // ---- ONE row: trigger, lot, and the three risk numbers ----
    // These were four separate rows (trigger / steppers / distance / the rest), which is ~60dp of
    // captions and gaps for five numbers. The DISTANCE now rides in the trigger's caption instead
    // of owning a line: it belongs to the trigger, it is only ever read next to it, and putting it
    // there is what freed the room for SL and TP in the first place. Units are $/oz on TP, SL and
    // trail alike -- the same unit the engine uses -- and are spelled out under ⌃.
    Spacer(Modifier.height(6.dp))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        TinyField(
            "trigger", trg, live, Modifier.weight(1.75f), fontSize = 15.sp,
            hint = if (draftDist == null) null
            else (if (draftDist >= 0) "+" else "−") + fmtP(abs(draftDist)),
            // Red once the APPLIED level is behind price -- i.e. the engine would enter the
            // instant it were armed. Amber is "not there yet".
            hintColor = if (appliedCrossed) Red else Amber,
        ) { trg = it }
        TinyField("lot", vol, live, Modifier.weight(1f)) { vol = it }
        // TP and trail are the two the spread guard checks. Redden the moment the draft sits at or
        // inside the live spread -- the value the SERVER would refuse -- so it is caught while
        // typing, not after ARM bounces. SL (hard_sl) is the broker backstop and is not guarded.
        TinyField("TP", tgt, live, Modifier.weight(1f), warn = insideSpread(tgt)) { tgt = it }
        TinyField("SL", hsl, live, Modifier.weight(1f)) { hsl = it }
        TinyField("trail", rtr, live, Modifier.weight(1f), warn = insideSpread(rtr)) { rtr = it }
    }

    // ---- the live spread: the number TP and trail are judged against ----
    // This is why "trail 0.50 worked once and not the next time": the guard compares against the
    // spread AT THAT INSTANT, and gold's spread is not constant. Showing it here is the whole point
    // of the panel -- you were choosing TP and trail blind to the one number that decides them.
    if (minStop != null) {
        Spacer(Modifier.height(4.dp))
        Text("spread ${fmtP(minStop)} /oz — TP and trail must clear it",
            fontSize = 10.sp, fontWeight = FontWeight.Bold,
            color = if (!guardOk) Red else MaterialTheme.colorScheme.onSurfaceVariant)
    }

    // ---- seed the trigger from the live quote, then nudge it ----
    // @BID / @ASK write the current price straight into the trigger box. Typing seven digits while
    // the market moves is the actual bottleneck this panel exists to remove; you tap the side you
    // trade against, then walk it with the steppers. @ASK is the BUY entry side, @BID the SELL one
    // -- which is also the side the engine measures the crossing on.
    Spacer(Modifier.height(4.dp))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        SeedChip("@BID", Modifier.weight(1f), live && quote.bid != null) {
            quote.bid?.let { trg = fmtP(it) }
        }
        SeedChip("@ASK", Modifier.weight(1f), live && quote.ask != null) {
            quote.ask?.let { trg = fmtP(it) }
        }
        listOf(-1.0, -0.1, 0.1, 1.0).forEach { step ->
            StepChip(step, Modifier.weight(1f), live && trgNum != null) {
                trgNum?.let { trg = fmtP(it + step) }
            }
        }
    }

    // Two states that change what ARM means, kept on one line rather than two.
    if (appliedCrossed || paper) {
        Row(Modifier.padding(top = 4.dp)) {
            if (appliedCrossed) Text("ALREADY CROSSED — ARM enters at once", fontSize = 10.sp,
                fontWeight = FontWeight.Bold, color = Red)
            // The panel no longer OFFERS paper mode -- but if the server is still in it (a box set
            // that way to measure a trigger), ARM would place nothing at all. Say so, rather than
            // let the operator arm into silence.
            if (paper) Text((if (appliedCrossed) "  ·  " else "") + "SIMULATING — places nothing",
                fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Amber)
        }
    }
    // A new level typed while trades are open is QUEUED, not live -- the open book finishes on its
    // old rules first. Without this the operator sets a level, nothing happens, and it looks broken.
    if (queuedBehindBook) {
        Text("New level queued — no new entries until the ${s.openPositions ?: ""} open trade(s) close.",
            fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Amber,
            modifier = Modifier.padding(top = 4.dp))
    }
    // The guard's own sentence, shown BEFORE arming rather than after a refusal. `guardOk` also
    // disables the ARM button below, so this is the explanation that pairs with the greyed control.
    if (!guardOk && s.guardReason != null) {
        Text(s.guardReason, fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Red,
            maxLines = if (expanded) 6 else 3, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 4.dp))
    }
    // Server prose is UNBOUNDED (the ladder's target-vs-trail warning runs to four lines), so in
    // PEEK it is clamped and only opens up when expanded. An engine must not be able to push the
    // controls off screen by talking.
    Spacer(Modifier.height(5.dp))
    if (!s.state.isNullOrBlank()) {
        Text(s.state, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
    s.error?.let {
        Text(it, fontSize = 10.sp, color = Red, fontWeight = FontWeight.Bold,
            maxLines = if (expanded) 6 else 2, overflow = TextOverflow.Ellipsis)
    }
    s.warning?.let {
        Text(it, fontSize = 10.sp, color = Amber,
            maxLines = if (expanded) 8 else 2, overflow = TextOverflow.Ellipsis)
    }

    // ---- expanded: the pyramid CAPS, what's open, and what the numbers mean ----
    if (expanded) {
        // max_positions + max_lots -- the two ceilings on how far the ladder pyramids. Web-only
        // until now, which is the exact parity gap the repo rule warns about: a control on one
        // client and not the other. 0 = uncapped on BOTH. They are independent ceilings; whichever
        // binds first stops the pyramid, so you can cap by count, by size, by both, or by neither.
        Spacer(Modifier.height(7.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            TinyField("max rungs (0=∞)", mpos, live, Modifier.weight(1f)) { mpos = it }
            TinyField("max lots (0=∞)", mlot, live, Modifier.weight(1f)) { mlot = it }
        }
        // What the max-lots cap actually buys you, in the operator's own terms: at `volume` per
        // rung, a lots cap is a rung cap. "1 lot at 0.01 = 100 rungs" is the example the operator
        // asked about; compute it live so the number is theirs, not a guess.
        run {
            val v = vol.trim().toDoubleOrNull() ?: p?.volume ?: 0.0
            val ml = mlot.trim().toDoubleOrNull() ?: 0.0
            if (v > 0.0 && ml > 0.0) {
                Text("≈ ${(ml / v).toInt()} rungs at ${fmtP(v)} lots each",
                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 3.dp))
            }
        }
        // The one combination the engine flags as unbounded: no count cap AND no lots cap. On a
        // slow grind that accumulates rungs faster than the target drains them. Say it here, in the
        // same words as the server warning, before it is armed.
        if ((mpos.trim().toIntOrNull() ?: 0) == 0 && (mlot.trim().toDoubleOrNull() ?: 0.0) <= 0.0) {
            Text("Both caps 0 = UNBOUNDED — a slow grind can pile up rungs with nothing to stop it. " +
                "Set a lots cap before arming live.",
                fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Amber,
                modifier = Modifier.padding(top = 3.dp))
        }

        // Trail activation. 0 = trail is live from entry (can close BELOW entry on a dip); set it
        // >= the trail distance and the trail only arms once you are that far in profit (first stop
        // at breakeven -> it can never book a loss; the SL covers the downside until then).
        Spacer(Modifier.height(7.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            TinyField("trail after (+$/oz, 0=entry)", tact, live, Modifier.weight(1f)) { tact = it }
            Spacer(Modifier.weight(1f))
        }
        // trailArmed is the SERVER's derived flag (StrategyStatus.trail_armed) -- render it, do not
        // recompute. When trail_activate > 0 the trail is inert until the run is up by that much; this
        // Amber note explains the deliberate quiet (only the broker SL is live) so it is not read as a
        // broken trail. Shown only while a rung is open AND an activation distance is actually set.
        if (s.trailArmed == false && (p?.trailActivate ?: 0.0) > 0.0 && (s.openPositions ?: 0) > 0) {
            Text("trail waiting — not up +${fmtP(p?.trailActivate)} yet (only the SL is active)",
                fontSize = 10.sp, color = Amber, modifier = Modifier.padding(top = 3.dp))
        }

        // After a run ends: OFF = one-shot (park, set a new level to go again); ON = auto-continue
        // (keep taking runs while price stays past the level, stop when it returns). Sent on FLIP,
        // immediately -- a mode change is not a SET LEVEL and must not re-level the ladder.
        Spacer(Modifier.height(9.dp))
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Keep taking trades past the level", fontSize = 11.sp,
                    fontWeight = FontWeight.Bold)
                Text(
                    if (s.params?.autoContinue == true)
                        "ON — keeps going down the move; stops when price returns to your level."
                    else
                        "OFF — stops after each run; set a new level to go again.",
                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(
                checked = s.params?.autoContinue == true,
                enabled = live,
                onCheckedChange = { want ->
                    onSet(s.id, null, buildJsonObject { put("auto_continue", JsonPrimitive(want)) })
                },
            )
        }

        Spacer(Modifier.height(7.dp))
        s.openPositions?.let {
            Text("open rungs $it · ${fmtP(s.openLots)} lots" +
                (s.effectiveStop?.let { e -> " · effective stop ${fmtP(e)}" } ?: ""),
                fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        // The three exits are different mechanisms, and conflating them is what made "what is the
        // SL?" unanswerable. One line each: who holds it, what it covers, whether it moves.
        LegendLine("TP", "closes ONE trade at +TP profit. Engine-held, per trade.")
        LegendLine("trail", "closes the WHOLE ladder at once when price gives back this much from " +
            "its best. Engine-held, follows the peak, never retreats. This is what ends most ladders.")
        LegendLine("SL", "the broker's own stop, set once per trade at its fill and never moved. " +
            "A backstop for the server dying — keep it well WIDER than the trail.")
        Text("A trade opens paying the spread, so TP and trail must both clear it or the trade " +
            "cannot win — the engine refuses that. Entry step, cooldown and the daily cap live in " +
            "Settings → Strategies.",
            fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 3.dp))
    } else {
        Text("SET never arms. Tap ⌃ for max rungs / max lots and what TP / SL / trail mean.",
            fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 4.dp))
    }
    }   // end of the scrolling body

    // ---- SET / ARM: a FIXED FOOTER, outside the scroll above ----
    // Actions must never be something you have to discover by scrolling. This row was inside the
    // scrolling body and, once the four risk fields joined it, both buttons sat below the panel's
    // fold -- visible in a screenshot, unreachable with a thumb.
    Spacer(Modifier.height(8.dp))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
        Button(
            onClick = {
                // enabled = null: setting a level can NEVER arm the engine as a side effect.
                // That invariant is inherited from LadderTrigger and must survive any fast path.
                // But "never arms" is not the same as "never trades": on an engine that is
                // ALREADY armed, `_apply` drops the running LadderState and resets `_rearm_ok`,
                // so the next poll builds a fresh ladder that fires IMMEDIATELY if the new level
                // is already behind price. Same two taps, and the second one buys. Hence the
                // confirm -- but only in exactly that case, so the fast path stays fast.
                if (s.enabled && draftCrossed) confirmSet = true else sendSet()
            },
            enabled = live && trgNum != null && trgNum > 0.0,
            modifier = Modifier.weight(1f).height(40.dp),
            contentPadding = PaddingValues(0.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                disabledContainerColor = ChipBg.copy(alpha = 0.5f),
                disabledContentColor = ChipDim.copy(alpha = 0.5f),
            ),
        ) { Text("SET LEVEL", fontWeight = FontWeight.Bold, fontSize = 12.sp) }

        Button(
            onClick = { if (s.enabled) onSet(s.id, false, JsonObject(emptyMap())) else confirmArm = true },
            // ARM is blocked when the guard would refuse -- the reason is printed above, so this is
            // the greyed control it explains. DISARM is NEVER blocked: never stand between the
            // operator and the brakes, whatever the guard says.
            enabled = live && (s.enabled || guardOk),
            modifier = Modifier.weight(1f).height(40.dp),
            contentPadding = PaddingValues(0.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = if (s.enabled) Red.copy(alpha = 0.28f) else Amber,
                contentColor = if (s.enabled) Red else Color(0xFF241A04),
                disabledContainerColor = ChipBg.copy(alpha = 0.5f),
                disabledContentColor = ChipDim.copy(alpha = 0.5f),
            ),
        ) { Text(if (s.enabled) "DISARM" else "ARM", fontWeight = FontWeight.Bold, fontSize = 12.sp) }
    }

    // ---- SET onto a LIVE, ARMED engine: the only case where SET can cost money ----
    if (confirmSet) {
        AlertDialog(
            onDismissRequest = { confirmSet = false },
            title = { Text("Move the level while armed?") },
            text = {
                Column {
                    Text("The ladder is ARMED and ${if (paper) "simulating" else "placing real orders"}, " +
                        "and ${fmtP(trgNum)} is already behind the ${if (side == "buy") "ask" else "bid"} " +
                        "(${fmtP(px, 3)}).", color = Red, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text("Applying it restarts the ladder, which will ENTER on the next tick. " +
                        "DISARM first if you only meant to change the number.")
                }
            },
            confirmButton = {
                TextButton(onClick = { confirmSet = false; sendSet() }) {
                    Text("APPLY ANYWAY", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = { TextButton(onClick = { confirmSet = false }) { Text("Cancel") } },
        )
    }

    // ---- the one confirmation, and it is now the ONLY thing between ARM and the broker ----
    // The paper toggle is gone from this panel by design: an ARM that quietly simulates is an ARM
    // that lies. So ARM sends `paper = false` alongside `enabled = true` -- one button, one
    // meaning -- and this dialog carries the whole warning instead of a switch two screens away.
    // It can only ever be demo money: the ladder declares `allows_real = False`, which
    // StrategyBase re-checks EVERY poll and which auto-disables the engine the moment MT5 is on
    // anything else. The `real` branch below therefore describes a refusal, not a risk.
    if (confirmArm) {
        val real = health.isDemo == false
        AlertDialog(
            onDismissRequest = { confirmArm = false },
            title = { Text("Arm the ladder?") },
            text = {
                Column {
                    if (appliedCrossed) {
                        Text("The applied trigger is ALREADY CROSSED — this will ENTER IMMEDIATELY.",
                            color = Red, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(6.dp))
                    }
                    Text("${side.uppercase()} ${fmtP(p?.volume)} lots at ${applied?.let { fmtP(it) } ?: "no trigger set"}" +
                        (p?.maxPositions?.let { if (it == 0) ", UNCAPPED rungs" else if (it > 1) ", up to $it rungs" else "" } ?: ""))
                    Spacer(Modifier.height(6.dp))
                    Text("TP ${fmtP(p?.target)} · trail ${fmtP(p?.retrace)} · broker SL ${fmtP(p?.hardSl)} (all $/oz)",
                        fontSize = 12.sp)
                    Spacer(Modifier.height(6.dp))
                    if (real) Text(
                        "This account is NOT a demo. The ladder is demo-only and the server " +
                            "will refuse and disable it.",
                        color = Red, fontWeight = FontWeight.Bold,
                    ) else Text("It will place REAL ORDERS on this demo account.",
                        color = Red, fontWeight = FontWeight.Bold)
                    // Re-arming is not a one-shot: a price oscillating around the level starts a
                    // NEW ladder every time it crosses back through. With cooldown_s and
                    // max_ladders_per_day both defaulting to 0 nothing bounds that, and it is the
                    // exact shape of the 68-ladders-in-25-minutes run the engine's docstring
                    // records. Say it here, where it is still cheap to reconsider.
                    if (appliedCrossed) {
                        Spacer(Modifier.height(6.dp))
                        Text("It re-arms every time price crosses back through the level. " +
                            "Set a cooldown or a daily cap in Settings to bound that.",
                            fontSize = 11.sp, color = Amber)
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmArm = false
                    onSet(s.id, true, buildJsonObject { put("paper", JsonPrimitive(false)) })
                }) { Text("ARM", color = Amber, fontWeight = FontWeight.Bold) }
            },
            dismissButton = { TextButton(onClick = { confirmArm = false }) { Text("Cancel") } },
        )
    }
}

/* ============================== STRADDLE-LADDER ============================== */

@Composable
private fun ColumnScope.SladderQuick(
    s: StrategyStatus?,
    quote: Quote,
    health: Health,
    live: Boolean,
    expanded: Boolean,
    onSet: (String, Boolean?, JsonObject) -> Unit,
) {
    if (s == null) {
        Text("No straddle-ladder engine on this server.", fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    val p = s.params

    // Draft state keyed on the SERVER value -- an unchanged snapshot leaves the field alone; a
    // change made elsewhere re-seeds it. Same trick as LadderQuick.
    var lvl by remember(p?.level) { mutableStateOf(if ((p?.level ?: 0.0) > 0.0) fmtP(p?.level) else "") }
    var sl by remember(p?.sl) { mutableStateOf(fmtP(p?.sl)) }
    var tp by remember(p?.tp) { mutableStateOf(fmtP(p?.tp)) }
    var gap by remember(p?.gap) { mutableStateOf(fmtP(p?.gap)) }
    var vol by remember(p?.volume) { mutableStateOf(fmtP(p?.volume)) }
    var mlegs by remember(p?.maxLegs) { mutableStateOf((p?.maxLegs ?: 0).toString()) }
    var mlot by remember(p?.maxLots) { mutableStateOf(fmtP(p?.maxLots)) }

    var confirmArm by remember { mutableStateOf(false) }
    var confirmSet by remember { mutableStateOf(false) }

    val lvlNum = lvl.trim().toDoubleOrNull()
    val bid = quote.bid
    val ask = quote.ask
    val mid = if (bid != null && ask != null) (bid + ask) / 2.0 else (bid ?: ask)
    // The straddle fires when the level sits INSIDE the spread. `wouldFireNow` is the server's
    // answer for the APPLIED level (what ARM acts on); the draft version reddens the field as you
    // type, before you send.
    val appliedFire = s.wouldFireNow == true
    val draftFire = lvlNum != null && bid != null && ask != null && lvlNum in bid..ask
    val draftDist = if (lvlNum != null && mid != null) lvlNum - mid else null

    // The server's spread guard, rendered not re-derived: sl or tp inside the live spread is a
    // bracket that cannot win, which the engine refuses to arm. Default to allowing when absent.
    val guardOk = s.guardOk != false
    val minStop = s.minStop ?: s.spread
    val insideSpread = { v: String -> minStop != null && (v.trim().toDoubleOrNull()?.let { it <= minStop } ?: false) }
    val queuedBehindBook = s.managingReason == "params_changed"

    // Hoisted so the button and its confirmation send BYTE-IDENTICAL bodies. Only CHANGED values
    // are sent -- `_apply` treats any present key as an edit and, mid-run, hands the open leg over
    // to managing-only mode, so echoing unchanged numbers would stall a running grid.
    val sendSet: () -> Unit = {
        val body = buildJsonObject {
            lvlNum?.let { put("level", JsonPrimitive(it)) }
            sl.trim().toDoubleOrNull()?.takeIf { it != p?.sl }?.let { put("sl", JsonPrimitive(it)) }
            tp.trim().toDoubleOrNull()?.takeIf { it != p?.tp }?.let { put("tp", JsonPrimitive(it)) }
            gap.trim().toDoubleOrNull()?.takeIf { it != p?.gap }?.let { put("gap", JsonPrimitive(it)) }
            vol.trim().toDoubleOrNull()?.takeIf { it != p?.volume }?.let { put("volume", JsonPrimitive(it)) }
            mlegs.trim().toIntOrNull()?.takeIf { it != p?.maxLegs }?.let { put("max_legs", JsonPrimitive(it)) }
            mlot.trim().toDoubleOrNull()?.takeIf { it != p?.maxLots }?.let { put("max_lots", JsonPrimitive(it)) }
        }
        onSet(s.id, null, body)
    }

    Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {

    // ---- level + lot + the two bracket numbers + gap ----
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        TinyField(
            "level", lvl, live, Modifier.weight(1.6f), fontSize = 15.sp,
            hint = if (draftDist == null) null
            else (if (draftDist >= 0) "+" else "−") + fmtP(abs(draftDist)),
            hintColor = if (appliedFire) Red else Amber,
        ) { lvl = it }
        TinyField("lot", vol, live, Modifier.weight(1f)) { vol = it }
        TinyField("SL", sl, live, Modifier.weight(1f), warn = insideSpread(sl)) { sl = it }
        TinyField("TP", tp, live, Modifier.weight(1f), warn = insideSpread(tp)) { tp = it }
        TinyField("gap", gap, live, Modifier.weight(1f)) { gap = it }
    }

    // ---- the live spread: what SL and TP are judged against ----
    if (minStop != null) {
        Spacer(Modifier.height(4.dp))
        Text("spread ${fmtP(minStop)} /oz — SL and TP must clear it",
            fontSize = 10.sp, fontWeight = FontWeight.Bold,
            color = if (!guardOk) Red else MaterialTheme.colorScheme.onSurfaceVariant)
    }

    // ---- seed the level from the live quote, then nudge it ----
    Spacer(Modifier.height(4.dp))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        SeedChip("@BID", Modifier.weight(1f), live && bid != null) { bid?.let { lvl = fmtP(it) } }
        SeedChip("@ASK", Modifier.weight(1f), live && ask != null) { ask?.let { lvl = fmtP(it) } }
        listOf(-1.0, -0.1, 0.1, 1.0).forEach { step ->
            StepChip(step, Modifier.weight(1f), live && lvlNum != null) {
                lvlNum?.let { lvl = fmtP(it + step) }
            }
        }
    }

    if (appliedFire) {
        Text("PRICE IS AT THE LEVEL — ARM places the straddle at once", fontSize = 10.sp,
            fontWeight = FontWeight.Bold, color = Red, modifier = Modifier.padding(top = 4.dp))
    }
    if (queuedBehindBook) {
        Text("New level queued — no new run until the ${s.openLegs ?: ""} open leg(s) close.",
            fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Amber,
            modifier = Modifier.padding(top = 4.dp))
    }
    if (!guardOk && s.guardReason != null) {
        Text(s.guardReason, fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Red,
            maxLines = if (expanded) 6 else 3, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 4.dp))
    }

    // ---- run status: phase, locked direction, leg count, next entry ----
    Spacer(Modifier.height(5.dp))
    val bits = buildList {
        s.phase?.takeIf { it.isNotBlank() }?.let { add(it) }
        s.direction?.let { add(it.uppercase()) }
        if (s.legsTaken != null) {
            val cap = (p?.maxLegs ?: 0).let { if (it > 0) "/$it" else "" }
            add("leg ${s.legsTaken}$cap")
        }
        s.nextEntry?.takeIf { it > 0.0 }?.let { add("next ${fmtP(it)}") }
    }
    if (bits.isNotEmpty()) {
        Text(bits.joinToString("  ·  "), fontSize = 10.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
            overflow = TextOverflow.Ellipsis)
    } else if (!s.state.isNullOrBlank()) {
        Text(s.state, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
    s.error?.let {
        Text(it, fontSize = 10.sp, color = Red, fontWeight = FontWeight.Bold,
            maxLines = if (expanded) 6 else 2, overflow = TextOverflow.Ellipsis)
    }
    // Parked at max_legs: the run asks for a new level (server-latched, edge-triggered by the app's
    // notification layer). Show it so the operator re-loads rather than watch the trend walk away.
    if (s.needsAttention == true) {
        Text("🔔 " + (s.attentionReason ?: "Run parked — set a new level to continue."),
            fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Amber,
            maxLines = if (expanded) 4 else 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 3.dp))
    }

    // ---- expanded: continuation mode + caps + what the fields mean ----
    if (expanded) {
        // OFF (default) = single leg on the trend side after the 1st straddle. ON = every entry a
        // straddle (walking grid). Sent on FLIP, immediately -- it is not a SET LEVEL.
        Spacer(Modifier.height(7.dp))
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Every entry a straddle", fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Text(
                    if (p?.alwaysStraddle == true)
                        "ON — walking straddle grid; re-detects direction each step (nets ~0/step)."
                    else
                        "OFF — single leg on the trend side after the 1st straddle (rides the trend).",
                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(
                checked = p?.alwaysStraddle == true,
                enabled = live,
                onCheckedChange = { want ->
                    onSet(s.id, null, buildJsonObject { put("always_straddle", JsonPrimitive(want)) })
                },
            )
        }
        Spacer(Modifier.height(7.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            TinyField("max legs (0=∞)", mlegs, live, Modifier.weight(1f)) { mlegs = it }
            TinyField("max lots (0=∞)", mlot, live, Modifier.weight(1f)) { mlot = it }
        }
        if ((mlegs.trim().toIntOrNull() ?: 0) == 0) {
            Text("Max legs 0 = UNCAPPED — it keeps taking orders until the daily-loss cap. " +
                "Set a real cap before arming.",
                fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Amber,
                modifier = Modifier.padding(top = 3.dp))
        }
        LegendLine("1st order", "a straddle, ONLY to find the trend (winner +tp, loser −sl).")
        LegendLine("then", "single leg on the trend side; arms at last winning TP ± gap.")
        LegendLine("SL / TP", "each order's bracket, $/oz. TP must clear the spread or a win still loses.")
        LegendLine("on a stop", "keeps the SAME level (last winning TP) — waits, does not chase.")
        LegendLine("max legs", "orders per run before it PARKS.")
        Text("Turn on “Every entry a straddle” above for the walking-grid mode instead. Demo-only, " +
            "HEDGING account required. Cooldown and the daily-loss cap live in Settings → Strategies.",
            fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 3.dp))
    } else {
        Text("SET never arms. Tap ⌃ for max legs / max lots and what the fields mean.",
            fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
    }
    }   // end of the scrolling body

    // ---- SET / ARM: a FIXED FOOTER, outside the scroll ----
    Spacer(Modifier.height(8.dp))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
        Button(
            onClick = {
                // SET never arms. But on an ALREADY-armed engine a new level restarts the run and
                // opens a fresh straddle -- which fires at once if the level is at the market. Same
                // guard the ladder carries: confirm only in exactly that case.
                if (s.enabled && draftFire) confirmSet = true else sendSet()
            },
            enabled = live && lvlNum != null && lvlNum > 0.0,
            modifier = Modifier.weight(1f).height(40.dp),
            contentPadding = PaddingValues(0.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                disabledContainerColor = ChipBg.copy(alpha = 0.5f),
                disabledContentColor = ChipDim.copy(alpha = 0.5f),
            ),
        ) { Text("SET LEVEL", fontWeight = FontWeight.Bold, fontSize = 12.sp) }

        Button(
            onClick = { if (s.enabled) onSet(s.id, false, JsonObject(emptyMap())) else confirmArm = true },
            enabled = live && (s.enabled || guardOk),
            modifier = Modifier.weight(1f).height(40.dp),
            contentPadding = PaddingValues(0.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = if (s.enabled) Red.copy(alpha = 0.28f) else Amber,
                contentColor = if (s.enabled) Red else Color(0xFF241A04),
                disabledContainerColor = ChipBg.copy(alpha = 0.5f),
                disabledContentColor = ChipDim.copy(alpha = 0.5f),
            ),
        ) { Text(if (s.enabled) "DISARM" else "ARM", fontWeight = FontWeight.Bold, fontSize = 12.sp) }
    }

    if (confirmSet) {
        AlertDialog(
            onDismissRequest = { confirmSet = false },
            title = { Text("Move the level while armed?") },
            text = {
                Column {
                    Text("The engine is ARMED and ${fmtP(lvlNum)} is at the market " +
                        "(${fmtP(mid, 3)}).", color = Red, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text("Applying it restarts the run and opens a NEW straddle on the next tick. " +
                        "DISARM first if you only meant to change a number.")
                }
            },
            confirmButton = {
                TextButton(onClick = { confirmSet = false; sendSet() }) {
                    Text("APPLY ANYWAY", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = { TextButton(onClick = { confirmSet = false }) { Text("Cancel") } },
        )
    }

    if (confirmArm) {
        val real = health.isDemo == false
        AlertDialog(
            onDismissRequest = { confirmArm = false },
            title = { Text("Arm the straddle-ladder?") },
            text = {
                Column {
                    if (appliedFire) {
                        Text("Price is AT the level — the straddle is placed IMMEDIATELY.",
                            color = Red, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(6.dp))
                    }
                    Text("Opens a LONG + SHORT straddle at ${p?.level?.let { fmtP(it) } ?: (lvlNum?.let { fmtP(it) } ?: "no level")}.")
                    Spacer(Modifier.height(6.dp))
                    Text("SL ${fmtP(p?.sl)} · TP ${fmtP(p?.tp)} · gap ${fmtP(p?.gap)} (all $/oz). " +
                        (if (p?.alwaysStraddle == true) "Every re-entry is a straddle too (walking grid), "
                         else "Then single legs on the trend side, ") +
                        ((p?.maxLegs ?: 0).let { if (it > 0) "up to $it orders" else "UNCAPPED orders" }) +
                        ", one at a time.", fontSize = 12.sp)
                    Spacer(Modifier.height(6.dp))
                    if (real) Text(
                        "This account is NOT a demo. This engine is demo-only and the server will " +
                            "refuse and disable it.", color = Red, fontWeight = FontWeight.Bold,
                    ) else Text(
                        "It will place REAL ORDERS on this demo account, and needs a HEDGING account.",
                        color = Red, fontWeight = FontWeight.Bold,
                    )
                    if ((p?.maxLegs ?: 0) == 0) {
                        Spacer(Modifier.height(6.dp))
                        Text("Max straddles is 0 (uncapped) — the grid walks until the daily-loss " +
                            "cap. Set a cap first.", fontSize = 11.sp, color = Amber)
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmArm = false
                    onSet(s.id, true, JsonObject(emptyMap()))
                }) { Text("ARM", color = Amber, fontWeight = FontWeight.Bold) }
            },
            dismissButton = { TextButton(onClick = { confirmArm = false }) { Text("Cancel") } },
        )
    }
}

/* ============================== RIDER ============================== */

@Composable
private fun ColumnScope.RiderQuick(
    s: StrategyStatus?,
    quote: Quote,
    health: Health,
    live: Boolean,
    expanded: Boolean,
    onSet: (String, Boolean?, JsonObject) -> Unit,
    onPlaceCard: (RiderCard, Double?) -> Unit,
) {
    if (s == null) {
        Text("No rider engine on this server.", fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    var confirm by remember { mutableStateOf(false) }
    val card = s.card
    // `actionable` is the SERVER's answer. Do NOT substitute card.kind == "enter": the card keeps
    // that kind for the whole trade, so it would offer a long-expired entry price.
    val hot = riderHot(s)
    val canPlace = hot && card != null

    // ---- scrolling body: either the live card, or why there is none ----
    Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {
        if (canPlace && card != null) {
            KvLine("SIDE", card.side?.uppercase() ?: "—", if (card.isBuy) Green else Red)
            KvLine("LOT", fmtP(card.lot, 2))
            KvLine("ENTRY ~", fmtP(card.entry, 3))
            if (expanded) {
                KvLine("SL", fmtP(card.sl, 3))
                KvLine("TP", fmtP(card.tp, 3))
                card.reason?.let { KvLine("REASON", it) }
                if (s.paperTrades != null) {
                    KvLine("PAPER", "${fmtP(s.paperPnlPerOz)} /oz · ${s.paperTrades} trades")
                }
            }
        } else {
            Text(
                when {
                    !s.enabled -> "Rider is OFF. ARM it below to start getting suggestions."
                    s.card == null -> "Armed — waiting for the first suggestion."
                    else -> "Armed — nothing actionable right now (${s.card?.kind ?: "—"})."
                },
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            s.note?.let { Text(it, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 3.dp)) }
            if (expanded && s.paperTrades != null) {
                Text("paper ${fmtP(s.paperPnlPerOz)} /oz · ${s.paperTrades} trades",
                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 3.dp))
            }
        }
    }   // end of the scrolling body

    // ---- fixed footer: PLACE (only when a card is hot) above ARM/DISARM (always) ----
    // The rider tab used to have no engine toggle at all -- the only way to turn the suggestion
    // engine on or off was Settings -> Strategies, the exact walk this panel deletes for the
    // ladder. ARM here just runs the SUGGESTION engine; it does not auto-trade. Placing a trade is
    // still a separate, deliberate PLACE tap on a specific card, and turning on auto-execution is
    // still a Settings-only decision (the auto_demo / auto_real switches). So this toggle is safe
    // to leave un-confirmed: the worst it does is start or stop suggestions.
    if (canPlace && card != null) {
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = { confirm = true },
            // point == null means the broker has not told us the point size, and the price->points
            // conversion is then a guess. Refuse rather than place a stop in the wrong unit.
            enabled = live && s.enabled && quote.point != null && quote.point > 0.0,
            colors = ButtonDefaults.buttonColors(containerColor = Green),
            modifier = Modifier.fillMaxWidth().height(44.dp),
        ) { Text("PLACE THIS TRADE", fontWeight = FontWeight.Bold, color = Color(0xFF05231A)) }
        Text(
            if (quote.point == null) "Waiting for the broker's point size — cannot place safely yet."
            else "Expires with its bar. " + if (expanded) "" else "Tap ⌃ for SL/TP and the paper record.",
            fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp, bottom = 2.dp),
        )
    }

    Spacer(Modifier.height(6.dp))
    Button(
        onClick = { onSet(s.id, !s.enabled, JsonObject(emptyMap())) },
        enabled = live,
        modifier = Modifier.fillMaxWidth().height(40.dp),
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = if (s.enabled) Red.copy(alpha = 0.28f) else Amber,
            contentColor = if (s.enabled) Red else Color(0xFF241A04),
            disabledContainerColor = ChipBg.copy(alpha = 0.5f),
            disabledContentColor = ChipDim.copy(alpha = 0.5f),
        ),
    ) { Text(if (s.enabled) "DISARM — stop suggestions" else "ARM — start suggestions",
        fontWeight = FontWeight.Bold, fontSize = 12.sp) }

    if (confirm && card != null) {
        val real = health.isDemo == false
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("Place this trade?") },
            text = {
                Column {
                    Text("${card.side?.uppercase()} ${fmtP(card.lot)} at ~${fmtP(card.entry, 3)}")
                    Spacer(Modifier.height(6.dp))
                    // Shown as DISTANCES, because that is the unit /order actually receives.
                    val e = card.entry
                    if (e != null && card.sl != null && card.sl != 0.0)
                        Text("SL ${fmtP(card.sl, 3)}  (${fmtP(abs(e - card.sl))} away)")
                    else Text("No stop loss on this card.", color = Red, fontWeight = FontWeight.Bold)
                    if (e != null && card.tp != null && card.tp != 0.0)
                        Text("TP ${fmtP(card.tp, 3)}  (${fmtP(abs(card.tp - e))} away)")
                    if (real) {
                        Spacer(Modifier.height(6.dp))
                        Text("This is a REAL-MONEY account.", color = Red, fontWeight = FontWeight.Bold)
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { confirm = false; onPlaceCard(card, quote.point) }) {
                    Text("PLACE", color = Green, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Cancel") } },
        )
    }
}

/* ============================== small parts ============================== */

/**
 * Explicit colours on EVERY control in this panel.
 *
 * `buttonColors(containerColor = …)` alone leaves `contentColor` at its default, which under the
 * dynamic dark scheme resolved to a near-black — the ± steppers rendered as invisible dark text on
 * dark pills. On a trading surface an unreadable control is worse than an ugly one, so nothing here
 * relies on a theme default for contrast.
 */
private val ChipBg = Color(0xFF2E343B)
private val ChipInk = Color(0xFFE8EAED)
private val ChipDim = Color(0xFFB4BCC4)

@Composable
private fun SideChip(
    label: String, selected: Boolean, tint: Color, modifier: Modifier, enabled: Boolean,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.height(34.dp),
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = if (selected) tint.copy(alpha = 0.28f) else ChipBg,
            contentColor = if (selected) tint else ChipDim,
            disabledContainerColor = ChipBg.copy(alpha = 0.5f),
            disabledContentColor = ChipDim.copy(alpha = 0.5f),
        ),
    ) { Text(label, fontWeight = FontWeight.Bold, fontSize = 12.sp) }
}

@Composable
private fun SeedChip(label: String, modifier: Modifier, enabled: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.height(30.dp),
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = Amber.copy(alpha = 0.24f), contentColor = Amber,
            disabledContainerColor = ChipBg.copy(alpha = 0.5f),
            disabledContentColor = ChipDim.copy(alpha = 0.5f),
        ),
    ) { Text(label, fontSize = 10.sp, fontWeight = FontWeight.Bold) }
}

@Composable
private fun StepChip(step: Double, modifier: Modifier, enabled: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.height(30.dp),
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = ChipBg, contentColor = ChipInk,
            disabledContainerColor = ChipBg.copy(alpha = 0.5f),
            disabledContentColor = ChipDim.copy(alpha = 0.5f),
        ),
    ) {
        Text((if (step > 0) "+" else "−") + fmtP(abs(step), if (abs(step) < 1.0) 1 else 0),
            fontFamily = FontFamily.Monospace, fontSize = 12.sp, fontWeight = FontWeight.Bold)
    }
}

/**
 * A numeric field that costs ~46dp instead of Material's ~56dp+.
 *
 * `OutlinedTextField` reserves vertical space for a floating label whether or not the field is
 * focused. Five of them (trigger + lot/TP/SL/trail) plus chrome overran the panel's 330dp ceiling,
 * and that ceiling is not decorative -- it is what stops this overlay from covering the real BUY /
 * SELL / CLOSE buttons underneath. So the label is a plain caption above a fixed-height box, and
 * every colour is stated outright: the same dark-on-dark trap that made the steppers invisible
 * applies to a text field's cursor and content.
 */
@Composable
private fun TinyField(
    label: String, value: String, enabled: Boolean, modifier: Modifier,
    fontSize: androidx.compose.ui.unit.TextUnit = 13.sp,
    // A live readout that belongs TO this field (the trigger's distance-to-price). Riding in the
    // caption costs no extra row, and keeps the number beside the box it describes.
    hint: String? = null,
    hintColor: Color = ChipDim,
    // The value sits inside the live spread -- what the guard refuses. Redden the border and label
    // so it is caught while typing rather than after ARM bounces.
    warn: Boolean = false,
    onChange: (String) -> Unit,
) {
    Column(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, fontSize = 8.sp, color = if (warn) Red else ChipDim, maxLines = 1,
                fontWeight = if (warn) FontWeight.Bold else FontWeight.Normal,
                overflow = TextOverflow.Clip)
            if (hint != null) {
                Spacer(Modifier.width(4.dp))
                Text(hint, fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold, color = hintColor, maxLines = 1)
            }
        }
        Spacer(Modifier.height(2.dp))
        Box(
            Modifier.fillMaxWidth().height(34.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(ChipBg.copy(alpha = if (enabled) 1f else 0.5f))
                .border(1.dp, if (warn) Red else ChipDim.copy(alpha = 0.35f), RoundedCornerShape(6.dp))
                .padding(horizontal = 6.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            BasicTextField(
                value = value, onValueChange = onChange, enabled = enabled, singleLine = true,
                textStyle = TextStyle(
                    color = if (enabled) ChipInk else ChipDim.copy(alpha = 0.5f),
                    fontSize = fontSize, fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                ),
                cursorBrush = SolidColor(Amber),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun LegendLine(term: String, meaning: String) {
    Row(Modifier.fillMaxWidth().padding(top = 3.dp)) {
        Text(term, fontSize = 10.sp, fontWeight = FontWeight.Bold, color = ChipInk,
            modifier = Modifier.width(34.dp))
        Text(meaning, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun KvLine(k: String, v: String, tint: Color? = null) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(k, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(v, fontSize = 11.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,
            color = tint ?: MaterialTheme.colorScheme.onSurface)
    }
}
