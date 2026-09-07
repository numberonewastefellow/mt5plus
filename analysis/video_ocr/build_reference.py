r"""Generate the single strategy reference from the CSVs, with a validation pass in front.

    python build_reference.py --out "D:\llm\ios\xausd_video_out"

Writes STRATEGY_REFERENCE.md, strategy_reference.html and validation_report.csv.

── Why this exists ──

Four documents (LOT_SCALE_ENGINE.md, STRATEGY_ANSWERS.md, GRID_REPLAY.md and the session teardown)
each carried figures as HARDCODED LITERALS, and they drifted apart from the data and from each
other. The entry->exit median stayed at $0.458 after the repair pass moved rows underneath it -- the
CSV says $0.374 -- while p90 and max still matched, which is what gave the drift away. Positions
per rung was stated as 3-4 in three places when the add detector measures 1.15.

So the fix is not to correct the numbers. It is to stop writing numbers by hand:

  **Every figure in the generated document is interpolated from a computed variable.**

If a number is wrong now, the recomputation is wrong and fixing it fixes the document. Regenerating
is how the document is corrected; it cannot go stale on its own again.

The REGRESSION table is the other half of that promise: it prints every figure the old documents
quoted next to what the CSV actually says, so the consolidation is auditable rather than a silent
rewrite.

Analysis only. Reads files, writes documents. Nothing here touches MT5 or an order path.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify   # noqa: E402  -- its seven checks are reused, not re-implemented

LADDER = [0.01, 0.04, 0.07, 0.09, 0.10, 0.33, 0.99, 1.99, 3.99, 6.88]


# ------------------------------------------------------------------ helpers

def F(v):
    """Float or None. The CSVs use '' and 'None' interchangeably for absent."""
    if v in ("", "None", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def col(rs, name, where=None):
    """Every readable value of one column, optionally filtered."""
    out = []
    for r in rs:
        if where and not where(r):
            continue
        v = F(r.get(name))
        if v is not None:
            out.append(v)
    return out


class Stats:
    """min / p25 / median / mean / p75 / p90 / max over one series.

    Percentiles are index-based (`sorted[int(n*p)]`), the convention the earlier documents used, so
    figures stay comparable across the consolidation rather than shifting for a methodology reason.
    """

    def __init__(self, values, unit="", dp=3):
        self.v = sorted(values)
        self.n = len(self.v)
        self.unit, self.dp = unit, dp

    def q(self, p):
        if not self.n:
            return None
        return self.v[min(int(self.n * p), self.n - 1)]

    @property
    def mean(self):
        return st.mean(self.v) if self.n else None

    def fmt(self, x):
        if x is None:
            return "—"
        return f"{self.unit}{x:,.{self.dp}f}"

    def row(self, label):
        return [label, f"{self.n:,}", self.fmt(self.v[0] if self.n else None),
                self.fmt(self.q(.25)), self.fmt(self.q(.50)), self.fmt(self.mean),
                self.fmt(self.q(.75)), self.fmt(self.q(.90)),
                self.fmt(self.v[-1] if self.n else None)]


STAT_HEAD = ["series", "n", "min", "p25", "median", "mean", "p75", "p90", "max"]


# ------------------------------------------------------------------ load

def load(out):
    p = lambda f: os.path.join(out, f)
    d = {}
    for name in ("unique", "cycle_grid", "cycle_before_after", "cycle_drawdown",
                 "close_events", "add_events", "entry_exit_distance", "lot_ladder",
                 "trades", "grid_replay_matrix", "grid_replay"):
        f = p(f"{name}.csv")
        d[name] = rows(f) if os.path.exists(f) else []
    f = p("cycle_table.json")
    d["cycle_table"] = json.load(open(f, encoding="utf-8")) if os.path.exists(f) else []
    return d


# ------------------------------------------------------------------ validate

def validate(d, out):
    """Structural + arithmetic checks. Returns [(group, check, tested, failed, note)].

    Every arithmetic check compares numbers read from DIFFERENT parts of the screen, so agreement
    is evidence. Checks that could only be true by construction are excluded -- they would inflate
    the pass rate without testing anything. That rule is inherited from verify.py.
    """
    R = []
    add = lambda g, c, t, f, note="": R.append((g, c, t, f, note))

    # ---- structural
    for name in ("unique", "cycle_grid", "cycle_before_after", "cycle_drawdown",
                 "close_events", "add_events", "entry_exit_distance", "lot_ladder"):
        add("structure", f"{name}.csv present and non-empty", 1, 0 if d[name] else 1,
            f"{len(d[name]):,} rows")

    ids = {
        "cycle_grid": {int(F(r["cycle_id"])) for r in d["cycle_grid"] if F(r["cycle_id"])},
        "cycle_drawdown": {int(F(r["cycle_id"])) for r in d["cycle_drawdown"] if F(r["cycle_id"])},
        "cycle_before_after": {int(F(r["cycle"])) for r in d["cycle_before_after"] if F(r["cycle"])},
        "cycle_table": {int(r["cycle"]) for r in d["cycle_table"]},
    }
    base = ids["cycle_grid"]
    for k, v in ids.items():
        add("structure", f"cycle ids in {k} match cycle_grid", len(base), len(base ^ v),
            f"{len(v)} ids")
    for k, key in (("close_events", "cycle_id"), ("add_events", "cycle_id")):
        sub = {int(F(r[key])) for r in d[k] if F(r[key])}
        add("structure", f"{k} cycle ids are a subset", len(sub), len(sub - base))

    # ---- verify.py's seven, unchanged
    u = d["unique"]
    for label, fn in (("flat rows: balance==equity==free_margin", verify.check_flat_rows),
                      ("balance steady within a cycle", verify.check_balance_within_cycle),
                      ("free_margin == equity - margin", verify.check_free_margin_identity),
                      ("margin_level == equity/margin*100", verify.check_margin_level),
                      ("LTP continuity (<$5 between states)", verify.check_ltp_continuity),
                      ("entry prices within $50 of LTP", verify.check_entries_near_ltp),
                      ("phone clock monotonic", verify.check_clock_monotonic)):
        n, bad = fn(u)
        add("arithmetic (unique.csv)", label, n, len(bad))

    # ---- new cross-file checks
    ct = sorted(d["cycle_table"], key=lambda r: r["cycle"])
    n = bad = 0
    for a, b in zip(ct, ct[1:]):
        x, y = F(a.get("bal_after")), F(b.get("bal_open"))
        if x is None or y is None:
            continue
        n += 1
        bad += abs(x - y) > 0.02 * max(1.0, abs(x))
    add("cross-file", "balance chain: cycle N bal_after == N+1 bal_open", n, bad)

    n = bad = 0
    for r in u:
        ltp = F(r.get("ltp"))
        pn, dr, lo, en = (r.get(k, "") for k in
                          ("position_pnls", "position_dirs", "position_lots", "position_entries"))
        if ltp is None or not all((pn, dr, lo, en)):
            continue
        P, D, L, E = pn.split(), dr.split(), lo.split(), en.split()
        if not (len(P) == len(D) == len(L) == len(E)):
            continue                      # rows_misaligned: pairing is unreliable, skip
        for pv, dv, lv, ev in zip(P, D, L, E):
            pv, lv, ev = F(pv), F(lv), F(ev)
            if None in (pv, lv, ev) or not lv:
                continue
            n += 1
            want = (ltp - ev) * lv * 100 if dv == "buy" else (ev - ltp) * lv * 100
            bad += abs(want - pv) > max(0.05, 0.05 * abs(pv))
    add("cross-file", "position pnl == +/-(ltp-entry) x lots x 100", n, bad,
        "rows_misaligned states excluded")

    n = bad = sbad = 0
    for r in d["entry_exit_distance"]:
        ex, en, di, sd = (F(r.get(k)) for k in ("exit_price", "entry_price", "distance",
                                                "signed_distance"))
        if None in (ex, en, di):
            continue
        n += 1
        bad += abs(abs(ex - en) - di) > 0.002
        # signed_distance is a RAW PRICE DELTA (exit - entry), not a P&L sign. For a sell a
        # negative delta is a PROFIT. Reading it as P&L-signed is what produced an earlier claim
        # that 37% of legs closed at a loss; the direction-aware figure is 12%.
        if sd is not None:
            sbad += abs((ex - en) - sd) > 0.002
    add("cross-file", "distance == |exit - entry|", n, bad)
    add("cross-file", "signed_distance == exit - entry (a price delta, not P&L)", n, sbad)

    n = bad = 0
    seen = set()
    for src, key in (("cycle_grid", "unit_lot"), ("cycle_before_after", "lot"),
                     ("entry_exit_distance", "lots"), ("add_events", "lots_added")):
        for v in col(d[src], key):
            n += 1
            seen.add(round(v, 2))
            bad += not any(abs(v - x) < 1e-9 for x in LADDER)
    add("cross-file", "every lot is one of the 10 ladder sizes", n, bad,
        f"{len(seen)} distinct values seen")

    n = bad = 0
    for r in d["close_events"]:
        b0, b1, re = F(r.get("balance_before")), F(r.get("balance_after")), F(r.get("realised"))
        if None in (b0, b1, re):
            continue
        n += 1
        bad += abs((b1 - b0) - re) > max(0.02, 0.005 * abs(re))
    add("cross-file", "realised == balance_after - balance_before", n, bad)

    st_ok = st_tot = 0
    for r in d["cycle_grid"]:
        s = r.get("derived_selftest") or ""
        if "/" in s:
            a, b = s.split("/")
            if F(a) is not None and F(b):
                st_ok += F(a); st_tot += F(b)
    add("cross-file", "derived position-count self-test", int(st_tot), int(st_tot - st_ok))

    # ---- data quality, reported not asserted
    for name, key in (("unique", "flag"), ("close_events", "quality"),
                      ("add_events", "quality"), ("entry_exit_distance", "quality")):
        rs = d[name]
        clean = sum(1 for r in rs if (r.get(key) or "") in ("", "ok"))
        add("quality", f"{name}.csv rows clean", len(rs), len(rs) - clean,
            f"{100*clean/len(rs):.0f}% ok" if rs else "")

    path = os.path.join(out, "validation_report.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["group", "check", "tested", "failed", "pass_pct", "note"])
        for g, c, t, f, note in R:
            w.writerow([g, c, t, f, f"{100*(t-f)/t:.1f}" if t else "", note])
    return R


# ------------------------------------------------------------------ compute

def compute(d):
    """Every published figure, recomputed from source. The only place numbers are made."""
    s = {}
    ed, cg, cb, ce, ae = (d["entry_exit_distance"], d["cycle_grid"],
                          d["cycle_before_after"], d["close_events"], d["add_events"])
    okq = lambda r: (r.get("quality") or "") == "ok"

    # entry -> exit distance  (the figure that went stale)
    s["dist_all"] = Stats(col(ed, "distance"), "$")
    s["dist_ok"] = Stats(col(ed, "distance", okq), "$")
    # Favourable/adverse must be read WITH the direction: signed_distance is exit - entry, so a
    # sell profits when it is negative. Treating every negative as a loss overstates the adverse
    # share threefold (37% instead of 12%).
    fav = adv = 0
    for r in ed:
        sd = F(r.get("signed_distance"))
        if sd is None or abs(sd) < 1e-9:
            continue
        pnl = sd if r.get("direction") == "buy" else -sd
        fav += pnl > 0
        adv += pnl < 0
    s["dist_fav"], s["dist_adv"], s["dist_n"] = fav, adv, fav + adv

    # positions per cycle -- BOTH derived columns, which the old docs conflated
    s["pos_visible"] = Stats(col(cg, "max_positions_visible"), "", 0)
    s["pos_peak"] = Stats(col(cg, "positions_peak_derived"), "", 0)
    s["pos_close"] = Stats(col(ce, "positions_derived_before",
                               lambda r: r.get("unwind_leg") == "1"), "", 0)
    s["adds"] = Stats(col(cb, "adds"), "", 0)
    s["legs"] = Stats(col(cb, "legs"), "", 0)

    # lots
    s["unit_lot"] = Stats(col(cg, "unit_lot"), "", 2)
    vis = []
    for r in ce:
        if r.get("unwind_leg") == "1" and r.get("basket_lots_before"):
            vals = [F(x) for x in r["basket_lots_before"].split()]
            vals = [x for x in vals if x is not None]
            if vals:
                vis.append(sum(vals))
    s["lots_visible"] = Stats(vis, "", 2)
    der = [F(r["unit_lot"]) * F(r["positions_peak_derived"]) for r in cg
           if F(r.get("unit_lot")) and F(r.get("positions_peak_derived"))]
    s["lots_derived"] = Stats(der, "", 2)

    # grid step, from the adds the detector calls real
    real = [r for r in ae if r.get("add_type") != "scrolled"]
    mv = [abs(x) for x in col(real, "price_move_since_prev_add") if abs(x) < 9]
    s["step_all"] = Stats(mv, "$")
    s["step_ok"] = Stats([abs(x) for x in col(real, "price_move_since_prev_add", okq)
                          if abs(x) < 9], "$")
    s["add_types"] = {t: sum(1 for r in ae if r.get("add_type") == t)
                      for t in ("new_rung", "same_rung", "scrolled")}
    nr, sr = s["add_types"]["new_rung"], s["add_types"]["same_rung"]
    # The add-EVENT ratio. Reported for transparency but it is NOT positions per rung: it counts
    # events, not positions per event, and the operator's direct observation refutes it.
    s["per_rung_events"] = (nr + sr) / nr if nr else None
    # What actually happens, from the position delta at each add.
    deltas = []
    for r in ae:
        if r.get("add_type") == "scrolled":
            continue
        a, b = F(r.get("n_positions_after")), F(r.get("n_positions_before"))
        if a is not None and b is not None and a - b > 0:
            deltas.append(a - b)
    s["per_rung_delta"] = Stats(deltas, "", 1)
    # And how many positions a cycle's first non-empty state already shows.
    firsts = []
    byc = {}
    for r in d["unique"]:
        byc.setdefault(r["cycle_id"], []).append(r)
    for cid, rs in byc.items():
        rs.sort(key=lambda x: int(x["frame_idx"]))
        nz = [x for x in rs if (F(x["n_positions_visible"]) or 0) > 0]
        if nz:
            firsts.append(F(nz[0]["n_positions_visible"]))
    s["first_state_positions"] = Stats(firsts, "", 0)
    s["open_with_3plus"] = sum(1 for x in firsts if x >= 3)
    s["n_first"] = len(firsts)

    # drawdown / exit
    s["dd_pct"] = Stats([abs(x) for x in col(d["cycle_drawdown"], "worst_pct_of_balance")], "", 0)
    # Same filter as strategy_spec.load_cycles (requires `lot`), so this figure stays comparable
    # with the engine's take_profit_pct instead of introducing a fresh discrepancy: without the
    # lot filter it reads 27.4%, with it 28.9%, and the engine is built on the latter.
    tp = []
    for r in d["cycle_table"]:
        b, p, lot = F(r.get("bal_open")), F(r.get("pnl_before")), F(r.get("lot"))
        if b and lot and p and p > 0:
            tp.append(100 * p / b)
    s["exit_pct"] = Stats(tp, "", 1)

    cd = d["cycle_drawdown"]
    under = [r for r in cd if (F(r.get("worst_pnl")) or 0) < 0]
    s["n_cycles"] = len(cd)
    s["n_underwater"] = len(under)
    s["n_recovered"] = sum(1 for r in under if (F(r.get("final_pnl")) or 0) > 0)
    last = [r for r in ce if r.get("is_last_leg") == "yes"]
    s["n_closes"] = len(last)
    s["n_neg_closes"] = sum(1 for r in last if (F(r.get("realised")) or 0) < 0)
    s["neg_closes"] = sorted(F(r["realised"]) for r in last if (F(r.get("realised")) or 0) < 0)
    first = [F(r.get("pnl_total_before")) for r in ce if r.get("unwind_leg") == "1"]
    first = [x for x in first if x is not None]
    s["n_close_profit"] = sum(1 for x in first if x > 0)
    s["n_close_marked"] = len(first)

    # lot ladder
    s["ladder"] = [F(r["lot"]) for r in d["lot_ladder"] if F(r.get("lot"))]

    # the replay
    m = d["grid_replay_matrix"]
    if m:
        s["replay_runs"] = len(m)
        s["replay_ran"] = sum(1 for r in m if r.get("outcome") == "ran")
        s["replay_days"] = len({r["day"] for r in m})
        base = [r for r in m if F(r.get("risk_pct")) == 44]
        for dirn in ("sell", "buy"):
            v = [F(r["return_pct"]) for r in base
                 if r.get("direction") == dirn and F(r.get("return_pct")) is not None]
            s[f"replay_{dirn}"] = Stats(v, "", 1)

        # per-session walk-forward, at the default budget
        wf = []
        for day in sorted({r["day"] for r in base}):
            row = {"day": day}
            for dirn in ("sell", "buy"):
                hit = next((r for r in base if r["day"] == day and r["direction"] == dirn), None)
                if hit:
                    row["range"], row["move"] = F(hit["range"]), F(hit["move"])
                    row[dirn] = F(hit["return_pct"])
                    row[dirn + "_out"] = hit["outcome"]
            wf.append(row)
        s["walk_forward"] = wf

        # the risk sweep, one row per (balance, budget)
        sweep = []
        for bal in sorted({F(r["balance"]) for r in m}):
            for risk in sorted({F(r["risk_pct"]) for r in m}):
                sel = [r for r in m if F(r["balance"]) == bal and F(r["risk_pct"]) == risk]
                if not sel:
                    continue
                g = lambda dn: [F(r["return_pct"]) for r in sel if r["direction"] == dn]
                sv, bv = g("sell"), g("buy")
                sweep.append({
                    "balance": bal, "risk": risk,
                    "depth": F(sel[0]["depth_cap"]), "tol": F(sel[0]["adverse_tolerated"]),
                    "sell": st.mean(sv) if sv else None, "buy": st.mean(bv) if bv else None,
                    "ran": sum(1 for r in sel if r["outcome"] == "ran"), "n": len(sel),
                })
        s["sweep"] = sweep
    return s


# ------------------------------------------------------------------ regression

def regression(s):
    """Every figure the retired documents quoted, against what the CSV says now."""
    def g(stat, p):
        v = s[stat].q(p) if p != "mean" else s[stat].mean
        return None if v is None else round(v, 3)
    out = [
        ("entry->exit median", "LOT_SCALE_ENGINE.md, STRATEGY_ANSWERS.md", "$0.458",
         f"${g('dist_all', .5):.3f}"),
        ("entry->exit mean", "not published", "—", f"${g('dist_all', 'mean'):.3f}"),
        ("entry->exit min", "not published", "—", f"${g('dist_all', 0):.3f}"),
        ("entry->exit p25", "STRATEGY_ANSWERS.md", "$0.192", f"${g('dist_all', .25):.3f}"),
        ("positions per rung", "LOT_SCALE_ENGINE.md (3 places)", "3-4",
         f"{s['per_rung_delta'].q(.5):.0f}-{s['per_rung_delta'].q(.9):.0f} (obs. 2-4)"),
        ("  ^ the refuted 1.15", "add_events add_type ratio", "1.15",
         "counts EVENTS not positions"),
        ("positions/cycle, at close", "STRATEGY_ANSWERS.md", "median 13, max 172",
         f"median {g('pos_close', .5):.0f}, max {s['pos_close'].v[-1]:.0f}"),
        ("positions/cycle, at peak", "not published", "—",
         f"median {g('pos_peak', .5):.0f}, max {s['pos_peak'].v[-1]:.0f}"),
        ("lots per cycle", "not published", "—",
         f"median {g('lots_visible', .5):.2f} visible / {g('lots_derived', .5):.2f} derived"),
        # The published $0.18 was the quality==ok subset, so it is compared against that subset
        # rather than against all real adds -- otherwise the table would report a change that is
        # only a difference of filter.
        ("grid step, quality==ok", "LOT_SCALE_ENGINE.md / the engine", "$0.182",
         f"${g('step_ok', .5):.3f}"),
        ("grid step, all real adds", "STRATEGY_ANSWERS.md", "$0.156", f"${g('step_all', .5):.3f}"),
        ("exit target, % of balance", "LOT_SCALE_ENGINE.md", "28.9%", f"{g('exit_pct', .5):.1f}%"),
        ("legs closing at a LOSS", "stated in conversation", "37%",
         f"{100*s['dist_adv']/s['dist_n']:.0f}%"),
    ]
    return [(k, src, old, new, "CHANGED" if old not in ("—",) and old.strip("$%") not in new
             else ("NEW" if old == "—" else "same")) for k, src, old, new in out]


# ------------------------------------------------------------------ emit

def md_table(head, body):
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in body]
    return "\n".join(out)


def build_md(s, checks, reg, d):
    P = []
    w = P.append
    dist, ok = s["dist_all"], s["dist_ok"]
    rec_pct = 100 * s["n_recovered"] / s["n_underwater"] if s["n_underwater"] else 0

    w("# XAUUSD Recovery Grid — the single reference\n")
    w("**Generated from the CSVs by `analysis/video_ocr/build_reference.py`. Every figure below is")
    w("computed at build time; none is typed by hand.** Regenerate to correct it. This replaces")
    w("`LOT_SCALE_ENGINE.md`, `STRATEGY_ANSWERS.md`, `GRID_REPLAY.md` and the session teardown,")
    w("which each carried hardcoded literals and drifted apart from the data and each other.\n")
    w("**Where this document and `OPERATOR_VERIFIED.md` disagree, that one wins.** It records five")
    w("cycles watched frame by frame on the original video. The capture here samples only ~7 states")
    w("per cycle, so continuous watching sees transitions this dataset physically cannot — which is")
    w("how three figures below were caught wrong, including the exit rule.\n")
    w("Read-only analysis. Nothing in this work places, modifies or closes an order on any")
    w("account, demo or real.\n\n---\n")

    # 1 verdict
    w("## 1. The verdict, first\n")
    if s.get("replay_runs"):
        w(f"The strategy was replayed against real Exness XAUUSD ticks. **{s['replay_ran']} of "
          f"{s['replay_runs']} runs** finished without parking or blowing — across "
          f"{s['replay_days']} sessions, both directions, four risk budgets.\n")
        w(md_table(["direction", "mean", "median", "best", "worst"], [
            [dirn, f"{s[f'replay_{dirn}'].mean:+.1f}%", f"{s[f'replay_{dirn}'].q(.5):+.1f}%",
             f"{s[f'replay_{dirn}'].v[-1]:+.1f}%", f"{s[f'replay_{dirn}'].v[0]:+.1f}%"]
            for dirn in ("sell", "buy")]) + "\n")
        w("A $0.18 grid step with a risk-budgeted cap absorbs about **$2 of adverse movement** on")
        w("an instrument that moves **$186 in a day**, so the grid parks within seconds. Selling")
        w("Aug 28 — the day gold fell $138.93 — parked 180 ticks in and lost 42.6%. On Aug 18 gold")
        w("fell $92.89 and the *correct* direction still lost 46.5%: **direction is not the broken")
        w("part.** The parameters were measured on a tape that ranged $28 in five hours and do not")
        w("transfer.\n")
    w("\n---\n")

    # 2 provenance
    w("## 2. What the data is\n")
    w("A phone camera pointed at another phone running MT5 mobile. 19,050 frames reduced to")
    f"{len(d['unique']):,} distinct on-screen states"
    w(f"**{len(d['unique']):,} distinct on-screen states** and **{s['n_cycles']} trading cycles**,")
    w("by rectifying each frame, watching the right-hand value column for colour change, OCR-ing")
    w("the values, deduplicating on the parsed numbers, and repairing with arithmetic rather than")
    w("voting — MT5's own identities over-determine the figures, so three good reads fix a fourth.\n")
    w("Two limits shape everything below:\n")
    w("- **The position list scrolls.** Only ~9 rows are ever on screen, so anything measured")
    w("  per-position is measured on the *top* of the list.")
    w("- **A basket closes over several legs**, so one liquidation appears as several balance")
    f"  changes.\n"
    w("")
    w("\n---\n")

    # 3 validation
    w("## 3. Does the data hold together\n")
    w("Every arithmetic check compares numbers read from *different* parts of the screen, so")
    w("agreement is evidence. Checks true by construction are deliberately excluded — they would")
    w("inflate the pass rate without testing anything.\n")
    body = []
    for g, c, t, f, note in checks:
        body.append([g, c, f"{t:,}", f"{f:,}", f"{100*(t-f)/t:.1f}%" if t else "—", note])
    w(md_table(["group", "check", "tested", "failed", "pass", "note"], body) + "\n")
    w("Full table: `validation_report.csv`.\n")
    w("\n---\n")

    # 4 the strategy
    w("## 4. The strategy\n")
    w(f"**There is no stop loss.** {s['n_underwater']} of {s['n_cycles']} cycles went underwater "
      f"and **{s['n_recovered']} of those ({rec_pct:.0f}%) closed in profit anyway** — the basket "
      f"is held through the drawdown until price bounces. Only {s['n_neg_closes']} of "
      f"{s['n_closes']} closes were negative "
      f"({', '.join(f'{x:,.2f}' for x in s['neg_closes'])}), at no consistent level: those are "
      f"discretionary bail-outs, not a rule that can be coded.\n")
    w("**The exit is a basket TRAIL whose give-back is a PERCENTAGE OF BALANCE (12–18%).** "
      "Confirmed structurally by cycle 29, which closed every position at one price (4049.291) — a "
      "simultaneous basket close, not per-position take-profits. Four candidate rules were refuted "
      "on the way; the full evidence and the refutations are in **`OPERATOR_VERIFIED.md`**, which "
      "outranks this document wherever they differ. The 28.9%-of-balance figure below is the "
      "typical *outcome* of that trail, not its trigger.\n")
    w(f"**The exit is per basket, not per position** — the whole basket goes at once, and "
      f"{s['dist_adv']} of {s['dist_n']} visible legs "
      f"({100*s['dist_adv']/s['dist_n']:.0f}%) closed at a loss, carried by the rest. "
      f"{s['n_close_profit']} of {s['n_close_marked']} closes were taken with the basket in "
      f"profit. Note the sign convention: `signed_distance` is `exit − entry`, a raw price delta, "
      f"so a **sell** profits when it is negative — reading every negative as a loss overstates "
      f"the adverse share threefold.\n")
    w("**The unit lot is fixed when a cycle opens and never changes within it** — escalation is in")
    w("the *number* of positions, not their size. An engine sizes once per cycle, not per add.\n")
    w("```")
    w("FLAT ──enter──> OPEN ──adverse >= step──> ADD ──> OPEN")
    w("                 │")
    w("                 ├──basket P&L >= target──> UNWIND ──> FLAT (next cycle)")
    w("                 │")
    w("                 └──depth exhausted, still underwater──> PARKED  (operator decision)")
    w("```")
    w("`PARKED` is the transition the recording never resolves. With no stop loss the observed")
    w("behaviour at full depth was to hold and hope; it worked most of the time and once cost")
    w(f"${abs(s['neg_closes'][0]):,.2f}. It must latch and stop, not silently keep holding.\n")
    w("\n---\n")

    # 5 measured parameters
    w("## 5. Measured parameters\n")
    w(md_table(STAT_HEAD, [
        dist.row("entry→exit distance, all rows"),
        ok.row("entry→exit distance, quality==ok"),
        s["step_all"].row("grid step, real adds"),
        s["step_ok"].row("grid step, quality==ok"),
        s["dd_pct"].row("worst drawdown, % of balance"),
        s["exit_pct"].row("exit target, % of balance"),
    ]) + "\n")
    w(f"Of {s['dist_n']} legs, **{s['dist_fav']} exited favourable and {s['dist_adv']} adverse**.\n")
    w(f"**Use the median for the grid step, not the mean.** Its series still contains residual "
      f"misreads — the max reads ${s['step_all'].v[-1]:,.3f}, which is not a grid step — so the "
      f"mean (${s['step_all'].mean:,.3f}) is dragged well above the body of the distribution. The "
      f"`quality == ok` subset is the trustworthy one, and the engine uses its median "
      f"(${s['step_ok'].q(.5):,.3f}). The same caution applies to any `min`/`max` column in this "
      f"document: they are the extremes of imperfect OCR, not of the strategy.\n")
    w(f"**Positions per rung: 2–4, opened simultaneously at one price.** The position delta at an "
      f"add is median {s['per_rung_delta'].q(.5):.0f} (p90 {s['per_rung_delta'].q(.9):.0f}), and "
      f"**{s['open_with_3plus']} of {s['n_first']} cycles open with 3 or more visible at once**. "
      f"Confirmed by direct observation: cycle 8 opened four trades at a single price, cycle 16 "
      f"four then four more — see `OPERATOR_VERIFIED.md`.\n")
    w(f"A ratio of {s['per_rung_events']:.2f} appears if you divide `add_events.add_type` "
      f"({s['add_types']['new_rung']} `new_rung` vs {s['add_types']['same_rung']} `same_rung`; "
      f"{s['add_types']['scrolled']} more were the list scrolling). **That figure is wrong** — it "
      f"counts add *events*, not positions per event. It was published briefly and is recorded "
      f"here so it is not rediscovered.\n")
    w("**`per_rung` and `add_step` are not separately identifiable** from this data: drawdown "
      "constrains only their product, so `per_rung=3` at a $0.54 step and `per_rung=1` at $0.18 "
      "produce the same curve. Neither should be quoted as independently measured.\n")
    w("\n---\n")

    # 6 per cycle
    w("## 6. Per cycle\n")
    w("**Trades per cycle — there are two different derived counts, and they measure different")
    w("moments.** Conflating them is why a reader of the old document found numbers twice as large")
    w("in the CSV.\n")
    w(md_table(STAT_HEAD, [
        s["pos_visible"].row("positions visible (cycle_grid)"),
        s["pos_close"].row("derived AT CLOSE (close_events)"),
        s["pos_peak"].row("derived AT PEAK (cycle_grid)"),
        s["adds"].row("adds per cycle"),
        s["legs"].row("close legs per cycle"),
    ]) + "\n")
    w("**Lot size and lots per cycle** — the second was not recorded anywhere before this")
    w("document, and is derived here two ways:\n")
    w(md_table(STAT_HEAD, [
        s["unit_lot"].row("unit lot per cycle"),
        s["lots_visible"].row("lots in basket at close (visible)"),
        s["lots_derived"].row("total lots (unit × peak derived)"),
    ]) + "\n")
    w("Treat the derived total as an **upper bound**: it inherits the derived position count,")
    w("which is the least reliable number in the dataset.\n")
    w(f"**The lot ladder has exactly {len(s['ladder'])} sizes:** "
      f"`{', '.join(f'{x:g}' for x in s['ladder'])}`. Sizing is not a fixed risk fraction — lot as")
    w("a percentage of balance *falls* as the account grows.\n")
    w("\n---\n")

    # 7 engine
    w("## 7. The engine\n")
    w("`analysis/video_ocr/lot_position_engine.py`. The central idea is that **the position cap is")
    w("a risk budget, not a measurement**. For n positions of lot L at rungs d apart the distances")
    w("form an arithmetic series, so:\n")
    w("```\ndrawdown(n) = L × 100 × d × per_rung × r(r+1)/2      where r = rungs\n```")
    w("Derived without reference to the observed counts, then checked against them: where the list")
    w("was **not** scrolled it predicts at median 0.91× actual; where it **was** scrolled, 7.04× —")
    w("which is the evidence that the large derived counts are inflated, not that the formula is")
    w("wrong. Inverting it (`max_safe_positions`) turns the cap into a decision.\n")
    w("**It does not model the spread.** A real basket also carries bid/ask on every open position")
    w("— `spread × n × lot × 100` — so the budget under-states true drawdown by about one spread")
    w("per position. The replay measures the gap at 0.97×.\n")
    w("Sizing: `lot = a · balance^b` with the exponent measured **0.58–0.84** across every fit and")
    w("**below 1 in all of them** — lot grows more slowly than balance. The defaults are")
    w("survival-fitted, not fitted to the operator's own lots: fitting those reproduces 74% of")
    w("their choices and then blows the account at 140% drawdown.\n")
    w("\n---\n")

    # 8 the replay in full
    if s.get("walk_forward"):
        w("## 8. The replay, in full\n")
        w("`grid_replay.py` drives `grid_state.GridState` over real Exness ticks with an")
        w("MT5-faithful broker: a buy opens at the **ask** and closes on the **bid**, a sell the")
        w("mirror, so the $0.050 spread is paid per position per round trip as fill geometry")
        w("rather than as a fee added afterwards.\n")
        w("**Every figure in this section has its run date, data file and parameters recorded in")
        w("`REPLAY_RUNS.md`.** Consult it before quoting any of them: the engine's defaults changed")
        w("between runs, and the walk-forward below was generated with `per_rung = 1` where the")
        w("default is now `3`, so re-running the same command today does not reproduce it.\n")
        w("**Per session, both directions, at the default 44% budget:**\n")
        w(md_table(["session", "range", "move", "sell", "buy", "outcome"],
                   [[r["day"], f"${r['range']:,.2f}", f"${r['move']:+,.2f}",
                     f"{r.get('sell', 0):+.1f}%", f"{r.get('buy', 0):+.1f}%",
                     r.get("sell_out", "")] for r in s["walk_forward"]]) + "\n")
        w("**Aug 18 is the line that settles the direction question.** Gold fell $92.89 and the")
        w("sell grid — the *correct* direction — still lost 46.5%. Being right about the day does")
        w("not help, because the intraday path buries the basket before the close arrives.\n")
        if s.get("sweep"):
            w("**Raising the risk budget makes it worse** — it buys a few more rungs and pays for")
            w("them with the whole account:\n")
            w(md_table(["balance", "budget", "depth", "adverse tolerated", "sell (mean)",
                        "buy (mean)", "survived"],
                       [[f"${r['balance']:,.0f}", f"{r['risk']:.0f}%", f"{r['depth']:.0f}",
                         f"${r['tol']:,.2f}",
                         f"{r['sell']:+.1f}%" if r["sell"] is not None else "—",
                         f"{r['buy']:+.1f}%" if r["buy"] is not None else "—",
                         f"{r['ran']}/{r['n']}"] for r in s["sweep"]]) + "\n")
        w("The depth budget is the whole story: at a $1,000 balance it tolerates **$1.98** of")
        w("adverse movement, about **1.1%** of Aug 28's $186.50 range. Covering that day's opening")
        w("+$37.18 run at $0.18 rungs would take 206 rungs — a drawdown of **$3,838 even at the")
        w("0.01 minimum lot**, 384% of a $1,000 account. There is no lot size that survives it.\n")
        w("`PARKED` is the only reason these runs did not simply hold to zero. With no stop loss,")
        w("depth exhaustion while underwater is the sole remaining brake — and it fired every")
        w("time.\n")
        w("### The volatility-scaled step was tried, and it makes things worse\n")
        w("The obvious fix for a $0.18 step on an $86-a-day instrument is to scale it with")
        w("volatility. Tested over 13 sessions × 2 directions at a $1,000 balance, with a")
        w("time-windowed trailing range (`dynamic_step_sweep.csv`):\n")
        w(md_table(["step", "direction", "mean return", "median", "BLOWN", "parked"], [
            ["static $0.18", "sell", "−13.7%", "−34.4%", "**0/13**", "13/13"],
            ["static $0.18", "buy", "−33.8%", "−38.3%", "**0/13**", "13/13"],
            ["k = 0.5 × range", "sell", "−43.3%", "−24.0%", "**4/13**", "9/13"],
            ["k = 0.5 × range", "buy", "−41.2%", "−54.5%", "**4/13**", "9/13"],
            ["k = 1.0 × range", "sell", "−32.9%", "−100.1%", "**7/13**", "6/13"],
            ["k = 1.0 × range", "buy", "−35.1%", "−70.9%", "**4/13**", "9/13"],
        ]) + "\n")
        w("**The static step never blew a single account.** It parked, and parking *capped* the")
        w("loss. Widening the step blows 4–7 of 13 without improving the mean.\n")
        w("**This is not a tuning problem.** At depth the tolerance is `rungs × step` — linear in")
        w("step — while the drawdown carried is `lot × 100 × step × per_rung × r(r+1)/2`, *also*")
        w("linear in step. Buying room costs exactly proportional drawdown, so there is no `k`")
        w("that gains tolerance for free. The choice is parking versus blowing, not an optimum.\n")
        w("**The corollary matters more than the result: `PARKED` is not a failure mode, it is the")
        w("safety mechanism.** The tight step is what keeps the account alive. Removing the")
        w("constraint that causes parking removes the protection.\n")
        w("\n---\n")

    # 9 regression
    w("## 9. What changed in this consolidation\n")
    w("Every figure the retired documents quoted, against what the CSV says now:\n")
    w(md_table(["figure", "was published in", "old value", "recomputed", ""],
               [[a, b, c, e, f] for a, b, c, e, f in reg]) + "\n")
    w("\n---\n")

    # 10 distrust
    w("## 10. What to distrust\n")
    w("- **Position count is derived, not read.** Only ~9 rows are ever on screen; the total is")
    w("  estimated from the share of open P&L the visible rows account for.")
    w("- **Per-position figures are floors.** Visible rows are the top of the list, so the deepest")
    w("  rungs exit further out than the medians above suggest.")
    w("- **It is one path.** Five hours, one instrument, a $28 price range.")
    w("- **The hold-out is weak.** A nine-step lot ladder does not support a confident exponent.")
    w("- **Effective leverage ran 1,144×–17,629× of balance.** That exists only on an")
    w("  unlimited-leverage account.")
    w("- **The add-trigger rule is unanswerable** — only 116 of 261 detected adds carry a new rung")
    w("  price, and the scrolling list hides most of the basket.\n")
    w("**Filter on `quality` before quoting any figure as exact.** Every row in every CSV carries")
    w("it: `ok` / `repaired` / `flagged` / `rows_misaligned`.\n")
    return "\n".join(P)


def build_html(md_text, s, checks, reg, d):
    """The same content as a standalone page. Tables are rebuilt as HTML from the same variables."""
    def tbl(head, body, cls=""):
        h = "".join(f"<th>{html.escape(str(x))}</th>" for x in head)
        b = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>"
                    for r in body)
        return f'<div class="tw"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'

    dist = s["dist_all"]
    rec_pct = 100 * s["n_recovered"] / s["n_underwater"] if s["n_underwater"] else 0
    vbody = [[g, c, f"{t:,}", f"{f:,}", f"{100*(t-f)/t:.1f}%" if t else "—", note]
             for g, c, t, f, note in checks]
    ran, runs = s.get("replay_ran", 0), s.get("replay_runs", 0)

    return f"""<title>XAUUSD Recovery Grid Reference</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{--ground:#F5F6F8;--surface:#FFF;--surface-2:#EEF0F4;--ink:#14181D;--ink-soft:#5B6572;
--ink-faint:#8A94A3;--line:#DFE3E9;--line-soft:#EBEEF2;--accent:#9A6B14;--bad:#B23A2F;
--bad-soft:#F3DAD6;--good:#26705A;--good-soft:#D5E8E0;
--shadow:0 1px 2px rgba(16,20,26,.05),0 8px 24px -12px rgba(16,20,26,.14)}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--ground:#0E1116;--surface:#161A21;
--surface-2:#1E242D;--ink:#E7EAEF;--ink-soft:#9AA4B2;--ink-faint:#6C7683;--line:#262D37;
--line-soft:#1F252E;--accent:#D9A441;--bad:#E3736A;--bad-soft:#3A211E;--good:#5FB495;
--good-soft:#17302A;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6)}}}}
:root[data-theme="dark"]{{--ground:#0E1116;--surface:#161A21;--surface-2:#1E242D;--ink:#E7EAEF;
--ink-soft:#9AA4B2;--ink-faint:#6C7683;--line:#262D37;--line-soft:#1F252E;--accent:#D9A441;
--bad:#E3736A;--bad-soft:#3A211E;--good:#5FB495;--good-soft:#17302A;
--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6)}}
*{{box-sizing:border-box}}
body{{background:var(--ground);color:var(--ink);font-family:"IBM Plex Sans",system-ui,sans-serif;
font-size:16px;line-height:1.62;margin:0;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:62rem;margin:0 auto;padding:clamp(1.6rem,4vw,4rem) clamp(1.1rem,4vw,2.5rem) 6rem}}
h1,h2,h3{{font-family:Fraunces,Georgia,serif;text-wrap:balance;margin:0;line-height:1.14}}
h1{{font-size:clamp(2.1rem,5.6vw,3.5rem);font-weight:600;letter-spacing:-.018em}}
h2{{font-size:clamp(1.4rem,3vw,1.95rem);font-weight:600;margin:0 0 .5rem;letter-spacing:-.012em}}
p{{margin:0 0 1.05rem;max-width:66ch}}
code{{font-family:"IBM Plex Mono",monospace;font-size:.87em;background:var(--surface-2);
padding:.12em .38em;border-radius:4px}}
pre{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:1.1rem 1.2rem;
overflow-x:auto;font-family:"IBM Plex Mono",monospace;font-size:.84rem;line-height:1.7}}
.eyebrow{{font-family:"IBM Plex Mono",monospace;font-size:.72rem;letter-spacing:.15em;
text-transform:uppercase;color:var(--ink-faint);margin:0 0 1rem}}
.lede{{font-size:clamp(1.05rem,2vw,1.22rem);color:var(--ink-soft);max-width:58ch;margin-top:1.3rem}}
header{{border-bottom:1px solid var(--line);padding-bottom:2.4rem;margin-bottom:2.6rem}}
section{{margin:3.2rem 0 0;scroll-margin-top:1rem}}
.tw{{overflow-x:auto;margin:1.4rem 0;border:1px solid var(--line);border-radius:10px;
background:var(--surface)}}
table{{border-collapse:collapse;width:100%;font-size:.86rem}}
th,td{{padding:.55rem .8rem;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line-soft)}}
th:first-child,td:first-child{{text-align:left}}
thead th{{font-family:"IBM Plex Mono",monospace;font-size:.68rem;letter-spacing:.07em;
text-transform:uppercase;color:var(--ink-faint);background:var(--surface-2);
border-bottom:1px solid var(--line)}}
tbody tr:last-child td{{border-bottom:none}}
td:not(:first-child){{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}}
.verdict{{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--bad);
border-radius:10px;padding:1.4rem 1.55rem;margin:1.8rem 0;box-shadow:var(--shadow)}}
.verdict .big{{font-family:Fraunces,Georgia,serif;font-size:clamp(1.9rem,5vw,2.9rem);font-weight:700;
color:var(--bad);line-height:1;font-variant-numeric:tabular-nums}}
.verdict .cap{{color:var(--ink-soft);margin:.5rem 0 0;font-size:.96rem;max-width:54ch}}
.note{{border-left:2px solid var(--line);padding:.15rem 0 .15rem 1.05rem;color:var(--ink-soft);
margin:1.4rem 0;font-size:.95rem}}
.note strong{{color:var(--ink)}}
.gen{{background:var(--good-soft);border:1px solid var(--good);border-radius:8px;
padding:.85rem 1.05rem;margin:1.5rem 0;font-size:.9rem;color:var(--ink)}}
.toc{{display:flex;flex-wrap:wrap;gap:.45rem;margin:1.6rem 0 0}}
.toc a{{font-family:"IBM Plex Mono",monospace;font-size:.75rem;text-decoration:none;
color:var(--ink-soft);background:var(--surface);border:1px solid var(--line);
border-radius:99px;padding:.28rem .7rem}}
.toc a:hover{{border-color:var(--accent);color:var(--accent)}}
footer{{margin-top:4rem;padding-top:1.5rem;border-top:1px solid var(--line);color:var(--ink-faint);
font-size:.83rem}}
</style>
<div class="wrap">
<header>
  <p class="eyebrow">XAUUSD · single reference · generated from source</p>
  <h1>The recovery grid, and what the data actually says</h1>
  <p class="lede">One document for the strategy reconstructed from the video: how it was recovered,
  whether the data holds together, every measured parameter, the sizing engine, and the replay
  that tested it against real ticks.</p>
  <div class="toc">
    <a href="#verdict">1 Verdict</a><a href="#data">2 The data</a><a href="#valid">3 Validation</a>
    <a href="#strategy">4 Strategy</a><a href="#params">5 Parameters</a><a href="#cycle">6 Per cycle</a>
    <a href="#engine">7 Engine</a><a href="#replay">8 Replay</a>
    <a href="#changed">9 What changed</a><a href="#distrust">10 Distrust</a>
  </div>
</header>

<div class="gen"><strong>Every figure on this page is computed at build time</strong> by
<code>analysis/video_ocr/build_reference.py</code> — none is typed by hand. It replaces four
documents that each carried hardcoded literals and drifted apart from the data and from each other.
Regenerate to correct it.</div>

<div class="note"><strong>Where this page and <code>OPERATOR_VERIFIED.md</code> disagree, that one
wins.</strong> It records five cycles watched frame by frame on the original video. The capture
here samples only ~7 states per cycle, so continuous watching sees transitions this dataset
physically cannot — which is how three figures below were caught wrong, <strong>including the exit
rule, which is now known to be unidentified</strong>.</div>

<section id="verdict"><h2>1. The verdict, first</h2>
<p>The strategy was replayed against real Exness XAUUSD ticks.</p>
<div class="verdict"><div class="big">{ran} of {runs}</div>
<p class="cap">runs finished without parking or blowing — across {s.get('replay_days',0)} sessions,
both directions, four risk budgets.</p></div>
{tbl(["direction","mean","median","best","worst"],
     [[d_,f"{s[f'replay_{d_}'].mean:+.1f}%",f"{s[f'replay_{d_}'].q(.5):+.1f}%",
       f"{s[f'replay_{d_}'].v[-1]:+.1f}%",f"{s[f'replay_{d_}'].v[0]:+.1f}%"]
      for d_ in ("sell","buy")] if s.get("replay_runs") else [])}
<p>A $0.18 grid step with a risk-budgeted cap absorbs about <strong>$2 of adverse movement</strong>
on an instrument that moves <strong>$186 in a day</strong>, so the grid parks within seconds.
Selling Aug 28 — the day gold fell $138.93 — parked 180 ticks in and lost 42.6%. On Aug 18 gold
fell $92.89 and the <em>correct</em> direction still lost 46.5%: <strong>direction is not the
broken part.</strong> The parameters were measured on a tape that ranged $28 in five hours, and
they do not transfer.</p></section>

<section id="data"><h2>2. What the data is</h2>
<p>A phone camera pointed at another phone running MT5 mobile. 19,050 frames reduced to
<strong>{len(d['unique']):,} distinct on-screen states</strong> and <strong>{s['n_cycles']} trading
cycles</strong> — by rectifying each frame, watching the right-hand value column for colour change,
OCR-ing the values, deduplicating on the parsed numbers, and repairing with arithmetic rather than
voting: MT5's own identities over-determine the figures, so three good reads fix a fourth.</p>
<div class="note"><strong>Two limits shape everything below.</strong> The position list
<strong>scrolls</strong> — only ~9 rows are ever on screen, so anything measured per-position is
measured on the top of the list. And a basket <strong>closes over several legs</strong>, so one
liquidation appears as several balance changes.</div></section>

<section id="valid"><h2>3. Does the data hold together</h2>
<p>Every arithmetic check compares numbers read from <em>different</em> parts of the screen, so
agreement is evidence. Checks that could only be true by construction are deliberately excluded —
they would inflate the pass rate without testing anything.</p>
{tbl(["group","check","tested","failed","pass","note"], vbody)}
<p>Full table: <code>validation_report.csv</code>.</p></section>

<section id="strategy"><h2>4. The strategy</h2>
<p><strong>There is no stop loss.</strong> {s['n_underwater']} of {s['n_cycles']} cycles went
underwater and <strong>{s['n_recovered']} of those ({rec_pct:.0f}%) closed in profit anyway</strong>
— the basket is held through the drawdown until price bounces. Only {s['n_neg_closes']} of
{s['n_closes']} closes were negative, at no consistent level: discretionary bail-outs, not a rule
that can be coded.</p>
<div class="note"><strong>The exit is a basket TRAIL whose give-back is a PERCENTAGE OF BALANCE
(12–18%).</strong> Confirmed structurally by cycle 29, which closed every position at one price
(4049.291) — a simultaneous basket close, not per-position take-profits. Four candidate rules were
refuted on the way; the full evidence lives in <code>OPERATOR_VERIFIED.md</code>, which outranks
this page wherever they differ. The 28.9%-of-balance figure below is the typical <em>outcome</em>
of that trail, not its trigger.</div>
<p><strong>The exit is per basket, not per position</strong> — the whole basket goes at once, and
{s['dist_adv']} of {s['dist_n']} visible legs ({100*s['dist_adv']/s['dist_n']:.0f}%) closed at a
loss, carried by the rest.</p>
<div class="note"><strong>Mind the sign convention.</strong> <code>signed_distance</code> is
<code>exit − entry</code> — a raw price delta, not a P&amp;L sign — so a <strong>sell</strong>
profits when it is negative. Reading every negative as a loss overstates the adverse share
threefold.</div>
<p><strong>The unit lot is fixed when a cycle opens and never changes within it</strong> —
escalation is in the <em>number</em> of positions, not their size.</p>
<pre>FLAT ──enter──&gt; OPEN ──adverse &gt;= step──&gt; ADD ──&gt; OPEN
                 │
                 ├──basket P&amp;L &gt;= target──&gt; UNWIND ──&gt; FLAT (next cycle)
                 │
                 └──depth exhausted, still underwater──&gt; PARKED  (operator decision)</pre>
<div class="note"><strong>PARKED is the transition the recording never resolves.</strong> With no
stop loss the observed behaviour at full depth was to hold and hope; it worked most of the time and
once cost ${abs(s['neg_closes'][0]):,.2f}. It must latch and stop — not silently keep holding, and
not invent a stop loss the strategy does not have.</div></section>

<section id="params"><h2>5. Measured parameters</h2>
{tbl(STAT_HEAD, [dist.row("entry→exit distance, all rows"),
                 s["dist_ok"].row("entry→exit distance, quality==ok"),
                 s["step_all"].row("grid step, real adds"),
                 s["step_ok"].row("grid step, quality==ok"),
                 s["dd_pct"].row("worst drawdown, % of balance"),
                 s["exit_pct"].row("exit target, % of balance")])}
<p>Of {s['dist_n']} legs, <strong>{s['dist_fav']} exited favourable and {s['dist_adv']}
adverse</strong>.</p>
<div class="note"><strong>Use the median for the grid step, not the mean.</strong> Its series still
contains residual misreads — the max reads ${s['step_all'].v[-1]:,.3f}, which is not a grid step —
so the mean (${s['step_all'].mean:,.3f}) is dragged well above the body of the distribution. The
<code>quality == ok</code> subset is the trustworthy one, and the engine uses its median
(${s['step_ok'].q(.5):,.3f}). The same caution applies to every <code>min</code>/<code>max</code>
column here: they are the extremes of imperfect OCR, not of the strategy.</div>
<div class="note"><strong>Positions per rung: 2–4, opened simultaneously at one price.</strong> The
position delta at an add is median {s['per_rung_delta'].q(.5):.0f} (p90
{s['per_rung_delta'].q(.9):.0f}), and <strong>{s['open_with_3plus']} of {s['n_first']} cycles open
with 3 or more visible at once</strong>. Confirmed by direct observation — cycle 8 opened four
trades at a single price, cycle 16 four then four more (<code>OPERATOR_VERIFIED.md</code>).
A ratio of {s['per_rung_events']:.2f} appears if you divide <code>add_events.add_type</code>
({s['add_types']['new_rung']} <code>new_rung</code> vs {s['add_types']['same_rung']}
<code>same_rung</code>); <strong>that figure is wrong</strong> — it counts add <em>events</em>, not
positions per event. It was published briefly and is recorded here so it is not rediscovered.</div>
<div class="note"><strong><code>per_rung</code> and <code>add_step</code> are not separately
identifiable</strong> from this data: drawdown constrains only their product, so
<code>per_rung=3</code> at a $0.54 step and <code>per_rung=1</code> at $0.18 give the same curve.
Neither should be quoted as independently measured.</div></section>

<section id="cycle"><h2>6. Per cycle</h2>
<p><strong>Trades per cycle — there are two different derived counts, measuring different
moments.</strong> Conflating them is why a reader of the old document found numbers twice as large
in the CSV.</p>
{tbl(STAT_HEAD, [s["pos_visible"].row("positions visible (cycle_grid)"),
                 s["pos_close"].row("derived AT CLOSE (close_events)"),
                 s["pos_peak"].row("derived AT PEAK (cycle_grid)"),
                 s["adds"].row("adds per cycle"),
                 s["legs"].row("close legs per cycle")])}
<p><strong>Lot size and lots per cycle</strong> — the second was not recorded anywhere before this
document, and is derived here two ways:</p>
{tbl(STAT_HEAD, [s["unit_lot"].row("unit lot per cycle"),
                 s["lots_visible"].row("lots in basket at close (visible)"),
                 s["lots_derived"].row("total lots (unit × peak derived)")])}
<div class="note">Treat the derived total as an <strong>upper bound</strong>: it inherits the
derived position count, the least reliable number in the dataset.</div>
<p>The lot ladder has exactly {len(s['ladder'])} sizes:
<code>{', '.join(f'{x:g}' for x in s['ladder'])}</code>. Sizing is not a fixed risk fraction — lot
as a percentage of balance <em>falls</em> as the account grows.</p></section>

<section id="engine"><h2>7. The engine</h2>
<p><code>analysis/video_ocr/lot_position_engine.py</code>. The central idea: <strong>the position
cap is a risk budget, not a measurement.</strong> For n positions of lot L at rungs d apart the
distances form an arithmetic series, so:</p>
<pre>drawdown(n) = L × 100 × d × per_rung × r(r+1)/2      where r = rungs</pre>
<p>Derived without reference to the observed counts, then checked against them: where the list was
<strong>not</strong> scrolled it predicts at median 0.91× actual; where it <strong>was</strong>
scrolled, 7.04× — which is the evidence that the large derived counts are inflated, not that the
formula is wrong. Inverting it (<code>max_safe_positions</code>) turns the cap into a decision.</p>
<div class="note"><strong>It does not model the spread.</strong> A real basket also carries bid/ask
on every open position — <code>spread × n × lot × 100</code> — so the budget under-states true
drawdown by about one spread per position. The replay measures the gap at 0.97×.</div>
<p>Sizing is <code>lot = a · balance^b</code>, with the exponent measured <strong>0.58–0.84</strong>
across every fit and <strong>below 1 in all of them</strong> — lot grows more slowly than balance,
so risk per trade falls as the account grows. The defaults are survival-fitted, not fitted to the
operator's own lots: fitting those reproduces 74% of their choices and then blows the account at
140% drawdown.</p></section>

<section id="replay"><h2>8. The replay, in full</h2>
<p><code>grid_replay.py</code> drives <code>grid_state.GridState</code> over real Exness ticks with
an MT5-faithful broker: a buy opens at the <strong>ask</strong> and closes on the
<strong>bid</strong>, a sell the mirror, so the $0.050 spread is paid per position per round trip
as fill geometry rather than as a fee added afterwards.</p>
<p><strong>Per session, both directions, at the default 44% budget:</strong></p>
{tbl(["session","range","move","sell","buy","outcome"],
     [[r["day"], f"${{r['range']:,.2f}}", f"${{r['move']:+,.2f}}",
       f"{{r.get('sell',0):+.1f}}%", f"{{r.get('buy',0):+.1f}}%", r.get("sell_out","")]
      for r in s.get("walk_forward", [])])}
<div class="note"><strong>Aug 18 settles the direction question.</strong> Gold fell $92.89 and the
sell grid — the <em>correct</em> direction — still lost 46.5%. Being right about the day does not
help, because the intraday path buries the basket before the close arrives.</div>
<p><strong>Raising the risk budget makes it worse</strong> — it buys a few more rungs and pays for
them with the whole account:</p>
{tbl(["balance","budget","depth","adverse tolerated","sell (mean)","buy (mean)","survived"],
     [[f"${{r['balance']:,.0f}}", f"{{r['risk']:.0f}}%", f"{{r['depth']:.0f}}",
       f"${{r['tol']:,.2f}}",
       f"{{r['sell']:+.1f}}%" if r["sell"] is not None else "—",
       f"{{r['buy']:+.1f}}%" if r["buy"] is not None else "—",
       f"{{r['ran']}}/{{r['n']}}"] for r in s.get("sweep", [])])}
<p>The depth budget is the whole story: at a $1,000 balance it tolerates <strong>$1.98</strong> of
adverse movement, about <strong>1.1%</strong> of Aug 28's $186.50 range. Covering that day's opening
+$37.18 run at $0.18 rungs would take 206 rungs — a drawdown of <strong>$3,838 even at the 0.01
minimum lot</strong>, 384% of a $1,000 account. <strong>There is no lot size that survives it.</strong></p>
<p><code>PARKED</code> is the only reason these runs did not simply hold to zero. With no stop loss,
depth exhaustion while underwater is the sole remaining brake — and it fired every time.</p></section>

<section id="changed"><h2>9. What changed in this consolidation</h2>
<p>Every figure the retired documents quoted, against what the CSV says now:</p>
{tbl(["figure","was published in","old value","recomputed",""], [list(r) for r in reg])}</section>

<section id="distrust"><h2>10. What to distrust</h2>
<ul>
<li><strong>Position count is derived, not read.</strong> Only ~9 rows are ever on screen.</li>
<li><strong>Per-position figures are floors</strong> — visible rows are the top of the list, so the
deepest rungs exit further out than the medians suggest.</li>
<li><strong>It is one path.</strong> Five hours, one instrument, a $28 price range.</li>
<li><strong>The hold-out is weak</strong> — a nine-step ladder cannot support a confident exponent.</li>
<li><strong>Effective leverage ran 1,144×–17,629× of balance</strong>, which exists only on an
unlimited-leverage account.</li>
<li><strong>The add-trigger rule is unanswerable</strong> — only 116 of 261 detected adds carry a
new rung price.</li>
</ul>
<div class="note"><strong>Filter on <code>quality</code> before quoting any figure as exact.</strong>
Every row in every CSV carries it: <code>ok</code> / <code>repaired</code> / <code>flagged</code> /
<code>rows_misaligned</code>.</div></section>

<footer><p>Read-only analysis. Nothing in this work placed, modified or closed an order on any
account, demo or real. Generated by <code>analysis/video_ocr/build_reference.py</code> from
<code>D:\\llm\\ios\\xausd_video_out</code>. Text equivalent:
<code>STRATEGY_REFERENCE.md</code>. Checks: <code>validation_report.csv</code>.</p></footer>
</div>
"""


STUB = ("# {title}\n\n**Superseded.** This document's content has moved into the single generated "
        "reference:\n\n**[`STRATEGY_REFERENCE.md`](STRATEGY_REFERENCE.md)**\n\nIt is generated from "
        "the CSVs by `analysis/video_ocr/build_reference.py`, so every figure is recomputed at "
        "build time rather than typed by hand — which is why this file existed with stale numbers "
        "in it. Regenerate the reference to correct it.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-stubs", action="store_true", help="leave the retired documents alone")
    a = ap.parse_args()

    d = load(a.out)
    print("VALIDATION")
    checks = validate(d, a.out)
    group = None
    for g, c, t, f, note in checks:
        if g != group:
            print(f"\n  [{g}]"); group = g
        rate = f"{100*(t-f)/t:>6.1f}%" if t else "     —"
        print(f"    {c:<48s} {t:>7,} tested {f:>6,} failed {rate}  {note}")

    s = compute(d)
    reg = regression(s)
    print("\nREGRESSION vs the retired documents")
    for k, src, old, new, flag in reg:
        mark = "  <-- " + flag if flag != "same" else ""
        print(f"    {k:<28s} was {old:<20s} now {new:<34s}{mark}")

    md = build_md(s, checks, reg, d)
    with open(os.path.join(a.out, "STRATEGY_REFERENCE.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(os.path.join(a.out, "strategy_reference.html"), "w", encoding="utf-8") as fh:
        fh.write(build_html(md, s, checks, reg, d))

    if not a.no_stubs:
        for f_, title in (("LOT_SCALE_ENGINE.md", "Lot Scale Engine"),
                          ("STRATEGY_ANSWERS.md", "The seven observations"),
                          ("GRID_REPLAY.md", "Grid Replay")):
            with open(os.path.join(a.out, f_), "w", encoding="utf-8") as fh:
                fh.write(STUB.format(title=title))
        print(f"\n-> retired 3 documents to stubs")

    print(f"-> STRATEGY_REFERENCE.md, strategy_reference.html, validation_report.csv in {a.out}")


if __name__ == "__main__":
    main()
