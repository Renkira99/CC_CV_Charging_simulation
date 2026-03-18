# EV Dynamic Charging Simulator — Code Documentation

**References:** IEEE TPEL 2023, IEEE TIE 2025, IEEE TTE 2024, Springer EE 2023  
**Grid Standard:** Indian Grid, 50 Hz (CEA Regulations)

---

## Overview

This simulator models how an electric vehicle battery charges from a given initial State of Charge (SoC) to a target SoC, using the CC-CV (Constant Current / Constant Voltage) protocol used by all lithium-ion chargers. It also analyses the harmonic distortion each charger type injects into the grid, and compares four converter topologies on efficiency, THD, and power factor.

The simulation is provided in two formats:
1. **Python Simulator (`Python/ev_charging_sim.py`)**: Runs headlessly to produce IEEE-style analytical figures for charging profiles and harmonic distortion.
2. **Web Simulator (`Simulator/EV_Charging_Simulaion.html`)**: An interactive, browser-based dashboard that visualizes the CC-CV charging process and compliance metrics in real-time without requiring Python dependencies.

---

## Section 1 — Imports and Grid Constant

```python
import numpy as np
import matplotlib
matplotlib.use('Agg')
...
GRID_FREQ_HZ = 50
```

Standard scientific Python libraries. `matplotlib.use('Agg')` switches matplotlib to a non-interactive backend — plots are saved to file instead of displayed in a window, which is necessary for running the script headlessly or on a server.

`GRID_FREQ_HZ = 50` sets the Indian grid frequency per CEA (Central Electricity Authority) regulations. This is used in the harmonic waveform generation to ensure the distorted current waveform is modelled at the correct base frequency.

---

## Section 2 — Configuration

### `BatteryConfig`

```python
class BatteryConfig:
    capacity_kwh = 60
    nominal_voltage = 400
    v_min_ratio = 0.82
    v_max_ratio = 1.05
    initial_soc = 20
    target_soc = 100
```

Defines the battery being simulated — representative of a mid-range Indian four-wheeler (Tata Nexon EV, MG ZS EV).

| Parameter | Value | What it means |
|---|---|---|
| `capacity_kwh` | 60 kWh | Total energy the battery can store |
| `nominal_voltage` | 400 V | Reference voltage for the pack (96 NMC cells × ~4.17V average) |
| `v_min_ratio` | 0.82 | Battery voltage at 0% SoC = 0.82 × 400V = 328V |
| `v_max_ratio` | 1.05 | Battery voltage at 100% SoC = 1.05 × 400V = 420V (96 cells × 4.375V) |
| `initial_soc` | 20% | Simulation starts at 20% — typical "arrived home" state |
| `target_soc` | 100% | Charge to full |

To simulate a different vehicle, change `capacity_kwh` and `nominal_voltage`. Everything else (amp-hour capacity, cutoff current, BMS limits) recalculates automatically.

---

### `ChargerConfig`

```python
class ChargerConfig:
    max_power_w = 7200
    max_current_a = 30
    cable_limit_a = 30
    temp_limit_a = float('inf')
    evse_max_limit_a = float('inf')
    cc_cv_transition = 80
    efficiency = 0.96
```

Default charger parameters — overridden at runtime by the preset selected. The defaults represent a basic Level 2 AC charger.

`cc_cv_transition = 80` means the simulation switches from CC to CV mode at 80% SoC, which is the standard transition point for NMC lithium-ion chemistry.

`temp_limit_a` and `evse_max_limit_a` default to infinity (no limit) — they exist as hooks for connector temperature derating (DIN 70121) and EVSE-communicated current limits (ISO 15118), which can be set for specific scenario modelling.

---

### `CHARGER_PRESETS`

```python
CHARGER_PRESETS = {
    'Bharat AC-001 (3.3kW)': {'power': 3300, 'current': 15, 'cable': 15},
    ...
    'DC Ultra-Fast (150kW)': {'power': 150000, 'current': 375, 'cable': 350},
}
```

Six charger types defined under Indian standards (AIS-138, BIS IS 17017, MoP EV Charging Guidelines 2022). Each preset overrides `ChargerConfig` with its rated power, current, and cable rating at runtime.

Note that DC preset currents are DC battery-side amperes — inherently higher than AC grid-side currents for the same power level, because DC chargers convert AC to DC internally before delivering to the battery.

---

### `TOPOLOGY_PROFILES`

```python
TOPOLOGY_PROFILES = {
    'Vienna + LLC Resonant (η=95.1%)': {
        'eta': 0.951,
        'thd_cc': 4.2, 'thd_cv': 2.1,
        'harmonics': {3: 0.0, 5: 3.2, 7: 2.1, ...},
        'cv_thd_stable': True,
    },
    ...
}
```

Four complete AC-DC converter systems, each representing a different hardware architecture used in EV chargers. Values are derived from published literature on each topology class (IEEE TPEL, TIE, TTE).

| Key | What it means |
|---|---|
| `eta` | Overall efficiency = η_PFC stage × η_DC-DC stage |
| `thd_cc` | Total Harmonic Distortion of AC input current during CC/CP mode (%) |
| `thd_cv` | THD during CV mode — lower because current is smaller |
| `harmonics` | Individual harmonic magnitudes as % of fundamental (3rd, 5th, 7th...) |
| `cv_thd_stable` | `True` for active PFC topologies (Vienna, AFE) — control loop holds THD steady at light load. `False` for passive diode bridge — THD rises as current tapers |

**Why triplen harmonics (3rd, 9th, 15th) are zero for Vienna and AFE:** The three-level topology structure mathematically cancels all odd multiples of 3 from the input current spectrum. This is a structural property, not a filtering effect.

**Standard scope:** THD percentage limits in the compliance table follow IEEE 519 / IS 16528, which apply at the feeder Point of Common Coupling (PCC). IEC 61000-3-2 (also cited) defines absolute amp limits per harmonic for equipment type approval — a different scope.

---

## Section 3 — Battery Model

### OCV Lookup Table

```python
_OCV_SOC_PTS  = np.array([0, 5, 10, 20, ..., 100])
_OCV_NORM_PTS = np.array([0.820, 0.838, ..., 1.050])
```

13-point piecewise lookup for NMC/NCM cell open-circuit voltage as a function of SoC. The flat region between 20–70% SoC captures the characteristic lithium-ion plateau that a simple polynomial would miss.

---

### `battery_ocv(soc, nominal_v, v_min_ratio, v_max_ratio)`

Returns the open-circuit voltage (OCV) of the battery at a given SoC. OCV is the voltage the battery settles at when no current flows — it rises as the battery fills.

**What it does:** Interpolates the NMC reference curve to get a normalized voltage ratio, then linearly remaps it from the reference range [0.82, 1.05] to the configured [v_min_ratio, v_max_ratio]. This allows the same curve shape to serve different pack configurations.

$$V_{ocv} = V_{nominal} \times \left[ v_{min} + \frac{v_{ref} - 0.820}{0.230} \times (v_{max} - v_{min}) \right]$$

---

### `internal_resistance(soc, nominal_v)`

Returns the pack's internal resistance at a given SoC. Models the well-established U-shaped curve — resistance is highest at both extremes (0% and 100% SoC), minimum near 50%.

$$R = R_{base} \times \left(1 + 2\left(0.5 - s\right)^2\right)$$

where $s = SoC/100$ and $R_{base} = V_{nominal} \times 0.0001$ (~0.04 Ω at 400V).

At SoC = 0% or 100%: R = 1.5 × R_base.  
At SoC = 50%: R = R_base (minimum).

---

### `bms_current_limit(soc, ah_capacity, max_c_rate=2.0)`

Returns the maximum current the Battery Management System will allow at the current SoC. The BMS derates charging current at high SoC to protect cells from lithium plating and thermal stress.

| SoC range | Allowed current |
|---|---|
| < 70% | Full rate (2C max) |
| 70–85% | 80% of max |
| 85–95% | 50% of max |
| > 95% | 20% of max |

These steps appear as visible drops in the current plot before the CC→CV transition.

---

## Section 4 — CC-CV Charging Simulation

### `simulate_charging(bat, chg, dt=1.0)`

The core simulation loop. Runs timestep-by-timestep (default: 1 second per step) from `initial_soc` to `target_soc`, computing voltage, current, power, and mode at each step.

**Setup:**

```python
v_max = bat.nominal_voltage * bat.v_max_ratio       # Maximum safe pack voltage
ah_capacity = capacity_wh / bat.nominal_voltage     # Pack capacity in amp-hours
cutoff_current = min(ah_capacity / 20, chg.max_current_a * 0.05)
```

`cutoff_current` is the threshold below which CV mode terminates. C/20 (one twentieth of amp-hour capacity) is the standard criterion. The `min()` with 5% of charger max current prevents premature cutoff for low-power chargers — without this cap, a 3.3kW charger on a 60kWh pack would terminate at ~80% SoC because C/20 = 7.5A is already 50% of its peak current.

---

**CC/CP Mode (SoC < 80%):**

The charger delivers as much current as all constraints collectively allow. Six candidate currents are computed and the minimum is taken:

```python
i_power   = quadratic solution for P = V_term × I  # Power ceiling
i_bms     = BMS derating at current SoC             # Cell protection
i_cable   = cable physical rating                   # Wire thermal limit
i_charger = charger hardware max                    # Power electronics limit
i_temp    = connector temperature limit             # DIN 70121
i_evse    = EVSE-communicated limit                 # ISO 15118

i_cc = min(i_power, i_bms, i_cable, i_charger, i_temp, i_evse)
```

The power limit current is derived from the terminal voltage equation $P = (V_{ocv} + I \cdot R_{int}) \cdot I$, solved via quadratic formula:

$$I_{power} = \frac{-V_{ocv} + \sqrt{V_{ocv}^2 + 4 R_{int} P_{max}}}{2 R_{int}}$$

**Mode labelling:** If `i_power` is the binding constraint (more than 1A below all hardware limits), the mode is labelled `CP` (Constant Power). Otherwise `CC` (Constant Current). AC chargers almost always show CC because the circuit breaker current limit binds first. DC fast chargers show CP because the rated power limit binds before cable or BMS limits.

---

**CV Mode (SoC ≥ 80%):**

Terminal voltage is clamped at `v_max`. Current decays exponentially:

$$I_{cv} = I_{last} \times e^{-k \times progress}$$

where `progress` goes from 0 (just entered CV) to 1 (at target SoC), and `k` is the decay exponent.

```python
c_rate = chg.max_current_a / ah_capacity
cv_exponent = np.clip(1.5 + 2.0 * c_rate, 1.5, 3.5)
```

The exponent scales with C-rate so that slow chargers (low C-rate) get a shallower decay tail, allowing them to reach higher SoC before hitting cutoff. Fast chargers (high C-rate) decay quickly, which matches their physical behavior.

**Voltage ramp at transition:** Instead of jumping instantly to `v_max` at the first CV timestep (which causes a one-timestep power spike), `v_term` is ramped from the last CC terminal voltage to `v_max` over the first 10% of CV progress:

```python
v_term = min(last_cc_vterm + (v_max - last_cc_vterm) * min(progress * 10, 1.0), v_max)
```

**Physical cap:** Current is also capped at what the battery can physically absorb given its current OCV and internal resistance:

```python
i_physical = (v_max - v_ocv) / r_int
i_cv = min(i_cv, i_physical, chg.max_current_a)
```

**SoC update (Coulomb counting):**

$$SoC_{new} = SoC + \frac{I \times \Delta t}{3600 \times Q_{Ah}} \times 100$$

Current (A) × time (s) = charge (coulombs). Divided by capacity in amp-hours (×3600 to convert seconds to hours) gives the fraction of capacity added. Multiplied by 100 for percentage.

**Returns:** Dictionary of time-series numpy arrays — `time_min`, `voltage`, `current`, `power_kw`, `soc`, `mode`.

---

## Section 5 — Harmonic Analysis

### `compute_thd_profile(soc_arr, mode_arr, topology_name)`

Computes THD at each timestep across the full charge cycle. In CC/CP mode, THD is flat at the topology's rated `thd_cc` value. In CV mode:

- Active PFC topologies (`cv_thd_stable = True`): THD stays flat at `thd_cv` — the control loop maintains current shaping even at light load.
- Passive front-end (`cv_thd_stable = False`): THD rises linearly as current tapers, because the diode bridge has no active control to compensate.

---

### `harmonic_spectrum(topology_name, mode)`

Returns the individual harmonic magnitudes for a given topology and mode. CV mode applies a 0.6 scale factor to all harmonics, reflecting the reduced absolute distortion at lower current.

---

### `power_factor_from_thd(thd)`

$$PF = \frac{DPF}{\sqrt{1 + (THD/100)^2}}$$

DPF (Displacement Power Factor) is hardcoded at 0.99, appropriate for active PFC topologies. The denominator accounts for the distortion component — higher THD produces lower total power factor even if the fundamental is in phase with voltage.

---

### `generate_waveform(topology_name, mode, cycles, points)`

Constructs a synthetic distorted current waveform by superimposing harmonics onto a fundamental sine wave:

$$i(t) = \sin(\omega t) + \sum_{n} \frac{H_n}{100} \cdot \sin(n\omega t + n \times 0.3)$$

The phase offset `n × 0.3` introduces a realistic (non-zero) phase shift per harmonic. The waveform is in normalized angular time (ωt), making it frequency-agnostic — the 50 Hz label is applied at the plot axis level.

---

## Section 6 — Plotting

### Color Palette

```python
DARK_BG = '#0a0a14'    # Figure background
CARD_BG = '#13132a'    # Axes background
PURPLE  = '#7c6cf0'    # Waveform / harmonic bars (CC mode)
TEAL    = '#00d4c8'    # CV mode overlays
PINK    = '#ff6b9d'    # Voltage curve
BLUE    = '#6b9dff'    # Current curve
GREEN   = '#4ae0a0'    # Power curve / power factor
YELLOW  = '#ffc857'    # SoC curve / transition markers
RED     = '#ff5252'    # Limit lines
```

---

### `plot_charging_profile(data, topology_name, save_path)`

**Figure 1** — 2×2 grid of subplots showing the full CC-CV cycle:

- Top-left: Battery Voltage (V) vs time
- Top-right: Charging Current (A) vs time
- Bottom-left: Charging Power (kW) vs time
- Bottom-right: State of Charge (%) vs time

A dashed vertical line marks the CC/CP→CV transition on all four subplots. The final value of each quantity is annotated at the endpoint. A footer shows topology, battery spec, SoC range, and total time.

---

### `plot_harmonics(data, topology_name, save_path)`

**Figure 2** — 3-row harmonic analysis layout:

- **Row 1 (full width):** Grid current waveform at PCC — pure sine vs distorted CC mode vs distorted CV mode
- **Row 2 left:** Individual harmonic magnitudes (CC and CV mode) as grouped bar chart, with 4% IEC limit line
- **Row 2 right:** THD vs SoC across full charge cycle, showing the drop at CC→CV transition
- **Row 3 left:** Compliance table — THD, individual harmonics, and power factor checked against IEEE 519 / IS 16528 limits
- **Row 3 right:** Power factor vs SoC, with 0.95 minimum line

---

### `plot_topology_comparison(save_path)`

**Figure 3** — Side-by-side comparison of all four converter topologies:

- **Left:** THD by topology (CC and CV mode), with 5% IEC limit line
- **Centre:** Charger efficiency (%)
- **Right:** Power factor (CC and CV mode), with 0.95 minimum line

This figure is topology-independent of the charging simulation — it shows the AC input characteristics of each converter class from literature, regardless of which charger preset was run.

---

## Section 7 — Simulation Runner

### `preset_output_dir(base_output_dir, preset_name)`

Returns a filesystem-safe subdirectory path for a given charger preset. Replaces OS path separators in preset names (which contain slashes and parentheses) with underscores to avoid directory creation errors.

---

### `run_simulation_for_preset(preset_name, topology, base_output_dir)`

Orchestrates the full workflow for one charger preset:

1. Creates output directory
2. Builds a `ChargerConfig` from the preset dict
3. Runs `simulate_charging()` and prints summary statistics
4. Calls `plot_charging_profile()` → saves `fig1_charging_profile.png`
5. Calls `plot_harmonics()` → saves `fig2_harmonics.png`
6. Calls `plot_topology_comparison()` → saves `fig3_topology_comparison.png`

Returns the output directory path.

---

## Section 8 — Entry Point

### `main()`

Parses three command-line arguments:

| Argument | Default | Options |
|---|---|---|
| `--charger` | `all` | Any preset name, or `all` |
| `--topology` | `Vienna + LLC Resonant (η=95.1%)` | Any key in `TOPOLOGY_PROFILES` |
| `--output-dir` | `../Output/EV_Dynamic_Charging_Simulation_Results` | Any valid path |

Runs `run_simulation_for_preset()` for each selected preset. On single-preset runs, opens the three output figures automatically (macOS only — `open` command).

**Example usage:**

```bash
# Run all presets with default topology
python ev_charging_sim.py

# Run only DC Fast charger
python ev_charging_sim.py --charger "DC Fast (60kW)"

# Run with Diode Bridge topology
python ev_charging_sim.py --topology "Diode Bridge + Boost PFC + Full-Bridge (η=91.2%)"

# Specify custom output directory
python ev_charging_sim.py --output-dir ./results
```

---

## Output Structure

```
Output/
└── EV_Dynamic_Charging_Simulation_Results/
    ├── Bharat AC-001 (3.3kW)/
    │   ├── fig1_charging_profile.png
    │   ├── fig2_harmonics.png
    │   └── fig3_topology_comparison.png
    ├── AC Level 2 1-phase (7.4kW)/
    │   └── ...
    └── DC Ultra-Fast (150kW)/
        └── ...
```

One subdirectory per charger preset, each containing three figures. Figure 3 (topology comparison) is identical across all preset subdirectories since it does not depend on the charging simulation output.

---

## Interactive Web Simulator

The `Simulator/` directory contains an interactive, dependency-free HTML/JS version of the charging physics and harmonic analysis models. Simply open `EV_Charging_Simulaion.html` in any modern web browser to interact with the models, adjust battery/charger parameters, and monitor real-time charging curves and spectrum compliance visually.
