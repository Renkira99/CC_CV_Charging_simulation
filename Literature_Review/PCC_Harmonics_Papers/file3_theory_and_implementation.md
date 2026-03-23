# File 3: PCC Harmonic Analysis — Theory, Literature Basis, and Implementation Guide

## What This File Does

`pcc_harmonic_analysis.py` calculates the actual harmonic currents injected into the Indian distribution grid at the **Point of Common Coupling (PCC)** — the AC connection point between the EV charging station and the utility feeder.

Files 1 and 2 characterize *what* a charger looks like from a topology perspective. File 3 answers the engineering question your guide actually asked: **how many amps of 5th harmonic, 7th harmonic, etc. is this specific charger injecting into the feeder at each moment during the charge cycle?**

---

## Theory

### Step 1 — Fundamental Current at the PCC

The grid does not see the battery voltage or the DC side of the charger. It sees the AC input current drawn from the feeder. That current is the **fundamental current** at the PCC.

For a single-phase charger (Bharat AC-001, Level 2 1-phase):

$$I_{fundamental} = \frac{P_{charger}}{V_{grid} \times PF}$$

For a three-phase charger (Level 2 3-phase, DC fast, DC ultra-fast):

$$I_{fundamental} = \frac{P_{charger}}{V_{grid} \times \sqrt{3} \times PF}$$

Where:
- $P_{charger}$ = instantaneous power drawn from the grid in watts (from `simulate_charging()` output, `power_kw × 1000`)
- $V_{grid}$ = preset-specific AC input voltage at the PCC: 230V for single-phase presets and 415V line-line for three-phase presets in this project
- $PF$ = power factor (from `power_factor_from_thd()`)
- $\sqrt{3}$ = 1.732 for three-phase systems

**Validation anchor (Sivaraman 2021, IEEE MASCON):** A nominal 50kW DC fast charger reference on a 440V Indian feeder reports a measured fundamental current of 62.24A. Direct substitution with nominal values does not close exactly; implementation treats this as a methodology check with explicit tolerance and an implied-power diagnostic (~47.6kW), which is consistent with non-nominal operating conditions and losses.

---

### Step 2 — Individual Harmonic Currents in Amps

Each harmonic order injects a current proportional to the fundamental:

$$I_n = \frac{H_n}{100} \times I_{fundamental}$$

Where $H_n$ is the harmonic magnitude as a percentage of the fundamental for order $n$, taken from `TOPOLOGY_PROFILES['harmonics']`.

This gives currents in amps for h3, h5, h7, h9, h11, h13 at each timestep of the charge cycle.

**Literature basis:** Senol (2024, IEEE Access) and Mariscotti (2022, MDPI Smart Cities) both confirm that individual harmonic currents scale with the fundamental. Neither paper provides pre-tabulated amp values directly — the calculation approach above is the standard methodology used in IEEE 519 and IEC 61000-3-2 compliance engineering.

---

### Step 3 — Why Harmonic Currents Vary Through the Charge Cycle

This is the critical insight that distinguishes file 3 from file 2.

**In CC/CP mode (full load):**
- Fundamental current is high (e.g., 62.24A for 50kW at 440V)
- THD percentage is low (e.g., 4.2% for Vienna+LLC)
- But absolute harmonic amps are at their **maximum**
- Example: $I_5 = \frac{3.2}{100} \times 62.24 = 1.99\text{ A}$

**In CV mode (partial load, battery nearly full):**
- Fundamental current is falling exponentially
- THD percentage rises (active PFC topologies hold it; passive front-ends rise further)
- Absolute harmonic amps decrease
- But **percentage-based compliance limits may be violated** even as amps fall

**Lucas (2015, Electric Power Systems Research)** captures this precisely: TDD spiked to 40% when fundamental dropped below 10A, even though the absolute harmonic injection was low. This is the mathematical trap of THD-only analysis — file 3 reports both.

**Senol (2024)** models THD as a quadratic polynomial of charging power, confirming the nonlinear rise in THD as power tapers in CV mode. This is why `compute_thd_profile()` in file 2 models THD differently for active vs passive topologies — file 3 inherits that behavior.

---

### Step 4 — Compliance Check

Two standards apply, with different scopes:

**IEC 61000-3-2 Class A — Equipment level (≤16A per phase)**
Applies to: Bharat AC-001 (3.3kW, 15A), AC Level 2 1-phase (7.4kW, 32A at the borderline)

These are **absolute amp limits**, independent of fundamental current magnitude (Mariscotti 2022):

| Harmonic Order | IEC 61000-3-2 Limit (A) |
|---|---|
| h3 | 2.30 |
| h5 | 1.14 |
| h7 | 0.77 |
| h9 | 0.40 |
| h11 | 0.33 |
| h13 | 0.21 |

**IEC 61000-3-12 — Equipment level (16A–75A per phase)**
Applies to: AC Level 2 3-phase (22kW, 32A per phase)
Limits are percentage-based, dependent on short-circuit ratio Rsc ≥ 350:

| Harmonic Order | IEC 61000-3-12 Limit (% of fundamental) |
|---|---|
| h3 | 21.6% |
| h5 | 10.7% |
| h7 | 7.2% |
| h9 | 3.8% |
| h11 | 3.1% |
| h13 | 2.0% |

**IEEE 519 / IS 16528 — System level (at the feeder PCC)**
Applies to: All DC chargers (Bharat DC-001, DC Fast, DC Ultra-Fast)
Uses Total Demand Distortion (TDD) rather than THD:

$$TDD = \frac{\sqrt{\sum_{n=2}^{\infty} I_n^2}}{I_L} \times 100$$

Where $I_L$ is the maximum demand load current (the peak fundamental current during CC mode). TDD limit for most Indian LV feeders: **5%**

**Why TDD not THD:** THD references the instantaneous fundamental which collapses in CV mode, artificially inflating the percentage. TDD references the peak demand current — a stable reference. Senol (2024) and the Gemini deep research synthesis both explicitly recommend TDD for grid impact assessment.

---

### Step 5 — Topology Rules for Harmonic Orders

Confirmed by Senol (2024) and Sivaraman (2021) FFT analysis at 250Hz, 350Hz, 550Hz, 650Hz:

**Single-phase chargers** (Bharat AC-001, AC Level 2 1-phase):
- h3, h5, h7 are dominant
- h9, h11, h13 present but lower
- Triplen harmonics (h3, h9) are **non-zero** and accumulate in the neutral conductor
- This is the primary safety concern for Indian 3-phase 4-wire networks with undersized neutral conductors

**Three-phase chargers** (AC Level 2 3-phase, all DC presets):
- h3 = 0, h9 = 0 (triplen harmonics cancel in balanced three-phase systems)
- h5, h7, h11, h13 are dominant
- This is why Vienna rectifier profiles show h3=0.0 in `TOPOLOGY_PROFILES`

**Neutral current risk (Indian grid specific):**
Single-phase EVs with high h3 injection are dangerous on Indian LV networks because triplen harmonics add arithmetically in the neutral rather than cancelling. Sivaraman (2021) identifies this as a primary concern for Indian distribution transformer health.

---

### Step 6 — Transformer Derating

From Sivaraman (2021), a 200kVA distribution transformer (standard Indian urban feeder) derated as:

| Number of EVs | THD | Effective Transformer Capacity |
|---|---|---|
| 1 EV | 4.67% | 196.63 kVA |
| 3 EVs | 14.07% | 191.64 kVA |
| 5 EVs | 23.55% | 186.11 kVA |

File 3 computes this using the **K-factor** or **Harmonic Loss Factor (FHL)**:

$$F_{HL} = \frac{\sum_{n=1}^{N} I_n^2 \cdot n^2}{\sum_{n=1}^{N} I_n^2}$$

$$S_{derated} = \frac{S_{rated}}{\sqrt{F_{HL}}}$$

Higher-order harmonics contribute more to transformer derating because eddy current losses scale with $n^2$.

---

## What File 3 Produces

Four outputs, building on files 1 and 2:

**Figure 4a — Harmonic current time series (A vs time)**
Individual h3, h5, h7, h11, h13 in amps plotted across the full charge cycle. Shows the absolute injection peak in CC mode and the exponential decay in CV mode.

**Figure 4b — Compliance check at PCC**
Bar chart showing computed harmonic amps vs IEC 61000-3-2 / IEC 61000-3-12 limits. Pass/fail per harmonic order at the worst-case (CC mode peak) point.

**Figure 4c — TDD vs time**
Total Demand Distortion as a percentage across the charge cycle, with IS 16528 5% limit line. Shows how TDD evolves from CC to CV phase.

**Figure 4d — Transformer derating**
K-factor and derated transformer capacity as a function of number of EVs, based on Sivaraman (2021) methodology applied to computed harmonic spectrum.

---

## Prompt for Your AI Coding Agent

Give this to Claude Code or your IDE agent verbatim:

---

```
Create a new file `pcc_harmonic_analysis.py` in the Python/ directory.

This is the third module of a three-file EV charging simulator project.
- File 1: ev_charging_sim.py — CC-CV charging simulation
- File 2: harmonic_characterization.py — topology harmonic profiles from literature
- File 3 (this file): PCC harmonic current calculation at 440V, 50Hz Indian grid

IMPORTS
Import from ev_charging_sim: simulate_charging, CHARGER_PRESETS, TOPOLOGY_PROFILES,
BatteryConfig, ChargerConfig, GRID_FREQ_HZ
Import from harmonic_characterization: harmonic_spectrum, power_factor_from_thd,
compute_thd_profile, setup_ieee_style, and all color constants
Import numpy, matplotlib, gridspec, AutoMinorLocator, argparse, os, warnings

CONSTANTS — add these at the top with citations:

# IEC 61000-3-2 Class A absolute harmonic current limits in amps (equipment ≤16A/phase)
# Source: Mariscotti 2022 (MDPI Smart Cities) — confirmed via IEC 61000-3-2 standard
IEC_61000_3_2_A = {3: 2.30, 5: 1.14, 7: 0.77, 9: 0.40, 11: 0.33, 13: 0.21}

# IEC 61000-3-12 limits as % of fundamental for Rsc >= 350 (equipment 16A–75A/phase)
# Source: Gemini deep research synthesis of IEC 61000-3-12
IEC_61000_3_12_PCT = {3: 21.6, 5: 10.7, 7: 7.2, 9: 3.8, 11: 3.1, 13: 2.0}

# IS 16528 / IEEE 519 TDD limit at PCC for Indian LV distribution (440V)
IS_16528_TDD_LIMIT_PCT = 5.0

# Indian grid voltage — LV distribution standard
GRID_VOLTAGE_V = 440.0

# Validated reference: Sivaraman 2021 (IEEE MASCON) measured 62.24A fundamental
# for a 50kW DC fast charger on a 440V Indian feeder — used as validation anchor
SIVARAMAN_VALIDATION = {'power_kw': 50, 'voltage_v': 440, 'measured_fundamental_a': 62.24}

FUNCTION 1: compute_fundamental_current(power_w, grid_voltage, pf, phases)
  Compute the fundamental AC current drawn from the grid at the PCC.
  For single-phase: I = P / (V * PF)
  For three-phase: I = P / (V * sqrt(3) * PF)
  phases parameter: 1 or 3
  Return float in amps.
  Add docstring citing Sivaraman 2021 validation.

FUNCTION 2: get_charger_phases(preset_name)
  Return 1 or 3 based on charger preset name.
  Single-phase presets: 'Bharat AC-001 (3.3kW)', 'AC Level 2 1-phase (7.4kW)'
  Three-phase presets: all others
  Return int.

FUNCTION 3: compute_pcc_harmonic_currents(data, topology_name, preset_name,
                                           grid_voltage=GRID_VOLTAGE_V)
  For each timestep in the simulation data:
    1. Get instantaneous power in watts from data['power_kw'] * 1000
    2. Get THD from compute_thd_profile at that timestep
    3. Compute PF from power_factor_from_thd(THD)
    4. Determine phases from get_charger_phases(preset_name)
    5. Compute I_fundamental using compute_fundamental_current
    6. Get harmonic percentages from TOPOLOGY_PROFILES[topology_name]['harmonics']
    7. For three-phase chargers, zero out h3 and h9 (triplen cancellation)
       — cite Senol 2024 for this topology rule
    8. For each harmonic order n in [3,5,7,9,11,13]:
       I_n = (H_n / 100) * I_fundamental
    9. Compute TDD at this timestep:
       I_L = max(I_fundamental array) — peak demand current
       TDD = sqrt(sum(I_n^2 for all n)) / I_L * 100
  Return dict with keys:
    'time_min': array
    'i_fundamental': array
    'harmonic_currents': dict of arrays keyed by harmonic order {3,5,7,9,11,13}
    'tdd': array
    'i_L': float (peak demand current, scalar)

FUNCTION 4: get_applicable_standard(preset_name)
  Return which IEC standard applies based on charger current rating:
  'Bharat AC-001 (3.3kW)': return 'IEC_61000_3_2' (15A ≤ 16A threshold)
  'AC Level 2 1-phase (7.4kW)': return 'IEC_61000_3_12' (32A > 16A threshold)
  'AC Level 2 3-phase (22kW)': return 'IEC_61000_3_12' (32A per phase)
  All DC presets: return 'IEEE_519_IS_16528' (off-board chargers, system level)

FUNCTION 5: check_compliance(harmonic_currents_at_peak, i_fundamental_at_peak,
                               preset_name, topology_name)
  Check compliance at the peak CC mode point (worst case absolute amps).
  Get applicable standard from get_applicable_standard().
  For IEC_61000_3_2:
    Compare I_n in amps against IEC_61000_3_2_A limits
    Pass if I_n <= limit for each order
  For IEC_61000_3_12:
    Compare I_n as % of I_fundamental against IEC_61000_3_12_PCT limits
    Pass if (I_n / I_fundamental * 100) <= limit for each order
  For IEEE_519_IS_16528:
    Use TDD at peak — compare against IS_16528_TDD_LIMIT_PCT
  Return list of tuples: (order, measured_value, limit, unit_str, pass_bool)

FUNCTION 6: compute_transformer_derating(harmonic_currents_at_peak, i_fundamental_at_peak,
                                          n_evs_list=[1,3,5], rated_kva=200)
  Compute K-factor (Harmonic Loss Factor FHL) and derated transformer capacity.
  FHL = sum(I_n^2 * n^2) / sum(I_n^2) for all harmonic orders including fundamental
  Derated capacity = rated_kva / sqrt(FHL) in kVA
  Scale linearly for n_evs (simplified aggregation without phase diversity).
  Return dict: {n_evs: {'fhl': float, 'derated_kva': float}}
  Add docstring citing Sivaraman 2021 (IEEE MASCON) reference values:
  1 EV → 196.63 kVA, 3 EVs → 191.64 kVA, 5 EVs → 186.11 kVA

FUNCTION 7: plot_pcc_analysis(data, pcc_data, compliance_results, derating_results,
                               topology_name, preset_name, save_path=None)
  IEEE-style figure, double-column width (7.16 in), 2x2 layout.
  Use setup_ieee_style() from harmonic_characterization.

  Subplot (0,0) — Harmonic Current Time Series:
    Plot I_fundamental in black solid line
    Plot h5 in black dashed, h7 dotted, h11 dash-dot, h13 loosely dashed
    Skip h3 for three-phase (it's zero), plot h3 for single-phase
    x-axis: Time (min), y-axis: Current (A)
    Title: 'Harmonic Current Injection at 440 V PCC'
    Legend showing each harmonic order
    Vertical dashed line at CC→CV transition

  Subplot (0,1) — TDD vs Time:
    Plot TDD array as black solid line
    Horizontal red dashed line at IS_16528_TDD_LIMIT_PCT (5%)
    x-axis: Time (min), y-axis: TDD (%)
    Title: 'Total Demand Distortion vs. Time'
    Annotate peak TDD value
    Vertical dashed line at CC→CV transition

  Subplot (1,0) — Compliance Bar Chart:
    Grouped bar chart: measured value vs limit for each harmonic order
    Dark gray bars for measured, light gray hatched bars for limit
    x-axis: harmonic orders (h3, h5, h7, h9, h11, h13)
    y-axis: Current (A) for IEC 61000-3-2, % for others
    Title: f'PCC Harmonic Compliance — {applicable_standard}'
    Mark failing bars with red edge, passing bars with black edge
    Add compliance status text box: COMPLIANT or NON-COMPLIANT

  Subplot (1,1) — Transformer Derating:
    Bar chart showing derated kVA for 1, 3, 5 EVs
    Horizontal black dashed line at rated_kva (200 kVA)
    x-axis: Number of EVs, y-axis: Effective Capacity (kVA)
    Annotate each bar with its kVA value
    Title: 'Distribution Transformer Derating (200 kVA base)'
    Add footnote: 'Methodology: Sivaraman et al., IEEE MASCON 2021'

  Footer caption (italic): cite all four papers
  Save at 300 DPI white background if save_path provided.

FUNCTION 8: run_pcc_analysis_for_preset(preset_name, topology, base_output_dir)
  Mirror the structure of run_simulation_for_preset in ev_charging_sim.py.
  1. Create preset output directory (reuse preset_output_dir from ev_charging_sim)
  2. Run simulate_charging with preset config
  3. Run compute_pcc_harmonic_currents
  4. Extract peak CC mode values for compliance and derating
  5. Run check_compliance
  6. Run compute_transformer_derating
  7. Print summary to terminal:
     - Fundamental current at PCC (A): peak and end values
     - Peak harmonic currents in amps for each order
     - TDD at peak and at end of charge
     - Compliance status per standard
     - Transformer derated capacity for 1, 3, 5 EVs
  8. Call plot_pcc_analysis and save as fig4_pcc_harmonic_analysis.png
  9. Return preset_dir

MAIN FUNCTION:
  Same argparse structure as ev_charging_sim.py.
  Arguments: --charger (default 'all'), --topology (default Vienna+LLC), --output-dir
  Loop over selected presets and call run_pcc_analysis_for_preset.
  Print completion summary.
  For single preset runs, open the figure (macOS only, os.system).

VALIDATION CHECK (add as a module-level function called validate_sivaraman()):
  Compute fundamental current for 50kW, 440V, 3-phase, PF=0.989
  Print computed value vs Sivaraman measured value of 62.24A
  Print percentage error
  This runs automatically when the module is imported, prints one line of validation output.

STYLE REQUIREMENTS:
- All IEEE-style plots using setup_ieee_style() — white background, Times New Roman,
  inward ticks, 300 DPI
- All functions have docstrings citing the specific paper each methodology comes from
- All constants have inline comments with paper citations
- No dark theme — this file uses the same IEEE style as harmonic_characterization.py
- Follow the same local import pattern as ev_charging_sim.py to avoid circular imports
  (import from harmonic_characterization inside the function body if needed)
```

---

## How to Explain This to Your Guide

**What files 1 and 2 do:**
File 1 simulates how the battery charges over time — voltage, current, power, SoC. File 2 shows what harmonic distortion profile each converter topology class is known to produce, based on published literature values expressed as percentages.

**What file 3 adds:**
Files 1 and 2 don't tell you how many amps of 5th harmonic a specific charger injects into the Indian feeder. File 3 computes that by combining the instantaneous power draw from file 1 with the harmonic percentage profiles from file 2 and preset-specific PCC voltage (230V single-phase, 415V three-phase). The result is a time-varying harmonic current spectrum in amps at the PCC — which is what IS 16528 and IEC 61000-3-2 actually regulate.

**Why the literature doesn't give amps directly:**
The four papers (Mariscotti 2022, Senol 2024, Lucas 2015, Sivaraman 2021) confirm the dominant harmonic orders, the CC-to-CV behavioral shift, and the compliance thresholds, but don't pre-tabulate amps for every charger type at every operating point. That computation — using the standard methodology of scaling harmonic percentages by the fundamental current — is the specific contribution of this module.

**The Sivaraman validation:**
Sivaraman measured 62.24A fundamental for a nominal 50kW charger at 440V on an Indian feeder. In implementation this reference is used as a methodology anchor with tolerance, not a strict exact-equality unit target, because measured current implies an effective delivered power below nominal.

**The transformer derating result:**
Sivaraman shows that harmonic injection from EV chargers derated a 200kVA transformer to 186.11kVA with 5 EVs. File 3 reproduces this calculation using the K-factor methodology, then applies it to each charger preset — quantifying the infrastructure impact in terms your guide can directly relate to Indian distribution planning.

**The OpenDSS link:**
The `harmonic_currents` dict and `i_fundamental` array from `compute_pcc_harmonic_currents()` are the exact inputs OpenDSS needs for a harmonic power flow via `opendssdirect`. The power time series from file 1 becomes the LoadShape. The harmonic spectrum from file 3 becomes the harmonic load model at that bus. File 3 completes the single-charger characterization — OpenDSS aggregates it across the feeder.

---

## Literature Citations for File 3

1. **Mariscotti, A.** "Harmonic and Supraharmonic Emissions of Plug-In Electric Vehicle Chargers." *MDPI Smart Cities*, 2022. — IEC 61000-3-2 absolute amp limits; 9-vehicle Netherlands compliance study.

2. **Senol, M. and Bayram, I.S.** "Impact Assessment and Mitigation of Electric Vehicle Smart Charging Harmonics." *IEEE Access*, 2024. DOI: 10.1109/ACCESS.2024.0429000 — THD as quadratic function of charging power; topology-based harmonic order rules; single-phase vs three-phase dominant orders.

3. **Lucas, A., Bonavitacola, F., Kotsakis, E., Fulli, G.** "Grid harmonic impact of multiple electric vehicle fast charging." *Electric Power Systems Research*, Vol. 127, 2015. — TDD methodology; 67.5A DC fast charger baseline; harmonic cancellation behavior; 6.1A per harmonic modelling estimate.

4. **Sivaraman, P., Sakthi Suriya Raj, J.S., Ajith Kumar, P.** "Power quality impact of electric vehicle charging station on utility grid." *IEEE MASCON 2021*. DOI: 10.1109/MASCON51689.2021.9563528 — 440V Indian grid validation (62.24A measured fundamental); 200kVA transformer derating for 1/3/5 EVs; FFT dominant orders at 50Hz.
