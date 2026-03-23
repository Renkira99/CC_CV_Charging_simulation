"""
EV Dynamic Charging Simulator
Based on literature review of CC-CV charging characteristics
References: IEEE TPEL 2023, IEEE TIE 2025, IEEE TTE 2024, Springer EE 2023
"""

import argparse
import os
import sys
import warnings

from runtime_bootstrap import bootstrap_runtime, open_files_in_default_app

bootstrap_runtime(
    script_file=__file__,
    argv=sys.argv,
    required_modules=('numpy', 'matplotlib'),
    is_main=__name__ == '__main__',
)

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend — saves to file
import numpy as np
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
    v_ratio = v_min_ratio + (v_ref - _OCV_NORM_PTS[0]) / default_span * (v_max_ratio - v_min_ratio)  # Rescale to actual v_min/v_max range
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
    if bat is None:
        bat = BatteryConfig()
    if chg is None:
        chg = ChargerConfig()

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
    time_s_append = time_s.append
    voltage_append = voltage.append
    current_append = current.append
    power_append = power.append
    soc_append = soc.append
    mode_append = mode_arr.append

    # Cache loop-invariant limits/refs to reduce per-step overhead.
    i_cable_limit = getattr(chg, 'cable_limit_a', chg.max_current_a)
    i_temp_limit = getattr(chg, 'temp_limit_a', float('inf'))
    i_evse_limit = getattr(chg, 'evse_max_limit_a', float('inf'))
    i_charger_limit = chg.max_current_a

    battery_ocv_fn = battery_ocv
    internal_resistance_fn = internal_resistance
    bms_current_limit_fn = bms_current_limit

    t = 0.0
    current_soc = bat.initial_soc
    last_cc_current = chg.max_current_a  # Track for CV entry continuity
    target_soc = bat.target_soc
    cc_cv_transition = chg.cc_cv_transition

    last_cc_vterm = battery_ocv_fn(bat.initial_soc, bat.nominal_voltage,
                                   bat.v_min_ratio, bat.v_max_ratio)  # Track for CV voltage ramp

    while current_soc < target_soc:
        v_ocv = battery_ocv_fn(current_soc, bat.nominal_voltage, bat.v_min_ratio, bat.v_max_ratio)
        r_int = internal_resistance_fn(current_soc, bat.nominal_voltage)

        if current_soc < cc_cv_transition:
            # === CP or CC MODE ===
            
            # 1. Power limit: I_power = P_max / V_term (quadratic formula accounts for actual terminal voltage)
            i_power = (-v_ocv + np.sqrt(v_ocv**2 + 4 * r_int * chg.max_power_w)) / (2 * r_int)
            
            # 2. BMS current acceptance limit (derates at high SoC)
            i_bms = bms_current_limit_fn(current_soc, ah_capacity)
            
            # The actual current delivered is the minimum of all constraints
            i_cc = min(i_power, i_bms, i_cable_limit, i_charger_limit, i_temp_limit, i_evse_limit)
            
            v_term = min(v_ocv + i_cc * r_int, v_max)       # cap at battery max voltage
            p = v_term * i_cc                               # actual delivered power
            last_cc_current = i_cc    # Store for CV entry continuity
            last_cc_vterm = v_term    # Store for CV voltage ramp (eliminates power-spike at transition)
            
            # CP = power is genuinely binding when i_power is measurably below all hardware
            # limits. Level 2 AC: current limit binds first — CP essentially never fires.
            # DC fast: power often limits before cable/BMS — CP is the realistic label here.
            other_limits = min(i_bms, i_cable_limit, i_charger_limit, i_temp_limit, i_evse_limit)
            if i_power < other_limits - 1.0:
                m = 'CP'
            else:
                m = 'CC'
        else:
            # === CV MODE ===
            # Ramp v_term from last CC terminal voltage → v_max over the first 10% of CV progress.
            # This eliminates the one-timestep power spike caused by jumping straight to v_max
            # while current is still at last_cc_current (before the exponential decay takes over).
            progress = (current_soc - cc_cv_transition) / max(target_soc - cc_cv_transition, 1)
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

        time_s_append(t)
        voltage_append(v_term)
        current_append(i_cc)
        power_append(p)
        soc_append(current_soc)
        mode_append(m)

        # Coulomb counting: SoC += (I * dt) / (Ah_total * 3600) * 100
        current_soc += ((i_cc * dt) / 3600) / ah_capacity * 100
        current_soc = min(current_soc, target_soc)
        t += dt

    return {
        'time_min': np.array(time_s) / 60,
        'voltage': np.array(voltage),
        'current': np.array(current),
        'power_kw': np.array(power) / 1000,
        'soc': np.array(soc),
        'mode': mode_arr,
    }


def preset_output_dir(base_output_dir, preset_name):
    """Return a filesystem-safe output directory for a charger preset."""
    safe_name = preset_name.replace(os.sep, '_')
    if os.altsep:
        safe_name = safe_name.replace(os.altsep, '_')
    return os.path.join(base_output_dir, safe_name)


def run_simulation_for_preset(preset_name, topology, base_output_dir):
    """Run the full simulation workflow for one charger preset."""
    # Local import to avoid circular dependency at module load time.
    # harmonic_characterization imports TOPOLOGY_PROFILES, BatteryConfig, and GRID_FREQ_HZ
    # from this module; those are defined above, so this import is safe here.
    from harmonic_characterization import (
        plot_charging_profile,
        plot_harmonics,
        plot_topology_comparison,
        power_factor_from_thd,
    )
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
    plot_charging_profile(data, topology, os.path.join(preset_dir, 'Figure_1_Charging_Profile.png'))

    # --- 2. Harmonic Analysis ---
    print("\n⚡ [2/3] Running harmonic analysis...")
    profile = TOPOLOGY_PROFILES[topology]
    print(f"   Topology: {topology}")
    print(f"   THD (CC mode): {profile['thd_cc']}%")
    print(f"   THD (CV mode): {profile['thd_cv']}%")
    print(f"   Power Factor (CC): {power_factor_from_thd(profile['thd_cc']):.4f}")
    print(f"   Power Factor (CV): {power_factor_from_thd(profile['thd_cv']):.4f}")
    compliant = profile['thd_cc'] <= 5.0 and all(v <= 4.0 for v in profile['harmonics'].values())
    print(f"   IEEE 519 / IS 16528 Compliance: {'✅ PASS' if compliant else '❌ FAIL'}")
    plot_harmonics(data, topology, os.path.join(preset_dir, 'Figure_2_Harmonic_Analysis.png'))

    # --- 3. Topology Comparison ---
    print("\n🔄 [3/3] Comparing converter topologies...")
    for name in TOPOLOGY_PROFILES:
        p = TOPOLOGY_PROFILES[name]
        status = '✅' if p['thd_cc'] <= 5.0 else '❌'
        print(f"   {status} {name}: THD={p['thd_cc']:.1f}%, η={p['eta']*100:.1f}%")
    plot_topology_comparison(os.path.join(preset_dir, 'Figure_3_Topology_Comparison.png'))

    print("\n  Saved figures:")
    print(f"     {preset_dir}/Figure_1_Charging_Profile.png")
    print(f"     {preset_dir}/Figure_2_Harmonic_Analysis.png")
    print(f"     {preset_dir}/Figure_3_Topology_Comparison.png")

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
        open_files_in_default_app([
            os.path.join(preset_dir, 'Figure_1_Charging_Profile.png'),
            os.path.join(preset_dir, 'Figure_2_Harmonic_Analysis.png'),
            os.path.join(preset_dir, 'Figure_3_Topology_Comparison.png'),
        ])


if __name__ == '__main__':
    main()
