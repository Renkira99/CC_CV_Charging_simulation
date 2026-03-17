"""
EV Dynamic Charging Simulator
Based on literature review of CC-CV charging characteristics
References: IEEE TPEL 2023, IEEE TIE 2025, IEEE TTE 2024, Springer EE 2023
"""

import numpy as np
import os
import argparse
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend — saves to file
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import MultipleLocator
import warnings
warnings.filterwarnings('ignore')

# Indian Grid Standard
GRID_FREQ_HZ = 50   # India operates at 50 Hz (CEA Regulations)

# ============================================================
#  CONFIGURATION
# ============================================================

class BatteryConfig:
    """Battery parameters — adjust these to simulate different EVs"""
    capacity_kwh = 60        # Battery capacity (kWh)
    nominal_voltage = 400    # Nominal voltage (V)
    v_min_ratio = 0.82       # Min voltage ratio (empty)
    v_max_ratio = 1.05       # Max voltage ratio (full charge) — 96 cells × 4.2V ≈ 403V → ~1.05 × 400V
    initial_soc = 20         # Initial State of Charge (%)
    target_soc = 100         # Target State of Charge (%)

class ChargerConfig:
    """Charger parameters"""
    max_power_w = 7200       # Max charging power (W) — Level 2 default
    max_current_a = 30       # Max charging current (A)
    cable_limit_a = 30       # Cable physical current rating (A)
    temp_limit_a = float('inf')      # Dynamic connector temperature limit
    evse_max_limit_a = float('inf')  # ISO 15118 EVSE constraint
    cc_cv_transition = 80    # SoC (%) at which CC→CV transition occurs
    efficiency = 0.96        # Charger efficiency

# Charger level presets — Indian Standards (AIS-138 / BIS IS 17017 / MoP EV Charging Guidelines 2022)
CHARGER_PRESETS = {
    'Bharat AC-001 (3.3kW)':      {'power': 3300,   'current': 15,   'cable': 15},   # 230V/15A, IS 60309 socket
    'AC Level 2 1-phase (7.4kW)': {'power': 7400,   'current': 32,   'cable': 32},   # 230V/32A, single-phase
    'AC Level 2 3-phase (22kW)':  {'power': 22000,  'current': 32,   'cable': 32},   # 415V/32A, three-phase
    'Bharat DC-001 (15kW)':       {'power': 15000,  'current': 200,  'cable': 200},  # CCS2/CHAdeMO, low-speed public
    'DC Fast (60kW)':             {'power': 60000,  'current': 250,  'cable': 200},  # 60kW cable limit typically 150-200A
    'DC Ultra-Fast (150kW)':      {'power': 150000, 'current': 375,  'cable': 350},  # 150kW cable limit typically 350A
}

# Converter topology profiles (Complete AC-DC Systems from literature)
# Efficiency = η_PFC * η_DCDC
# THD = AC input current THD
# cv_thd_stable: True for topologies with active PFC — control loop holds THD at light load.
# False for passive front-end (Diode Bridge) where THD rises as current tapers in CV tail.
TOPOLOGY_PROFILES = {
    'Vienna + LLC Resonant (η=95.1%)': {
        'eta': 0.951, 'thd_cc': 4.2, 'thd_cv': 2.1,  # Vienna (97%) * LLC (98%)
        'harmonics': {3: 0.0, 5: 3.2, 7: 2.1, 9: 0.0, 11: 1.4, 13: 0.9, 15: 0.0, 17: 0.6, 19: 0.5},
        'cv_thd_stable': True,
    },
    'Vienna + LCL Resonant (η=93.8%)': {
        'eta': 0.938, 'thd_cc': 5.1, 'thd_cv': 2.8,  # Vienna (97%) * LCL (96.7%)
        'harmonics': {3: 0.0, 5: 4.0, 7: 2.8, 9: 0.0, 11: 1.8, 13: 1.1, 15: 0.0, 17: 0.7, 19: 0.6},
        'cv_thd_stable': True,
    },
    'Active Front End + Dual Active Bridge (η=94%)': {
        'eta': 0.94, 'thd_cc': 3.8, 'thd_cv': 1.9,   # AFE (96%) * DAB (98%)
        'harmonics': {3: 0.0, 5: 2.8, 7: 1.9, 9: 0.0, 11: 1.2, 13: 0.7, 15: 0.0, 17: 0.4, 19: 0.3},
        'cv_thd_stable': True,
    },
    'Diode Bridge + Boost PFC + Full-Bridge (η=91.2%)': {
        'eta': 0.912, 'thd_cc': 7.2, 'thd_cv': 4.0,  # Boost PFC (96%) * Full-Bridge (95%)
        'harmonics': {3: 5.5, 5: 3.8, 7: 2.5, 9: 1.5, 11: 1.0, 13: 0.8, 15: 0.5, 17: 0.4, 19: 0.3},
        'cv_thd_stable': False,  # Passive input stage: THD rises as current tapers
    },
}


# ============================================================
#  BATTERY MODEL
# ============================================================

# NMC/NCM pack OCV lookup — 13-point curve anchored to [0.82, 1.05]×V_nom.
# Flat plateau at 20–70% SoC captures the Li-ion characteristic that a polynomial misses.
_OCV_SOC_PTS  = np.array([0,     5,     10,    20,    30,    40,    50,    60,    70,    80,    90,    95,    100])
_OCV_NORM_PTS = np.array([0.820, 0.838, 0.855, 0.878, 0.895, 0.908, 0.920, 0.933, 0.950, 0.970, 1.000, 1.028, 1.050])

def battery_ocv(soc, nominal_v, v_min_ratio=0.82, v_max_ratio=1.05):
    """OCV via piecewise interpolation of NMC cell curve (scaled to configured pack limits).
    Rescales [0.82, 1.05] reference to actual v_min/v_max if they differ from defaults."""
    v_ref = np.interp(soc, _OCV_SOC_PTS, _OCV_NORM_PTS)   # Get normalized OCV from reference curve
    default_span = _OCV_NORM_PTS[-1] - _OCV_NORM_PTS[0]  # 0.230  — default voltage span of reference curve
    v_ratio = v_min_ratio + (v_ref - _OCV_NORM_PTS[0]) / default_span * (v_max_ratio - v_min_ratio)
    return np.minimum(nominal_v * v_ratio, nominal_v * v_max_ratio)

def internal_resistance(soc, nominal_v):
    """Pack resistance — U-curve, higher at both extremes, minimum near 50% SoC.
    Formula: r_base × (1 + 2(0.5−s)²) → 1.5× at SoC=0%/100%, 1.0× at SoC=50%.
    The old polynomial (1.5 − 0.8s + 0.6s²) was monotone and missed the high-SoC rise."""
    s = soc / 100.0
    r_base = nominal_v * 0.0001  # ~0.04 Ω at 400V
    return r_base * (1.0 + 2.0 * (0.5 - s) ** 2)


def bms_current_limit(soc, ah_capacity, max_c_rate=2.0):
    """BMS derates allowed charging current at high SoC to protect cells.
    Typical Li-ion derating: full rate <70%, 80% at 70-85%, 50% at 85-95%, 20% >95%.
    Default 2C max acceptance rate covers most Li-ion chemistries."""
    i_max = ah_capacity * max_c_rate
    if soc < 70:
        return i_max
    elif soc < 85:
        return i_max * 0.8
    elif soc < 95:
        return i_max * 0.5
    else:
        return i_max * 0.2


# ============================================================
#  CC-CV CHARGING SIMULATION
# ============================================================

def simulate_charging(bat=None, chg=None, dt=1.0):
    """
    Run full CC-CV charging simulation.
    dt: time step in seconds
    Returns dict of time-series arrays.
    """
    if bat is None: bat = BatteryConfig()
    if chg is None: chg = ChargerConfig()

    v_max = bat.nominal_voltage * bat.v_max_ratio
    capacity_wh = bat.capacity_kwh * 1000
    ah_capacity = capacity_wh / bat.nominal_voltage
    # Cutoff: C/20 is the standard termination criterion, but for low-power chargers
    # (e.g. 3.3kW/15A on a 60kWh/150Ah pack) C/20 = 7.5A is 50% of peak current,
    # causing the CV phase to terminate almost immediately after transition.
    # Cap at 5% of charger max current so the CV tail can actually run.
    cutoff_current = min(ah_capacity / 20, chg.max_current_a * 0.05)

    # Storage
    time_s, voltage, current, power, soc, mode_arr = [], [], [], [], [], []
    t = 0.0
    current_soc = bat.initial_soc
    last_cc_current = chg.max_current_a  # Track for CV entry continuity
    last_cc_vterm = battery_ocv(bat.initial_soc, bat.nominal_voltage,
                                bat.v_min_ratio, bat.v_max_ratio)  # Track for CV voltage ramp

    while current_soc < bat.target_soc:
        v_ocv = battery_ocv(current_soc, bat.nominal_voltage, bat.v_min_ratio, bat.v_max_ratio)
        r_int = internal_resistance(current_soc, bat.nominal_voltage)

        if current_soc < chg.cc_cv_transition:
            # === CP or CC MODE ===
            
            # 1. Power limit: I_power = P_max / V_term (quadratic formula accounts for actual terminal voltage)
            i_power = (-v_ocv + np.sqrt(v_ocv**2 + 4 * r_int * chg.max_power_w)) / (2 * r_int)
            
            # 2. BMS current acceptance limit (derates at high SoC)
            i_bms = bms_current_limit(current_soc, ah_capacity)
            
            # 3. Cable current rating limitation
            i_cable = getattr(chg, 'cable_limit_a', chg.max_current_a)
            
            # 4. Charger hardware capability
            i_charger = chg.max_current_a
            
            # Additional EVSE and Thermal limit constraints (DIN 70121 / ISO 15118)
            i_temp = getattr(chg, 'temp_limit_a', float('inf'))
            i_evse = getattr(chg, 'evse_max_limit_a', float('inf'))
            
            # The actual current delivered is the minimum of all constraints
            i_cc = min(i_power, i_bms, i_cable, i_charger, i_temp, i_evse)
            
            v_term = min(v_ocv + i_cc * r_int, v_max)       # cap at battery max voltage
            p = v_term * i_cc                               # actual delivered power
            last_cc_current = i_cc    # Store for CV entry continuity
            last_cc_vterm = v_term    # Store for CV voltage ramp (eliminates power-spike at transition)
            
            # CP = power is genuinely binding when i_power is measurably below all hardware
            # limits. Level 2 AC: current limit binds first — CP essentially never fires.
            # DC fast: power often limits before cable/BMS — CP is the realistic label here.
            other_limits = min(i_bms, i_cable, i_charger, i_temp, i_evse)
            if i_power < other_limits - 1.0:
                m = 'CP'
            else:
                m = 'CC'
        else:
            # === CV MODE ===
            # Ramp v_term from last CC terminal voltage → v_max over the first 10% of CV progress.
            # This eliminates the one-timestep power spike caused by jumping straight to v_max
            # while current is still at last_cc_current (before the exponential decay takes over).
            progress = (current_soc - chg.cc_cv_transition) / max(bat.target_soc - chg.cc_cv_transition, 1)
            v_term = min(last_cc_vterm + (v_max - last_cc_vterm) * min(progress * 10, 1.0), v_max)
            # CV exponent: 3.5 is appropriate for fast chargers (high C-rate → rapid
            # current acceptance decay). For slow chargers the same exponent cuts off too
            # early — scale it down so the tail is shallower at low C-rates.
            # Range: ~1.5 (3.3kW ≈ 0.06C) to ~3.5 (DC fast ≈ 1C+), clamped to [1.5, 3.5].
            c_rate = chg.max_current_a / ah_capacity
            cv_exponent = np.clip(1.5 + 2.0 * c_rate, 1.5, 3.5)
            i_cv = last_cc_current * np.exp(-cv_exponent * progress)
            # Max current battery can physically absorb at V_max given current OCV and R_int
            i_physical = max((v_max - v_ocv) / max(r_int, 0.001), 0)
            i_cv = min(i_cv, i_physical, chg.max_current_a)
            if i_cv < cutoff_current:
                break
            p = v_term * i_cv
            i_cc = i_cv  # reuse variable
            m = 'CV'

        time_s.append(t)
        voltage.append(v_term)
        current.append(i_cc)
        power.append(p)
        soc.append(current_soc)
        mode_arr.append(m)

        # Coulomb counting: SoC += (I * dt) / (Ah_total * 3600) * 100
        current_soc += ((i_cc * dt) / 3600) / ah_capacity * 100
        current_soc = min(current_soc, bat.target_soc)
        t += dt

    return {
        'time_min': np.array(time_s) / 60,
        'voltage': np.array(voltage),
        'current': np.array(current),
        'power_kw': np.array(power) / 1000,
        'soc': np.array(soc),
        'mode': mode_arr,
    }


# ============================================================
#  HARMONIC ANALYSIS
# ============================================================

def compute_thd_profile(soc_arr, mode_arr, topology_name, cc_cv_transition=80):
    """Compute THD at each SoC point based on converter topology.
    Active PFC topologies (Vienna, AFE) maintain stable THD at light load (cv_thd_stable=True);
    passive front-end (Diode Bridge) sees rising THD as fundamental current tapers."""
    profile = TOPOLOGY_PROFILES[topology_name]
    cv_thd_stable = profile.get('cv_thd_stable', False)
    thd_arr = []
    for s, m in zip(soc_arr, mode_arr):
        if m in ('CC', 'CP'):
            thd_arr.append(profile['thd_cc'])
        else:
            if cv_thd_stable:
                # Active PFC control loop holds THD at its CV-mode value regardless of load
                thd_arr.append(profile['thd_cv'])
            else:
                # Passive front end: THD rises as fundamental current tapers in CV tail
                progress = min((s - cc_cv_transition) / (100 - cc_cv_transition), 1.0)
                thd_arr.append(profile['thd_cv'] * (1 + 1.5 * progress))
    return np.array(thd_arr)

def harmonic_spectrum(topology_name, mode='CC'):
    """Get harmonic magnitudes for a given topology and mode"""
    profile = TOPOLOGY_PROFILES[topology_name]
    scale = 1.0 if mode in ('CC', 'CP') else 0.6
    orders = sorted(profile['harmonics'].keys())
    magnitudes = [profile['harmonics'][h] * scale for h in orders]
    return orders, magnitudes

def power_factor_from_thd(thd):
    """PF ≈ DPF / sqrt(1 + (THD/100)^2)"""
    dpf = 0.99
    return dpf / np.sqrt(1 + (thd/100)**2)

def generate_waveform(topology_name, mode='CC', cycles=3, points=1000):
    """Generate distorted grid current waveform at Indian grid frequency (50 Hz)"""
    profile = TOPOLOGY_PROFILES[topology_name]
    scale = 1.0 if mode in ('CC', 'CP') else 0.6
    # Time axis: one period = 1/GRID_FREQ_HZ seconds
    t = np.linspace(0, cycles * 2 * np.pi, points)  # angular frequency (ωt)
    y_pure = np.sin(t)
    y_distorted = np.sin(t)
    for order, mag in profile['harmonics'].items():
        y_distorted += (mag * scale / 100) * np.sin(order * t + order * 0.3)
    return t, y_pure, y_distorted



# ============================================================
#  PLOTTING — DARK THEME
# ============================================================

DARK_BG = '#0a0a14'
CARD_BG = '#13132a'
GRID_COLOR = '#252548'
TEXT_COLOR = '#e0e0f0'
TEXT2_COLOR = '#8888aa'
PURPLE = '#7c6cf0'
TEAL = '#00d4c8'
PINK = '#ff6b9d'
YELLOW = '#ffc857'
GREEN = '#4ae0a0'
RED = '#ff5252'
BLUE = '#6b9dff'

def setup_dark_style():
    plt.rcParams.update({
        'figure.facecolor': DARK_BG,
        'axes.facecolor': CARD_BG,
        'axes.edgecolor': GRID_COLOR,
        'axes.labelcolor': TEXT2_COLOR,
        'axes.grid': True,
        'grid.color': GRID_COLOR,
        'grid.alpha': 0.5,
        'text.color': TEXT_COLOR,
        'xtick.color': TEXT2_COLOR,
        'ytick.color': TEXT2_COLOR,
        'font.family': 'sans-serif',
        'font.size': 10,
        'legend.facecolor': CARD_BG,
        'legend.edgecolor': GRID_COLOR,
        'legend.fontsize': 9,
    })


def plot_charging_profile(data, topology_name, save_path=None):
    """Plot 1: CC-CV Charging Profile — 4 subplots"""
    setup_dark_style()
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle('CC-CV Dynamic Charging Profile', fontsize=18, fontweight='bold',
                 color=TEXT_COLOR, y=0.98)

    # Find CC/CP → CV transition index
    trans_idx = None
    for i, m in enumerate(data['mode']):
        if i > 0 and data['mode'][i-1] in ('CC', 'CP') and m == 'CV':
            trans_idx = i
            break

    configs = [
        (axes[0,0], data['voltage'], 'Battery Voltage (V)', PINK, 'V'),
        (axes[0,1], data['current'], 'Charging Current (A)', BLUE, 'A'),
        (axes[1,0], data['power_kw'], 'Charging Power (kW)', GREEN, 'kW'),
        (axes[1,1], data['soc'], 'State of Charge (%)', YELLOW, '%'),
    ]

    for ax, y, title, color, unit in configs:
        ax.fill_between(data['time_min'], y, alpha=0.15, color=color)
        ax.plot(data['time_min'], y, color=color, linewidth=2.5, zorder=5)
        ax.set_title(title, fontsize=12, fontweight='bold', color=TEXT_COLOR, pad=10)
        ax.set_xlabel('Time (min)', fontsize=9)
        ax.set_ylabel(f'{title.split("(")[0].strip()}', fontsize=9)

        # Transition line
        if trans_idx is not None:
            t_trans = data['time_min'][trans_idx]
            ax.axvline(t_trans, color=YELLOW, linestyle='--', alpha=0.6, linewidth=1)
            ypos = ax.get_ylim()[1] * 0.95
            ax.text(t_trans + 0.5, ypos, f"{data['mode'][trans_idx-1]}→CV", fontsize=8, color=YELLOW, alpha=0.8,
                    va='top', fontweight='bold')

        # Endpoint annotation
        ax.annotate(f'{y[-1]:.1f} {unit}',
                    xy=(data['time_min'][-1], y[-1]),
                    fontsize=9, fontweight='bold', color=color,
                    bbox=dict(boxstyle='round,pad=0.3', facecolor=CARD_BG, edgecolor=color, alpha=0.9))

    # Add topology info text
    fig.text(0.5, 0.01, f'Topology: {topology_name}  |  Battery: {BatteryConfig.capacity_kwh}kWh @ {BatteryConfig.nominal_voltage}V  |  '
             f'SoC: {BatteryConfig.initial_soc}% → {data["soc"][-1]:.0f}%  |  Total time: {data["time_min"][-1]:.0f} min',
             ha='center', fontsize=9, color=TEXT2_COLOR,
             bbox=dict(boxstyle='round,pad=0.5', facecolor=CARD_BG, edgecolor=GRID_COLOR))

    plt.tight_layout(rect=[0, 0.04, 1, 0.95])
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=DARK_BG)
        print(f"✅ Saved: {save_path}")
    plt.show()


def plot_harmonics(data, topology_name, save_path=None):
    """Plot 2: Harmonic Analysis — waveform, spectrum, THD vs SoC, compliance"""
    setup_dark_style()
    fig = plt.figure(figsize=(16, 12))
    gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.4, wspace=0.3)
    fig.suptitle(f'Harmonic Analysis & IEC 61000-3-2 / IS 17017 Compliance ({GRID_FREQ_HZ} Hz Grid)', fontsize=18,
                 fontweight='bold', color=TEXT_COLOR, y=0.98)

    # 1. Grid current waveform — CC/CP mode
    ax1 = fig.add_subplot(gs[0, :])
    t_w, y_pure, y_dist = generate_waveform(topology_name, 'CC')
    ax1.plot(t_w / (2*np.pi), y_pure, color=GRID_COLOR, linewidth=1, alpha=0.5, label='Pure sine')
    ax1.fill_between(t_w / (2*np.pi), y_dist, alpha=0.1, color=PURPLE)
    ax1.plot(t_w / (2*np.pi), y_dist, color=PURPLE, linewidth=2, label='Distorted (CC/CP mode)')
    # Also plot CV mode
    _, _, y_dist_cv = generate_waveform(topology_name, 'CV')
    ax1.plot(t_w / (2*np.pi), y_dist_cv, color=TEAL, linewidth=1.5, alpha=0.7, linestyle='--', label='Distorted (CV mode)')
    ax1.set_title('Grid Current Waveform at PCC', fontsize=12, fontweight='bold', pad=10)
    ax1.set_xlabel('Cycles (50 Hz)')
    ax1.set_ylabel('Current (p.u.)')
    ax1.legend(loc='upper right')
    ax1.axhline(0, color=GRID_COLOR, linewidth=0.5)

    # 2. Harmonic spectrum — CC/CP mode
    ax2 = fig.add_subplot(gs[1, 0])
    orders_cc, mags_cc = harmonic_spectrum(topology_name, 'CC')
    orders_cv, mags_cv = harmonic_spectrum(topology_name, 'CV')
    x = np.arange(len(orders_cc))
    w = 0.35
    bars1 = ax2.bar(x - w/2, mags_cc, w, color=PURPLE, alpha=0.85, label='CC/CP Mode', zorder=5)
    bars2 = ax2.bar(x + w/2, mags_cv, w, color=TEAL, alpha=0.85, label='CV Mode', zorder=5)
    ax2.axhline(4.0, color=RED, linestyle='--', linewidth=1.5, alpha=0.7, label='IEC 61000-3-2 Limit (4%)')
    ax2.set_xticks(x)
    ax2.set_xticklabels([f'{o}th' for o in orders_cc])
    ax2.set_title('Individual Harmonic Magnitudes', fontsize=12, fontweight='bold', pad=10)
    ax2.set_ylabel('Magnitude (% of fundamental)')
    ax2.set_xlabel('Harmonic Order')
    ax2.legend(loc='upper right', fontsize=8)
    # Value labels on bars
    for bar in bars1:
        h = bar.get_height()
        if h > 0.5:
            ax2.text(bar.get_x() + bar.get_width()/2, h + 0.15, f'{h:.1f}',
                     ha='center', fontsize=7, color=TEXT2_COLOR)

    # 3. THD vs SoC
    ax3 = fig.add_subplot(gs[1, 1])
    thd_profile = compute_thd_profile(data['soc'], data['mode'], topology_name)
    ax3.fill_between(data['soc'], thd_profile, alpha=0.15, color=PINK)
    ax3.plot(data['soc'], thd_profile, color=PINK, linewidth=2.5)
    ax3.axhline(5.0, color=RED, linestyle='--', linewidth=1.5, alpha=0.7, label='IEC 61000-3-2 THD Limit (5%)')
    if trans_idx := next((i for i, m in enumerate(data['mode']) if m == 'CV'), None):
        ax3.axvline(data['soc'][trans_idx], color=YELLOW, linestyle='--', alpha=0.5)
        ax3.text(data['soc'][trans_idx] + 1, max(thd_profile) * 0.9, f"{data['mode'][trans_idx-1]}→CV", fontsize=8, color=YELLOW)
    ax3.set_title('THD vs State of Charge', fontsize=12, fontweight='bold', pad=10)
    ax3.set_xlabel('SoC (%)')
    ax3.set_ylabel('THD (%)')
    ax3.legend(loc='upper right', fontsize=8)

    # 4. Harmonic compliance — % thresholds follow IEEE 519 / IS 16528 (feeder/PCC scope).
    #    Note: IEC 61000-3-2 defines absolute amp limits per harmonic for equipment Class A/C
    #    (different scope from feeder-level analysis). Both are cited here but % limits are IEEE 519.
    ax4 = fig.add_subplot(gs[2, 0])
    ax4.axis('off')
    profile = TOPOLOGY_PROFILES[topology_name]
    thd_cc = profile['thd_cc']
    thd_cv = profile['thd_cv']
    pf_cc = power_factor_from_thd(thd_cc)
    pf_cv = power_factor_from_thd(thd_cv)
    h3 = profile['harmonics'][3]
    h5 = profile['harmonics'][5]
    h7 = profile['harmonics'][7]

    compliance_data = [
        ('THD (CC/CP Mode)', f'{thd_cc:.1f}%', '≤ 5.0%', thd_cc <= 5.0),
        ('THD (CV Mode)', f'{thd_cv:.1f}%', '≤ 5.0%', thd_cv <= 5.0),
        ('3rd Harmonic', f'{h3:.1f}%', '≤ 4.0%', h3 <= 4.0),
        ('5th Harmonic', f'{h5:.1f}%', '≤ 4.0%', h5 <= 4.0),
        ('7th Harmonic', f'{h7:.1f}%', '≤ 4.0%', h7 <= 4.0),
        ('Power Factor (CC/CP)', f'{pf_cc:.4f}', '≥ 0.95', pf_cc >= 0.95),
        ('Power Factor (CV)', f'{pf_cv:.4f}', '≥ 0.95', pf_cv >= 0.95),
    ]
    all_pass = all(c[3] for c in compliance_data)

    ax4.set_xlim(0, 10)
    ax4.set_ylim(0, len(compliance_data) + 2)
    # Title box
    status_color = GREEN if all_pass else RED
    status_text = '✅ IEEE 519 / IS 16528 COMPLIANT' if all_pass else '❌ NON-COMPLIANT'
    ax4.text(5, len(compliance_data) + 1.2, status_text, ha='center', fontsize=14,
             fontweight='bold', color=status_color,
             bbox=dict(boxstyle='round,pad=0.5', facecolor=CARD_BG, edgecolor=status_color))
    # Header
    for j, header in enumerate(['Parameter', 'Value', 'Limit', 'Status']):
        ax4.text([0.5, 3.5, 5.8, 8.5][j], len(compliance_data) + 0.3, header,
                 fontsize=9, fontweight='bold', color=TEXT2_COLOR)
    # Rows
    for i, (param, val, limit, ok) in enumerate(compliance_data):
        y = len(compliance_data) - i - 0.5
        color = GREEN if ok else RED
        ax4.text(0.5, y, param, fontsize=9, color=TEXT_COLOR)
        ax4.text(3.5, y, val, fontsize=9, fontweight='bold', color=color, family='monospace')
        ax4.text(5.8, y, limit, fontsize=9, color=TEXT2_COLOR)
        ax4.text(8.5, y, '✅ Pass' if ok else '❌ Fail', fontsize=9, color=color, fontweight='bold')

    # 5. Power factor over charging cycle
    ax5 = fig.add_subplot(gs[2, 1])
    pf_arr = power_factor_from_thd(thd_profile)
    ax5.fill_between(data['soc'], pf_arr, alpha=0.15, color=GREEN)
    ax5.plot(data['soc'], pf_arr, color=GREEN, linewidth=2.5)
    ax5.axhline(0.95, color=RED, linestyle='--', alpha=0.7, label='Min PF = 0.95')
    ax5.set_title('Power Factor vs SoC', fontsize=12, fontweight='bold', pad=10)
    ax5.set_xlabel('SoC (%)')
    ax5.set_ylabel('Power Factor')
    ax5.set_ylim(0.92, 1.0)
    ax5.legend(loc='lower right', fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=DARK_BG)
        print(f"✅ Saved: {save_path}")
    plt.show()



def plot_topology_comparison(save_path=None):
    """Plot 4: Compare all converter topologies"""
    setup_dark_style()
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle('Converter Topology Comparison (from Literature)', fontsize=16,
                 fontweight='bold', color=TEXT_COLOR, y=1.02)

    names = list(TOPOLOGY_PROFILES.keys())
    short_names = ['Vienna+LLC', 'Vienna+LCL', 'AFE+DAB', 'Boost+FB']
    colors = [PURPLE, TEAL, GREEN, PINK]

    # 1. THD comparison
    ax1 = axes[0]
    thd_cc = [TOPOLOGY_PROFILES[n]['thd_cc'] for n in names]
    thd_cv = [TOPOLOGY_PROFILES[n]['thd_cv'] for n in names]
    x = np.arange(len(names))
    w = 0.35
    ax1.bar(x - w/2, thd_cc, w, color=colors, alpha=0.85, label='CC/CP Mode')
    ax1.bar(x + w/2, thd_cv, w, color=colors, alpha=0.45, label='CV Mode')
    ax1.axhline(5.0, color=RED, linestyle='--', alpha=0.7, linewidth=1.5, label='IEC 61000-3-2 Limit (5%)')
    ax1.set_xticks(x)
    ax1.set_xticklabels(short_names, fontsize=9)
    ax1.set_title('THD by Topology', fontweight='bold', pad=10)
    ax1.set_ylabel('THD (%)')
    ax1.legend(fontsize=7)
    for i, v in enumerate(thd_cc):
        ax1.text(i - w/2, v + 0.15, f'{v:.1f}%', ha='center', fontsize=7, color=TEXT2_COLOR)

    # 2. Efficiency comparison
    ax2 = axes[1]
    etas = [TOPOLOGY_PROFILES[n]['eta'] * 100 for n in names]
    bars = ax2.bar(short_names, etas, color=colors, alpha=0.85)
    ax2.set_ylim(93, 100)
    ax2.set_title('Charger Efficiency', fontweight='bold', pad=10)
    ax2.set_ylabel('Efficiency (%)')
    for bar, v in zip(bars, etas):
        ax2.text(bar.get_x() + bar.get_width()/2, v + 0.1, f'{v:.1f}%',
                 ha='center', fontsize=9, fontweight='bold', color=TEXT_COLOR)

    # 3. Power factor comparison
    ax3 = axes[2]
    pf_cc = [power_factor_from_thd(TOPOLOGY_PROFILES[n]['thd_cc']) for n in names]
    pf_cv = [power_factor_from_thd(TOPOLOGY_PROFILES[n]['thd_cv']) for n in names]
    ax3.bar(x - w/2, pf_cc, w, color=colors, alpha=0.85, label='CC/CP Mode')
    ax3.bar(x + w/2, pf_cv, w, color=colors, alpha=0.45, label='CV Mode')
    ax3.axhline(0.95, color=RED, linestyle='--', alpha=0.7, label='Min PF = 0.95')
    ax3.set_xticks(x)
    ax3.set_xticklabels(short_names, fontsize=9)
    ax3.set_ylim(0.93, 1.0)
    ax3.set_title('Power Factor', fontweight='bold', pad=10)
    ax3.set_ylabel('Power Factor')
    ax3.legend(fontsize=7)

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=DARK_BG)
        print(f"✅ Saved: {save_path}")
    plt.show()


def preset_output_dir(base_output_dir, preset_name):
    """Return a filesystem-safe output directory for a charger preset."""
    safe_name = preset_name.replace(os.sep, '_')
    if os.altsep:
        safe_name = safe_name.replace(os.altsep, '_')
    return os.path.join(base_output_dir, safe_name)


def run_simulation_for_preset(preset_name, topology, base_output_dir):
    """Run the full simulation workflow for one charger preset."""
    preset_dir = preset_output_dir(base_output_dir, preset_name)
    os.makedirs(preset_dir, exist_ok=True)

    chg = ChargerConfig()
    preset = CHARGER_PRESETS[preset_name]
    chg.max_power_w = preset['power']
    chg.max_current_a = preset['current']
    chg.cable_limit_a = preset.get('cable', preset['current'])

    print("\n" + "-" * 60)
    print(f"  Charger preset: {preset_name}")
    print(f"  Output folder: {preset_dir}")
    print("-" * 60)

    # --- 1. CC-CV Charging Profile ---
    print(f"\n🔋 [1/3] Running CC-CV charging simulation with {preset_name}...")
    data = simulate_charging(chg=chg)
    cc_time = data['time_min'][data['mode'].index('CV')] if 'CV' in data['mode'] else data['time_min'][-1]
    print(f"   Battery: {BatteryConfig.capacity_kwh} kWh @ {BatteryConfig.nominal_voltage}V")
    print(f"   SoC: {BatteryConfig.initial_soc}% → {data['soc'][-1]:.1f}%")
    print(f"   Total charging time: {data['time_min'][-1]:.0f} min ({data['time_min'][-1]/60:.1f} hrs)")
    print(f"   CC→CV transition at: {cc_time:.0f} min (SoC = {chg.cc_cv_transition}%)")
    print(f"   Peak power: {np.max(data['power_kw']):.1f} kW")
    print(f"   Peak current: {np.max(data['current']):.1f} A")
    plot_charging_profile(data, topology, os.path.join(preset_dir, 'fig1_charging_profile.png'))

    # --- 2. Harmonic Analysis ---
    print("\n⚡ [2/3] Running harmonic analysis...")
    profile = TOPOLOGY_PROFILES[topology]
    print(f"   Topology: {topology}")
    print(f"   THD (CC mode): {profile['thd_cc']}%")
    print(f"   THD (CV mode): {profile['thd_cv']}%")
    print(f"   Power Factor (CC): {power_factor_from_thd(profile['thd_cc']):.4f}")
    print(f"   Power Factor (CV): {power_factor_from_thd(profile['thd_cv']):.4f}")
    compliant = profile['thd_cc'] <= 5.0 and all(v <= 4.0 for v in profile['harmonics'].values())
    print(f"   IEC 61000-3-2 / IS 17017 Compliance: {'✅ PASS' if compliant else '❌ FAIL'}")
    plot_harmonics(data, topology, os.path.join(preset_dir, 'fig2_harmonics.png'))

    # --- 3. Topology Comparison ---
    print("\n🔄 [3/3] Comparing converter topologies...")
    for name in TOPOLOGY_PROFILES:
        p = TOPOLOGY_PROFILES[name]
        status = '✅' if p['thd_cc'] <= 5.0 else '❌'
        print(f"   {status} {name}: THD={p['thd_cc']:.1f}%, η={p['eta']*100:.1f}%")
    plot_topology_comparison(os.path.join(preset_dir, 'fig3_topology_comparison.png'))

    print("\n  Saved figures:")
    print(f"     {preset_dir}/fig1_charging_profile.png")
    print(f"     {preset_dir}/fig2_harmonics.png")
    print(f"     {preset_dir}/fig3_topology_comparison.png")

    return preset_dir


# ============================================================
#  MAIN — RUN ALL SIMULATIONS
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='EV Dynamic Charging Simulator')
    charger_choices = ['all'] + list(CHARGER_PRESETS.keys())
    parser.add_argument('--charger', type=str, default='all',
                        choices=charger_choices,
                        help='Select one charger preset or use "all" to run every preset')
    parser.add_argument('--topology', type=str, default='Vienna + LLC Resonant (η=95.1%)',
                        choices=list(TOPOLOGY_PROFILES.keys()),
                        help='Select the converter topology')
    parser.add_argument('--output-dir', type=str,
                        default=os.path.join(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'Output',
                            'EV_Dynamic_Charging_Simulation_Results'
                        ),
                        help='Directory for output figures (default: Output/EV_Dynamic_Charging_Simulation_Results)')
    args = parser.parse_args()

    print("=" * 60)
    print("  ⚡ EV DYNAMIC CHARGING SIMULATOR")
    print("  Based on Literature Review — CC-CV Charging, Harmonics,")
    print("  Converter Topologies, and Multi-EV Grid Impact")
    print("=" * 60)

    topology = args.topology
    base_output_dir = args.output_dir

    os.makedirs(base_output_dir, exist_ok=True)

    preset_names = list(CHARGER_PRESETS.keys()) if args.charger == 'all' else [args.charger]
    saved_dirs = [run_simulation_for_preset(preset_name, topology, base_output_dir) for preset_name in preset_names]

    print("\n" + "=" * 60)
    print(f"  ✅ Completed {len(saved_dirs)} charger preset simulation(s).")
    print("  Figures saved under:")
    for saved_dir in saved_dirs:
        print(f"     {saved_dir}")
    print("=" * 60)

    # Pop up the output PNGs for single-preset runs only.
    if len(saved_dirs) == 1:
        preset_dir = saved_dirs[0]
        os.system(
            f'open "{preset_dir}/fig1_charging_profile.png" '
            f'"{preset_dir}/fig2_harmonics.png" '
            f'"{preset_dir}/fig3_topology_comparison.png"'
        )


if __name__ == '__main__':
    main()
