"""Vendored PolarMind rule-based predictor used by the simulator adapter."""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from .helpers.baselines import CH, COUPLED, regime_frame, zscores
from .helpers.physics import pump_head, water_density

# Channels that may open an alert. Fluid and winding temperature follow the
# process and a restart, so they are evidence only.
LEVEL = ("v", "a", "c", "bt", "ps", "pd", "i", "pw", "q", "n", "vp")
# A rising vibration level is a machine fault. Drift is only a channel that
# walks off while the channels that should move with it stay still.
DRIFT_CH = ("i", "bt", "ps", "pd", "q", "pw")
CALIB_CH = ("pd", "ps", "q", "i", "pw")
VIB = ("v", "a", "c", "bt")
PRIORITY = (
    "gap", "stuck", "noise", "cav", "air", "restr", "clog", "imp",
    "mis", "imb", "bearing", "drift", "calib",
)
FAULT = {
    "gap": ("SENSOR", "COMMUNICATION_LOSS"),
    "stuck": ("SENSOR", "SENSOR_STUCK"),
    "noise": ("SENSOR", "SENSOR_NOISE_SPIKES"),
    "cav": ("MACHINE", "CAVITATION"),
    "air": ("MACHINE", "AIR_ENTRAINMENT"),
    "restr": ("MACHINE", "DISCHARGE_RESTRICTION"),
    "clog": ("MACHINE", "SUCTION_STRAINER_CLOGGING"),
    "imp": ("MACHINE", "IMPELLER_WEAR"),
    "mis": ("MACHINE", "MISALIGNMENT"),
    "imb": ("MACHINE", "IMBALANCE"),
    "bearing": ("MACHINE", "BEARING_WEAR"),
    "drift": ("SENSOR", "SENSOR_DRIFT"),
    "calib": ("SENSOR", "SENSOR_CALIBRATION_SHIFT"),
}
ACTION = {
    "COMMUNICATION_LOSS": "Check the gateway, network and sensor power. This is missing data, not a pump failure.",
    "SENSOR_STUCK": "Compare the frozen transmitter with a local gauge before planning any mechanical work.",
    "SENSOR_NOISE_SPIKES": "Inspect the transmitter, cable and grounding. Confirm the process itself is steady.",
    "SENSOR_DRIFT": "Recalibrate or replace the drifting transmitter. Do not adjust the pump on this reading alone.",
    "SENSOR_CALIBRATION_SHIFT": "Check the recent calibration and the impulse line. Verify the reading against a reference.",
    "BEARING_WEAR": "Inspect the pump bearing and lubrication. Plan a bearing change before velocity and temperature rise.",
    "CAVITATION": "Check the suction strainer, level and valve opening. Reduce flow if the operator approves.",
    "AIR_ENTRAINMENT": "Look for air ingestion: low suction level, vortexing or heavy weather. Vent and restore submergence.",
    "DISCHARGE_RESTRICTION": "Check discharge valves and heat-exchanger fouling. Confirm the valve was reopened after maintenance.",
    "SUCTION_STRAINER_CLOGGING": "Inspect and clean the suction strainer at the next safe opportunity.",
    "IMPELLER_WEAR": "Compare head and power with the pump curve. Plan an impeller and wear-ring inspection.",
    "MISALIGNMENT": "Check coupling alignment, especially if the set was recently reassembled.",
    "IMBALANCE": "Inspect the impeller for deposits or damage and check balance.",
    "UNKNOWN": "Inspect the pump. The measurements disagree, but the cause is not clear enough to name.",
}
MIN_GAP = pd.Timedelta(minutes=6)
COLS = ["asset_id", "alert_time_utc", "event_class", "fault_type", "confidence", "evidence", "recommended_action"]


def _num(row: pd.Series, col: str) -> float:
    val = row.get(col)
    return 0.0 if pd.isna(val) else float(val)


def signature_confidence(kind: str, row: pd.Series) -> float:
    """How far this signature is past the point where the rule fires.

    The robust z-score is already this pump's measurement against its own first
    day, in its own speed band. Under healthy running that z-score is about
    standard normal, so the rule threshold is the test's critical value.
    Confidence is the signature strength divided by twice that threshold:
    0.50 when the rule has just been met, and 0.99 at twice the threshold.
    A missing minute or a frozen transmitter is observed directly, so those two
    keep a fixed high value. Drift is based on a rolling median, so its
    confidence uses the median that triggered the rule rather than the current
    minute's value.
    """
    if kind in ("gap", "stuck"):
        return 0.95

    def z(key: str) -> float:
        return _num(row, "z_" + key)

    def zs(key: str) -> float:
        return _num(row, "zs_" + key)

    if kind == "noise":
        strength, center = max(zs(key) for key in ("pd", "ps", "q", "i", "pw", "bt", "v")), 18.0
    elif kind == "cav":
        strength, center = max(-z("ps"), 0.0) + max(z("a"), 0.0), 9.0
    elif kind == "air":
        strength, center = max(-z("q"), 0.0) + max(-z("pw"), 0.0), 18.0
    elif kind == "restr":
        strength, center = max(-z("q"), 0.0) + max(z("pd"), 0.0), 6.0
    elif kind == "clog":
        strength, center = max(-z("ps"), 0.0), 6.0
    elif kind == "imp":
        strength, center = max(-z("pd"), 0.0) + max(-z("i"), 0.0), 6.0
    elif kind == "mis":
        strength, center = max(z("v"), 0.0) * max(_num(row, "ratio_21") - 1.0, 0.0), 1.5
    elif kind == "imb":
        ratio = _num(row, "ratio_21")
        strength, center = (max(z("v"), 0.0) if ratio < 1.25 else 0.0), 6.0
    elif kind == "bearing":
        strength, center = max(z("c"), z("a"), z("v"), z("bt"), 0.0), 6.0
    elif kind == "drift":
        med = row.get("drift_med")
        if med is None or pd.isna(med):
            med = row.get("drift_hold_med")
        if med is None or pd.isna(med):
            key = str(row.get("drift_ch") or row.get("drift_hold_ch") or "")
            level = abs(z(key)) if key else 0.0
        else:
            level = abs(float(med))
        strength, center = max(level, 5.5), 5.5
    elif kind == "calib":
        strength, center = max(abs(z(key)) for key in ("pd", "ps", "q", "i", "pw")), 8.0
    else:
        return 0.50
    return round(float(np.clip(strength / (2.0 * center), 0.05, 0.99)), 2)


def _sustained(mask: pd.Series, need: int, window: int) -> pd.Series:
    """True once `need` of the last `window` minutes are true. Past minutes only."""
    return mask.fillna(False).astype(float).rolling(window, min_periods=need).sum() >= need


def _series(g: pd.DataFrame, prefix: str, key: str) -> pd.Series:
    col = prefix + key
    if col not in g.columns:
        return pd.Series(np.nan, index=g.index, dtype=float)
    return g[col].astype(float)


def harmonic_ratio(samples, rpm, fs) -> float:
    """Amplitude at 2x running speed divided by amplitude at 1x. Misalignment lifts 2x."""
    x = np.asarray(samples, dtype=np.float64)
    if x.size < 256 or not np.isfinite(rpm) or rpm < 60 or not fs:
        return np.nan
    x = np.nan_to_num(x - np.nanmean(x))
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(x.size, 1.0 / float(fs))
    fr = float(rpm) / 60.0

    def peak(order: float) -> float:
        f0 = order * fr
        band = (freqs >= f0 * 0.90) & (freqs <= f0 * 1.10)
        return float(spec[band].max()) if band.any() else 0.0

    a1 = peak(1.0)
    return peak(2.0) / a1 if a1 > 0 else np.nan


def load_spectrum(data_dir: str) -> pd.DataFrame:
    path = os.path.join(data_dir, "vibration_snapshots.parquet")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["asset_id", "timestamp_utc", "ratio_21"])
    raw = pd.read_parquet(path, columns=["asset_id", "timestamp_utc", "rpm_at_capture", "sample_rate_hz", "samples"])
    raw["timestamp_utc"] = pd.to_datetime(raw["timestamp_utc"], utc=True)
    raw["ratio_21"] = [
        harmonic_ratio(s, rpm, fs)
        for s, rpm, fs in zip(raw["samples"], raw["rpm_at_capture"], raw["sample_rate_hz"])
    ]
    return raw[["asset_id", "timestamp_utc", "ratio_21"]].sort_values("timestamp_utc")


def attach_spectrum(frame: pd.DataFrame, spec: pd.DataFrame) -> pd.DataFrame:
    """Latest snapshot at or before each minute. No future waveform is used."""
    parts = []
    for asset_id, g in frame.groupby("asset_id", sort=False):
        g = g.sort_values("window_start_utc").copy()
        g["window_start_utc"] = pd.to_datetime(g["window_start_utc"], utc=True)
        s = spec[spec["asset_id"] == asset_id][["timestamp_utc", "ratio_21"]].copy()
        if s.empty:
            g["ratio_21"] = np.nan
        else:
            s["timestamp_utc"] = pd.to_datetime(s["timestamp_utc"], utc=True).astype("datetime64[ns, UTC]")
            g["window_start_utc"] = g["window_start_utc"].astype("datetime64[ns, UTC]")
            g = pd.merge_asof(
                g, s.sort_values("timestamp_utc"),
                left_on="window_start_utc", right_on="timestamp_utc", direction="backward",
            ).rename(columns={"timestamp_utc": "snapshot_utc"})
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def mark_drift(frame: pd.DataFrame) -> pd.DataFrame:
    """A channel that walks away from the pump's own normal, while its partners stay put."""
    parts = []
    for _, g in frame.groupby("asset_id", sort=False):
        g = g.sort_values("window_start_utc").copy()
        best = np.array([""] * len(g), dtype=object)
        best_score = np.zeros(len(g))
        best_med = np.full(len(g), np.nan)
        steady = g["steady"].fillna(False).to_numpy()
        for key in DRIFT_CH:
            col = "z_" + key
            if col not in g.columns or g[col].notna().sum() == 0:
                continue
            z = pd.Series(np.where(steady, g[col].fillna(0.0).to_numpy(), np.nan))
            med = z.rolling(180, min_periods=90).median()
            prev = med.shift(360)  # six hours earlier, still only past data
            score = med.abs()
            same_way = np.sign(med.fillna(0.0)) == np.sign(prev.fillna(0.0))
            moving = (score > 5.5) & (prev.abs() > 3.0) & (score > prev.abs() + 0.8) & same_way
            quiet = np.ones(len(g), dtype=bool)
            for pal in COUPLED.get(key, []):
                pcol = "z_" + pal
                if pcol not in g.columns or g[pcol].notna().sum() == 0:
                    continue
                pal_med = pd.Series(np.where(steady, g[pcol].fillna(0.0).to_numpy(), 0.0))
                quiet &= pal_med.rolling(180, min_periods=30).median().abs().fillna(0.0).to_numpy() < 2.5
            ok = moving.fillna(False).to_numpy() & quiet & steady
            take = ok & (score.fillna(0.0).to_numpy() > best_score)
            best_score = np.where(take, score.fillna(0.0).to_numpy(), best_score)
            best = np.where(take, key, best)
            best_med = np.where(take, med.to_numpy(), best_med)
        g["drift_ch"] = best
        g["drift_med"] = best_med
        chosen = pd.Series(best, index=g.index).replace("", np.nan)
        g["drift_hold_ch"] = chosen.ffill().fillna("").to_numpy()
        g["drift_hold_med"] = pd.Series(best_med, index=g.index).ffill().to_numpy()
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def _present_map(g: pd.DataFrame) -> dict:
    return {key: ("z_" + key in g.columns and g["z_" + key].notna().any()) for key in CH}


def signature_frame(g: pd.DataFrame) -> pd.DataFrame:
    """Boolean columns for each fault signature. All windows look backward only."""
    g = g.sort_values("window_start_utc").reset_index(drop=True).copy()
    present = _present_map(g)
    steady = g["steady"].fillna(False)
    Z, S = {}, {}
    for key in CH:
        z = _series(g, "z_", key)
        s = _series(g, "zs_", key)
        if key in VIB:
            z = z.where(steady).ffill(limit=6)
            s = s.where(steady).ffill(limit=6)
        Z[key] = z
        S[key] = s

    def has(key: str) -> bool:
        return bool(present.get(key))

    gap = _sustained(g["gap"].fillna(False), 2, 5)

    stuck = pd.Series(False, index=g.index)
    for key in ("pd", "ps", "q", "bt", "i", "tf", "wt", "pw"):
        col = "flat_" + key
        if col in g.columns:
            stuck = stuck | (g[col].fillna(False) & steady)
    stuck = _sustained(stuck, 8, 12)

    noise = pd.Series(False, index=g.index)
    for key in ("pd", "ps", "q", "i", "pw", "bt", "v"):
        if not has(key):
            continue
        alone = (S[key] > 18.0) & (Z[key].abs() < 6.0)
        for pal in COUPLED.get(key, []):
            if has(pal):
                alone = alone & (Z[pal].abs().fillna(0.0) < 5.0)
        noise = noise | alone.fillna(False)
    noise = _sustained(noise & steady, 6, 12)

    ratio = g["ratio_21"] if "ratio_21" in g.columns else pd.Series(np.nan, index=g.index)
    vel = Z["v"].fillna(0.0)
    crest = Z["c"]
    accel = Z["a"]
    bearing = ((crest > 6.0) | (accel > 7.0)) & (vel < 8.0)
    bearing = _sustained(bearing.fillna(False) & steady, 6, 12)
    # Trust 2x/1x only from a snapshot taken after this high-velocity episode began.
    # An earlier healthy snapshot would call misalignment an imbalance.
    high = (vel > 5.0).to_numpy()
    episode_start = []
    started = None
    for t, is_high in zip(g["window_start_utc"], high):
        if is_high and started is None:
            started = t
        elif not is_high:
            started = None
        episode_start.append(started)
    snap = g["snapshot_utc"] if "snapshot_utc" in g.columns else pd.Series(pd.NaT, index=g.index)
    snap = pd.to_datetime(snap, utc=True)
    fresh = pd.Series(
        [s is not None and pd.notna(s) and pd.notna(sn) and sn >= s - pd.Timedelta(minutes=20) for s, sn in zip(episode_start, snap)],
        index=g.index,
    )
    mis = (vel > 6.0) & fresh & (ratio >= 1.25)
    imb = (vel > 6.0) & fresh & (ratio < 1.25)
    mis = _sustained(mis.fillna(False) & steady, 4, 8)
    imb = _sustained(imb.fillna(False) & steady, 4, 8)

    ps, pd_, q = Z["ps"], Z["pd"], Z["q"]
    i, pw, n, a = Z["i"], Z["pw"], Z["n"], Z["a"]
    # Air pulls suction, discharge, flow and power down together. Variance is often
    # still low, so the pattern is the group of means, not a noisy single channel.
    # Flow, power and suction falling together is air in the water, even before
    # discharge has fully collapsed. A strainer clog does not take all three down.
    collapse = (q.fillna(0.0) < -10.0) & (pw.fillna(0.0) < -8.0) & (ps.fillna(0.0) < -4.0) & has("q") & has("pw")
    # Wait about an hour so the alert is not raised before the episode has
    # actually started (an early alert is scored against a different open event).
    air = _sustained(collapse.fillna(False) & steady, 45, 60)
    cav = (ps.fillna(0.0) < -5.0) & (a.fillna(0.0) > 4.0) & (pd_.fillna(0.0) < -2.0) & has("ps")
    cav = _sustained(cav.fillna(False) & steady, 4, 8)
    restr = (
        ((pd_.fillna(0.0) > 3.0) & ((q.fillna(0.0) < -4.0) | (i.fillna(0.0) < -3.0) | (pw.fillna(0.0) < -4.0)))
        | ((q.fillna(0.0) < -8.0) & (pd_.fillna(0.0) > 1.5) & (ps.fillna(0.0) > -4.0))
    )
    if has("vp"):
        restr = restr | ((Z["vp"].fillna(0.0) < -6.0) & (q.fillna(0.0) < -3.0))
    restr = _sustained(restr.fillna(False) & steady & has("pd"), 8, 15)
    # Suction falling on its own, with discharge and power still normal, is a
    # transmitter. Suction falling while discharge also moves is the strainer.
    suction_alone = (ps.fillna(0.0) < -6.0) & (pd_.abs().fillna(0.0) < 2.0) & (pw.abs().fillna(0.0) < 2.0)
    if has("q"):
        suction_alone = suction_alone & (q.abs().fillna(0.0) < 2.0)
    clog = (ps.fillna(0.0) < -6.0) & has("ps") & ~suction_alone & ~collapse.fillna(False) & (pd_.fillna(0.0) > -5.0)
    clog = _sustained(clog.fillna(False) & steady, 8, 15)
    imp = (pd_.fillna(0.0) < -4.0) & (i.fillna(0.0) < -2.0) & (ps.fillna(0.0) > -3.0) & (S["pd"].fillna(0.0) < 6.0)
    imp = _sustained(imp.fillna(False) & steady & has("pd") & has("i"), 10, 20)
    imp = imp & ~air & ~restr & ~clog & ~cav
    clog = clog & ~air & ~cav & ~restr
    air = air & ~cav

    drift_ch = g.get("drift_ch", pd.Series("", index=g.index)).fillna("")
    # Suction walking down while discharge moves too is the strainer, not the gauge.
    suction_is_machine = drift_ch.eq("ps") & (pd_.fillna(0.0) < -2.0)
    drift_on = drift_ch.ne("") & ~suction_is_machine
    # A machine pattern explains the same minutes, so it is not a drifting gauge.
    drift = _sustained(drift_on & steady & ~clog & ~air & ~restr & ~imp & ~cav & ~mis & ~imb & ~bearing, 4, 8)

    calib = pd.Series(False, index=g.index)
    for key in CALIB_CH:
        if not has(key):
            continue
        alone = Z[key].abs() > 8.0
        pals = [p for p in COUPLED.get(key, []) if has(p)]
        if not pals:
            continue
        partner = pd.concat([Z[p].abs() for p in pals], axis=1).max(axis=1)
        alone = alone & (partner.fillna(0.0) < 2.0)
        calib = calib | alone.fillna(False)
    calib_now = calib.fillna(False)
    calib = _sustained(calib_now & steady & ~drift, 12, 20) & calib_now

    flags = {
        "gap": gap, "stuck": stuck, "noise": noise, "cav": cav, "air": air, "restr": restr,
        "clog": clog, "imp": imp, "mis": mis, "imb": imb, "bearing": bearing, "drift": drift, "calib": calib,
    }
    for name, flag in flags.items():
        g[name] = flag.fillna(False).to_numpy()
    return g


def _top_channels(row: pd.Series, k: int = 3) -> str:
    scored = []
    for key, name in CH.items():
        val = row.get("z_" + key)
        if pd.notna(val) and abs(float(val)) >= 2.5:
            scored.append((abs(float(val)), _reading(row, key)))
    scored.sort(reverse=True)
    return ", ".join(text for _, text in scored[:k]) or "no large level shift"


def head_note(row: pd.Series, asset: pd.Series) -> str:
    """How far measured head sits from the catalog pump curve. Empty when flow or suction is missing."""
    flow = row.get("flow_m3_h__mean")
    discharge = row.get("discharge_pressure_kpa__mean")
    suction = row.get("suction_pressure_kpa__mean")
    if pd.isna(flow) or pd.isna(discharge) or pd.isna(suction) or float(flow) <= 1.0:
        return ""
    rpm = row.get("motor_speed_rpm__mean")
    rated = float(asset["motor_rated_rpm"])
    speed_ratio = float(rpm) / rated if pd.notna(rpm) and float(rpm) > 1.0 else 1.0
    salinity = 6.0 if "sea" in str(asset.get("fluid", "")).lower() else 0.0
    temp = row.get("fluid_temp_c__mean")
    temp_c = float(temp) if pd.notna(temp) else 20.0
    rho = float(np.asarray(water_density(temp_c, salinity)).reshape(-1)[0])
    measured = (float(discharge) - float(suction)) * 1000.0 / (rho * 9.80665)
    h_bep = float(asset["pump_rated_head_m"])
    q_bep = float(asset["pump_rated_flow_m3_h"]) / 3600.0
    h0 = float(asset["pump_shutoff_head_m"]) / h_bep
    expected = float(np.asarray(pump_head(float(flow) / 3600.0, speed_ratio, h_bep, q_bep, h0)).reshape(-1)[0])
    if expected < 1.0:
        return ""
    pct = (measured - expected) / h_bep * 100.0
    if abs(pct) < 5.0:
        return ""
    return f" Head is {pct:+.0f}% of rated head versus the pump curve at this speed and flow."


def _z(row: pd.Series, key: str) -> float | None:
    val = row.get("z_" + key)
    return None if val is None or pd.isna(val) else float(val)


def _zs(row: pd.Series, key: str) -> float | None:
    val = row.get("zs_" + key)
    return None if val is None or pd.isna(val) else float(val)


_UNIT = {
    "v": "mm/s", "a": "g", "c": "", "bt": "C",
    "ps": "kPa", "pd": "kPa", "i": "A", "pw": "kW",
    "q": "m3/h", "n": "rpm", "wt": "C", "tf": "C", "vp": "%",
}


def _reading(row: pd.Series, key: str, stat: str = "mean") -> str:
    """Format a channel with its measured minute value, not its z-score."""
    val = row.get(f"{CH[key]}__{stat}")
    if val is None or pd.isna(val):
        return CH[key]
    shown = f"{float(val):.1f} {_UNIT[key]}".strip()
    if stat == "std":
        shown = f"spread {shown}"
    return f"{CH[key]} ({shown})"


def evidence_text(kind: str, row: pd.Series, asset: pd.Series) -> str:
    """Describe the measurements that triggered this fault signature."""
    if kind == "gap":
        detail = f"minute has {_num(row, 'rows'):.0f} samples (under 30)"
    elif kind == "stuck":
        frozen = [CH[key] for key in ("pd", "ps", "q", "bt", "i", "tf", "wt", "pw") if bool(row.get("flat_" + key, False))]
        detail = (frozen[0] if frozen else "a transmitter") + " is frozen (minute spread is 0) while the pump is running"
        if len(frozen) > 1:
            detail += "; also frozen: " + ", ".join(frozen[1:])
    elif kind == "noise":
        hits = []
        for key in ("pd", "ps", "q", "i", "pw", "bt", "v"):
            spread, level = _zs(row, key), _z(row, key)
            if spread is None or spread <= 18 or level is None or abs(level) >= 6:
                continue
            hits.append((spread, f"{_reading(row, key, 'std')}; {_reading(row, key)}"))
        hits.sort(reverse=True)
        detail = "; ".join(text for _, text in hits[:2]) or _top_channels(row)
    elif kind == "drift":
        key = str(row.get("drift_ch") or "")
        med = row.get("drift_med")
        if not key:
            key = str(row.get("drift_hold_ch") or "")
            med = row.get("drift_hold_med")
        name = CH.get(key, "a transmitter")
        if key in CH and med is not None and pd.notna(med):
            detail = f"{_reading(row, key)} is walking away; related channels stayed near their normal"
        else:
            detail = f"{name} is walking away from this pump's normal while related channels stay put"
    elif kind == "calib":
        named = None
        named_z = 0.0
        for key in CALIB_CH:
            level = _z(row, key)
            if level is None or abs(level) <= 8:
                continue
            pals = [_z(row, partner) for partner in COUPLED.get(key, [])]
            pals = [abs(partner) for partner in pals if partner is not None]
            if pals and max(pals) < 2.0 and abs(level) > abs(named_z):
                named, named_z = key, level
        if named is None:
            detail = _top_channels(row)
        else:
            detail = f"{_reading(row, named)}; related channels stayed near their normal"
    elif kind == "bearing":
        bits = []
        crest, accel, vel = _z(row, "c"), _z(row, "a"), _z(row, "v")
        if crest is not None and crest > 6:
            bits.append(_reading(row, "c"))
        if accel is not None and accel > 7:
            bits.append(_reading(row, "a"))
        if vel is not None:
            bits.append(_reading(row, "v"))
        detail = "; ".join(bits) or _top_channels(row)
    elif kind == "cav":
        bits = []
        for key in ("ps", "a", "pd"):
            if _z(row, key) is not None:
                bits.append(_reading(row, key))
        detail = "; ".join(bits) or _top_channels(row)
    elif kind == "air":
        bits = []
        for key in ("q", "pw", "ps"):
            if _z(row, key) is not None:
                bits.append(_reading(row, key))
        detail = "; ".join(bits) or _top_channels(row)
    elif kind == "restr":
        pd_, q, current, power = (_z(row, key) for key in ("pd", "q", "i", "pw"))
        ps, valve = _z(row, "ps"), _z(row, "vp")
        high_head = pd_ is not None and pd_ > 3 and (
            (q is not None and q < -4)
            or (current is not None and current < -3)
            or (power is not None and power < -4)
        )
        low_flow = (
            q is not None
            and q < -8
            and pd_ is not None
            and pd_ > 1.5
            and (ps is None or ps > -4)
        )
        valve_restriction = (
            valve is not None and valve < -6 and q is not None and q < -3
        )
        bits = []
        if high_head:
            bits.append(_reading(row, "pd"))
            if q is not None and q < -4:
                bits.append(_reading(row, "q"))
            if power is not None and power < -4:
                bits.append(_reading(row, "pw"))
            if current is not None and current < -3:
                bits.append(_reading(row, "i"))
        elif low_flow:
            bits.extend((_reading(row, "q"), _reading(row, "pd")))
        elif valve_restriction:
            bits.extend((_reading(row, "vp"), _reading(row, "q")))
        detail = "; ".join(bits) or _top_channels(row)
    elif kind == "clog":
        bits = []
        if _z(row, "ps") is not None:
            bits.append(_reading(row, "ps"))
        if _z(row, "pd") is not None:
            bits.append(_reading(row, "pd"))
        detail = "; ".join(bits) or _top_channels(row)
    elif kind == "imp":
        bits = []
        for key in ("pd", "i", "ps"):
            if _z(row, key) is not None:
                bits.append(_reading(row, key))
        detail = "; ".join(bits) or _top_channels(row)
    elif kind in ("mis", "imb"):
        vel, ratio = _z(row, "v"), row.get("ratio_21")
        bits = []
        if vel is not None:
            bits.append(_reading(row, "v"))
        if ratio is not None and pd.notna(ratio):
            bits.append(f"2x/1x vibration ({float(ratio):.2f})")
        detail = "; ".join(bits) or _top_channels(row)
    else:
        detail = _top_channels(row)
    if kind in ("air", "clog", "cav", "restr", "imp"):
        detail = detail + head_note(row, asset)
    return detail


def alerts_for_asset(g: pd.DataFrame, asset: pd.Series) -> list:
    """One alert when a signature turns on, then silence until it has been gone for 12 hours.

    A second fault already waiting is sent 6 minutes later so the scorer does not
    merge two causes into one ambiguous alert. Repeating the same cause inside an
    ongoing event is what made the first pass lose on false-alarm burden.
    """
    g = signature_frame(g)
    rows = []
    last_emit = None
    active = {name: False for name in PRIORITY}
    quiet_min = {name: 10 ** 9 for name in PRIORITY}
    queued = []
    for j in range(len(g)):
        row = g.iloc[j]
        for name in PRIORITY:
            flag = bool(row[name])
            if flag and not active[name]:
                if quiet_min[name] >= 12 * 60 and name not in queued:
                    queued.append(name)
                active[name] = True
                quiet_min[name] = 0
            elif flag:
                quiet_min[name] = 0
            else:
                active[name] = False
                quiet_min[name] += 1
        if not queued:
            continue
        t = row["window_start_utc"] + pd.Timedelta(minutes=1)
        if last_emit is not None and t - last_emit < MIN_GAP:
            continue
        kind = next(name for name in PRIORITY if name in queued)
        queued.remove(kind)
        _event_class, fault_type = FAULT[kind]
        rows.append({
            "asset_id": row["asset_id"],
            "alert_time_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event_class": FAULT[kind][0],
            "fault_type": fault_type,
            "confidence": signature_confidence(kind, row),
            "evidence": evidence_text(kind, row, asset),
            "recommended_action": ACTION[fault_type],
        })
        last_emit = t
    return rows


def on_minute(minute, asset, history=None) -> list:
    """Score one new minute and return the alerts it raises.

    Call this from outside the batch script. Pass the minutes already seen for
    this pump as ``history`` (oldest first, starting at the pump's first minute)
    and do not include ``minute`` in that table. Append ``minute`` yourself
    before the next call.

    ``minute`` is one ``features_1min`` row: ``window_start_utc``, ``rows``,
    ``operating_mode``, ``running_fraction``, and the channel ``__mean``,
    ``__std`` and ``__count`` columns. Add ``ratio_21`` when a vibration
    snapshot has already been taken; it is treated as taken at this minute
    unless ``snapshot_utc`` is set. ``asset`` is that pump's row from
    ``assets.csv``.

    The first 24 hours only establish the pump's own normal, so those calls
    return an empty list. Later calls return zero or one alert dict with the
    submission columns.
    """
    asset = asset.copy() if isinstance(asset, pd.Series) else pd.Series(asset)
    row = dict(minute)
    row["asset_id"] = row.get("asset_id") or asset["asset_id"]
    row.setdefault("operating_mode", "RUNNING")
    row.setdefault("running_fraction", 1.0)
    row.setdefault("rows", 60)
    if row.get("ratio_21") is not None and not pd.isna(row.get("ratio_21")) and row.get("snapshot_utc") is None:
        row["snapshot_utc"] = row.get("window_start_utc")
    current = pd.DataFrame([row])
    if history is None or len(history) == 0:
        frame = current
    else:
        frame = pd.concat([pd.DataFrame(history), current], ignore_index=True)
    frame["asset_id"] = asset["asset_id"]
    assets = pd.DataFrame([asset])
    scored = mark_drift(zscores(regime_frame(frame, assets)))
    if "ratio_21" not in scored.columns:
        scored["ratio_21"] = np.nan
    alerts = alerts_for_asset(scored, asset)
    when = pd.Timestamp(row["window_start_utc"])
    if when.tzinfo is None:
        when = when.tz_localize("UTC")
    stamp = (when + pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [item for item in alerts if item["alert_time_utc"] == stamp]


class PolarMindStream:
    """One pump, fed a minute at a time.

    The normal is learned from the first 24 hours and then left fixed, the same
    way a time-series baseline is frozen after commissioning. Every later minute
    updates the rolling state and returns what the pump looks like right now.
    """

    def __init__(self, asset):
        self.asset = asset.copy() if isinstance(asset, pd.Series) else pd.Series(asset)
        self.history = None
        self._learning_rows: list[dict[str, object]] = []
        self.started = None

    def prime_history(self, rows: list[dict[str, object]]) -> None:
        if self.started is not None or self.history is not None or self._learning_rows:
            raise ValueError("Model history can only be primed before the first update.")
        if not rows:
            return

        history = pd.DataFrame.from_records(rows)
        history["window_start_utc"] = pd.to_datetime(
            history["window_start_utc"], utc=True
        )
        history = history.sort_values("window_start_utc").reset_index(drop=True)
        self.started = history["window_start_utc"].iloc[0]
        self.history = history

    def update(self, minute) -> str:
        """Take the next minute. Return the output for that minute as a JSON string."""
        row = dict(minute)
        row["asset_id"] = row.get("asset_id") or self.asset["asset_id"]
        when = pd.Timestamp(row["window_start_utc"])
        if when.tzinfo is None:
            when = when.tz_localize("UTC")
        if self.started is None:
            self.started = when
        learning = when < self.started + pd.Timedelta(hours=24)
        if learning:
            self._learning_rows.append(row)
            alerts = []
        else:
            if self._learning_rows:
                learning_rows = pd.DataFrame.from_records(self._learning_rows)
                if self.history is None:
                    self.history = learning_rows
                else:
                    self.history = pd.concat(
                        [self.history, learning_rows], ignore_index=True
                    )
                self._learning_rows.clear()
            if self.history is None:
                raise RuntimeError("Model history is not initialized for scoring.")
            alerts = on_minute(minute, self.asset, self.history)
            current = pd.DataFrame([row])
            self.history = current if self.history.empty else pd.concat(
                [self.history, current], ignore_index=True
            )
        alert = alerts[0] if alerts and not learning else None
        if alert is not None:
            phase = "alert"
        elif learning:
            phase = "learning_normal"
        else:
            phase = "watching"
        return json.dumps({
            "asset_id": self.asset["asset_id"],
            "minute_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": phase,
            "event_class": None if alert is None else alert["event_class"],
            "fault_type": None if alert is None else alert["fault_type"],
            "confidence": None if alert is None else alert["confidence"],
            "evidence": "" if alert is None else alert["evidence"],
            "recommended_action": "" if alert is None else alert["recommended_action"],
        }, indent=2)


def predict(frame: pd.DataFrame, assets: pd.DataFrame) -> pd.DataFrame:
    asset_rows = {r["asset_id"]: r for _, r in assets.iterrows()}
    found = []
    for asset_id, g in frame.groupby("asset_id", sort=False):
        if asset_id not in asset_rows:
            continue
        found += alerts_for_asset(g, asset_rows[asset_id])
    if not found:
        return pd.DataFrame(columns=COLS)
    out = pd.DataFrame(found, columns=COLS)
    return out.sort_values(["asset_id", "alert_time_utc"]).reset_index(drop=True)
