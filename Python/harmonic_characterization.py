"""
Harmonic Characterization & Plotting for EV Charging Simulator
IEEE Transactions-style publication figures.
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import AutoMinorLocator, MultipleLocator
import matplotlib.patches as mpatches
import warnings
warnings.filterwarnings('ignore')

from ev_charging_sim import TOPOLOGY_PROFILES, BatteryConfig, GRID_FREQ_HZ

# ============================================================
#  IEEE STYLE CONSTANTS
# ============================================================

# IEEE single-column: 3.5 in, double-column: 7.16 in
IEEE_SINGLE = 3.5
IEEE_DOUBLE = 7.16
IEEE_FONT   = 'Times New Roman'

# Line styles and markers for black-and-white compatibility
LINE_STYLES  = ['-', '--', '-.', ':']
MARKERS      = ['o', 's', '^', 'D']
HATCHES      = ['///', '...', '\\\\\\', 'xxx']

# Grayscale ramp for bar charts (dark → light)
GRAYS = ['#1a1a1a', '#4d4d4d', '#808080', '#b3b3b3']

# Accent color for compliance pass/fail only
PASS_COLOR = '#2c7bb6'   # IEEE blue
FAIL_COLOR = '#d7191c'   # IEEE red
LIMIT_COLOR = '#d7191c'

# ============================================================
#  IEEE STYLE SETUP
# ============================================================

def setup_ieee_style():
    """Apply IEEE Transactions figure style globally."""
    plt.rcParams.update({
        # Figure
        'figure.facecolor':     'white',
        'figure.dpi':           300,
        'savefig.dpi':          300,
        'savefig.bbox':         'tight',
        'savefig.pad_inches':   0.05,

        # Axes
        'axes.facecolor':       'white',
        'axes.edgecolor':       'black',
        'axes.linewidth':       0.8,
        'axes.labelcolor':      'black',
        'axes.labelsize':       8,
        'axes.titlesize':       8,
        'axes.titleweight':     'bold',
        'axes.titlepad':        4,
        'axes.spines.top':      True,
        'axes.spines.right':    True,

        # Grid — IEEE uses very subtle grid or none
        'axes.grid':            True,
        'grid.color':           '#cccccc',
        'grid.linewidth':       0.4,
        'grid.alpha':           0.6,
        'grid.linestyle':       ':',

        # Ticks
        'xtick.color':          'black',
        'ytick.color':          'black',
        'xtick.labelsize':      7,
        'ytick.labelsize':      7,
        'xtick.direction':      'in',
        'ytick.direction':      'in',
        'xtick.major.width':    0.8,
        'ytick.major.width':    0.8,
        'xtick.minor.width':    0.5,
        'ytick.minor.width':    0.5,
        'xtick.major.size':     3,
        'ytick.major.size':     3,
        'xtick.minor.size':     1.5,
        'ytick.minor.size':     1.5,
        'xtick.top':            True,
        'ytick.right':          True,

        # Font — Times New Roman throughout
        'font.family':          'serif',
        'font.serif':           [IEEE_FONT, 'DejaVu Serif', 'serif'],
        'font.size':            8,
        'text.color':           'black',

        # Legend
        'legend.facecolor':     'white',
        'legend.edgecolor':     'black',
        'legend.fontsize':      7,
        'legend.framealpha':    1.0,
        'legend.borderpad':     0.4,
        'legend.handlelength':  2.0,

        # Lines
        'lines.linewidth':      1.2,
        'lines.markersize':     4,

        # Math text
        'mathtext.fontset':     'stix',
    })


# ============================================================
#  HARMONIC ANALYSIS FUNCTIONS  (unchanged logic)
# ============================================================

def compute_thd_profile(soc_arr, mode_arr, topology_name, cc_cv_transition=80):
    """Compute THD at each SoC point based on converter topology."""
    profile = TOPOLOGY_PROFILES[topology_name]
    cv_thd_stable = profile.get('cv_thd_stable', False)
    thd_arr = []
    for s, m in zip(soc_arr, mode_arr):
        if m in ('CC', 'CP'):
            thd_arr.append(profile['thd_cc'])
        else:
            if cv_thd_stable:
                thd_arr.append(profile['thd_cv'])
            else:
                progress = min((s - cc_cv_transition) / (100 - cc_cv_transition), 1.0)
                thd_arr.append(profile['thd_cv'] * (1 + 1.5 * progress))
    return np.array(thd_arr)


def harmonic_spectrum(topology_name, mode='CC'):
    """Get harmonic magnitudes for a given topology and mode.

    Scale logic:
    - CC/CP mode: raw literature values (scale = 1.0).
    - CV mode, active PFC (cv_thd_stable=True): the control loop holds harmonic
      content steady — scale stays at 1.0 for individual harmonics.
    - CV mode, passive front-end (cv_thd_stable=False): fundamental current tapers
      while harmonic currents from filter resonance remain roughly constant, so
      harmonics as a % of fundamental *increase*. We scale by thd_cv/thd_cc to
      match the qualitative direction shown by compute_thd_profile.
    """
    profile = TOPOLOGY_PROFILES[topology_name]
    if mode in ('CC', 'CP'):
        scale = 1.0
    elif profile.get('cv_thd_stable', False):
        # Active PFC: loop holds harmonic content — individual harmonics stay flat
        scale = 1.0
    else:
        # Passive front-end: harmonics rise as % of fundamental at light load
        # Use thd_cv/thd_cc as the scaling ratio (consistent with compute_thd_profile)
        scale = profile['thd_cv'] / profile['thd_cc'] if profile['thd_cc'] > 0 else 1.0
    orders = sorted(profile['harmonics'].keys())
    magnitudes = [profile['harmonics'][h] * scale for h in orders]
    return orders, magnitudes


def power_factor_from_thd(thd):
    """PF ≈ DPF / sqrt(1 + (THD/100)²)"""
    dpf = 0.99
    return dpf / np.sqrt(1 + (thd / 100) ** 2)


def generate_waveform(topology_name, mode='CC', cycles=3, points=1000):
    """Generate distorted grid current waveform (50 Hz)."""
    profile = TOPOLOGY_PROFILES[topology_name]
    scale = 1.0 if mode in ('CC', 'CP') else 0.6
    t = np.linspace(0, cycles * 2 * np.pi, points)
    y_pure = np.sin(t)
    y_distorted = np.sin(t)
    # Harmonics are increased x3 purely for visualization clarity, not physical accuracy
    for order, mag in profile['harmonics'].items():
        y_distorted += (mag * scale * 3 / 100) * np.sin(order * t + order * 0.3)
    return t, y_pure, y_distorted


# ============================================================
#  PLOT 1 — CC-CV CHARGING PROFILE  (IEEE double-column)
# ============================================================

def plot_charging_profile(data, topology_name, save_path=None):
    """
    IEEE-style CC-CV charging profile.
    2×2 subplot, double-column width (7.16 in).
    Each subplot uses a distinct line style for B&W readability.
    """
    setup_ieee_style()

    fig, axes = plt.subplots(2, 2, figsize=(IEEE_DOUBLE, 4.5))
    fig.suptitle(
        'CC-CV Dynamic Charging Profile',
        fontsize=9, fontweight='bold', y=1.01
    )

    # Find CC/CP → CV transition
    trans_idx = None
    for i, m in enumerate(data['mode']):
        if i > 0 and data['mode'][i - 1] in ('CC', 'CP') and m == 'CV':
            trans_idx = i
            break

    configs = [
        (axes[0, 0], data['voltage'],  'Terminal Voltage (V)',      'V'),
        (axes[0, 1], data['current'],  'Charging Current (A)',      'A'),
        (axes[1, 0], data['power_kw'], 'Charging Power (kW)',       'kW'),
        (axes[1, 1], data['soc'],      'State of Charge (%)',       '%'),
    ]

    for ax, y, ylabel, unit in configs:
        ax.plot(data['time_min'], y,
                color='black', linewidth=1.2, linestyle='-', zorder=5)

        # Minor ticks
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())

        ax.set_xlabel('Time (min)', fontsize=8)
        ax.set_ylabel(ylabel, fontsize=8)

        # Transition marker — vertical dashed line
        if trans_idx is not None:
            t_trans = data['time_min'][trans_idx]
            ax.axvline(t_trans, color='black', linestyle='--',
                       linewidth=0.8, alpha=0.7, zorder=4)
            yrange = ax.get_ylim()
            ax.text(
                t_trans + data['time_min'][-1] * 0.01,
                yrange[0] + (yrange[1] - yrange[0]) * 0.92,
                f"{data['mode'][trans_idx - 1]}→CV",
                fontsize=6.5, style='italic', va='top'
            )

        # End-point annotation — plain text box, no color fill
        ax.annotate(
            f'{y[-1]:.1f} {unit}',
            xy=(data['time_min'][-1], y[-1]),
            xytext=(-5, -10), textcoords='offset points',
            fontsize=6.5,
            bbox=dict(boxstyle='round,pad=0.2',
                      facecolor='white', edgecolor='black',
                      linewidth=0.5),
            ha='right'
        )

    # Footer caption — mimics IEEE figure caption style
    cap = (
        f'Topology: {topology_name} | '
        f'Battery: {BatteryConfig.capacity_kwh} kWh @ {BatteryConfig.nominal_voltage} V | '
        f'SoC: {BatteryConfig.initial_soc}%→{data["soc"][-1]:.0f}% | '
        f'Total time: {data["time_min"][-1]:.0f} min'
    )
    fig.text(0.5, -0.02, cap, ha='center', fontsize=6.5, style='italic')

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=300, bbox_inches='tight',
                    facecolor='white')
        print(f'✅ Saved: {save_path}')
    plt.show()


# ============================================================
#  PLOT 2 — HARMONIC ANALYSIS  (IEEE double-column, 3-row)
# ============================================================

def plot_harmonics(data, topology_name, save_path=None):
    """
    IEEE-style harmonic analysis figure.
    Waveform | Spectrum | THD vs SoC | Compliance table | PF vs SoC
    """
    setup_ieee_style()

    fig = plt.figure(figsize=(IEEE_DOUBLE, 7.5))
    gs  = gridspec.GridSpec(3, 2, figure=fig,
                            hspace=0.52, wspace=0.32)
    fig.suptitle(
        f'Harmonic Analysis and IEEE 519 / IS 16528 Compliance '
        f'({GRID_FREQ_HZ} Hz Grid)',
        fontsize=9, fontweight='bold', y=1.01
    )

    # ── 1. Grid current waveform ─────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    t_w, y_pure, y_dist = generate_waveform(topology_name, 'CC')
    _, _, y_dist_cv      = generate_waveform(topology_name, 'CV')

    ax1.plot(t_w / (2 * np.pi), y_pure,
             color='#aaaaaa', linewidth=0.8, linestyle='-',
             label='Ideal sinusoid', zorder=2)
    ax1.plot(t_w / (2 * np.pi), y_dist,
             color='black', linewidth=1.2, linestyle='-',
             label='Distorted — CC/CP mode', zorder=4)
    ax1.plot(t_w / (2 * np.pi), y_dist_cv,
             color='black', linewidth=1.0, linestyle='--',
             label='Distorted — CV mode', zorder=3)

    ax1.axhline(0, color='black', linewidth=0.4, zorder=1)
    ax1.set_xlabel(f'Cycles ({GRID_FREQ_HZ} Hz)', fontsize=8)
    ax1.set_ylabel('Current (p.u.)', fontsize=8)
    ax1.set_title(f'Typical Grid Current Waveform — {topology_name}\n(Harmonic magnitudes scaled ×3. Values are representative literature figures for this topology class.)',
                  fontsize=8, fontweight='bold')
    ax1.legend(loc='upper right', framealpha=1)
    ax1.xaxis.set_minor_locator(AutoMinorLocator())
    ax1.yaxis.set_minor_locator(AutoMinorLocator())

    # ── 2. Harmonic spectrum ─────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    orders_cc, mags_cc = harmonic_spectrum(topology_name, 'CC')
    orders_cv, mags_cv = harmonic_spectrum(topology_name, 'CV')
    x = np.arange(len(orders_cc))
    w = 0.35

    bars1 = ax2.bar(x - w / 2, mags_cc, w,
                    color='#333333', hatch='///',
                    edgecolor='black', linewidth=0.5,
                    label='CC/CP Mode', zorder=3)
    bars2 = ax2.bar(x + w / 2, mags_cv, w,
                    color='#999999', hatch='...',
                    edgecolor='black', linewidth=0.5,
                    label='CV Mode', zorder=3)

    ax2.axhline(4.0, color=LIMIT_COLOR, linestyle='--',
                linewidth=0.9, label='IEEE 519 limit (4%)', zorder=4)
    ax2.set_xticks(x)
    ax2.set_xticklabels([f'$h_{{{o}}}$' for o in orders_cc], fontsize=7)
    ax2.set_xlabel('Harmonic Order', fontsize=8)
    ax2.set_ylabel('Magnitude (% of fundamental)', fontsize=8)
    ax2.set_title('Individual Harmonic Spectrum', fontsize=8, fontweight='bold')
    ax2.legend(loc='upper right', framealpha=1)
    ax2.yaxis.set_minor_locator(AutoMinorLocator())

    # Value labels on CC bars
    for bar in bars1:
        h = bar.get_height()
        if h > 0.4:
            ax2.text(bar.get_x() + bar.get_width() / 2,
                     h + 0.12, f'{h:.1f}',
                     ha='center', fontsize=6, color='black')

    # ── 3. THD vs SoC ────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    thd_profile = compute_thd_profile(data['soc'], data['mode'], topology_name)

    ax3.plot(data['soc'], thd_profile,
             color='black', linewidth=1.2, linestyle='-', zorder=4)
    ax3.axhline(5.0, color=LIMIT_COLOR, linestyle='--',
                linewidth=0.9, label='IEEE 519 THD limit (5%)', zorder=3)

    if trans_idx := next((i for i, m in enumerate(data['mode']) if m == 'CV'), None):
        ax3.axvline(data['soc'][trans_idx], color='black',
                    linestyle=':', linewidth=0.8, alpha=0.7)
        ax3.text(data['soc'][trans_idx] + 1,
                 max(thd_profile) * 0.88,
                 f"{data['mode'][trans_idx - 1]}→CV",
                 fontsize=6.5, style='italic')

    ax3.set_xlabel('State of Charge (%)', fontsize=8)
    ax3.set_ylabel('THD (%)', fontsize=8)
    ax3.set_title('Total Harmonic Distortion vs. SoC', fontsize=8, fontweight='bold')
    ax3.legend(loc='upper right', framealpha=1)
    ax3.xaxis.set_minor_locator(AutoMinorLocator())
    ax3.yaxis.set_minor_locator(AutoMinorLocator())

    # ── 4. Compliance table ──────────────────────────────────
    ax4 = fig.add_subplot(gs[2, 0])
    ax4.axis('off')

    profile = TOPOLOGY_PROFILES[topology_name]
    thd_cc  = profile['thd_cc']
    thd_cv  = profile['thd_cv']
    pf_cc   = power_factor_from_thd(thd_cc)
    pf_cv   = power_factor_from_thd(thd_cv)
    h3, h5, h7 = profile['harmonics'][3], profile['harmonics'][5], profile['harmonics'][7]

    rows = [
        ('THD — CC/CP Mode',     f'{thd_cc:.1f}%',   '≤ 5.0%', thd_cc <= 5.0),
        ('THD — CV Mode',        f'{thd_cv:.1f}%',   '≤ 5.0%', thd_cv <= 5.0),
        ('3rd Harmonic',         f'{h3:.1f}%',        '≤ 4.0%', h3 <= 4.0),
        ('5th Harmonic',         f'{h5:.1f}%',        '≤ 4.0%', h5 <= 4.0),
        ('7th Harmonic',         f'{h7:.1f}%',        '≤ 4.0%', h7 <= 4.0),
        ('Power Factor — CC/CP', f'{pf_cc:.4f}',      '≥ 0.95', pf_cc >= 0.95),
        ('Power Factor — CV',    f'{pf_cv:.4f}',      '≥ 0.95', pf_cv >= 0.95),
    ]
    all_pass = all(r[3] for r in rows)

    # Draw as matplotlib table
    col_labels  = ['Parameter', 'Value', 'Limit', 'Status']
    cell_text   = [[r[0], r[1], r[2], 'Pass' if r[3] else 'Fail'] for r in rows]
    cell_colors = [
        ['white', 'white', 'white',
         '#d4eaf7' if r[3] else '#fde8e8']
        for r in rows
    ]

    tbl = ax4.table(
        cellText=cell_text,
        colLabels=col_labels,
        cellColours=cell_colors,
        cellLoc='center',
        loc='center',
        bbox=[0, 0, 1, 1],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(7)

    # Style header row
    for j in range(len(col_labels)):
        tbl[0, j].set_facecolor('#222222')
        tbl[0, j].set_text_props(color='white', fontweight='bold')

    # Column widths
    tbl.auto_set_column_width([0, 1, 2, 3])

    # Overall compliance title above table
    status_str = 'IEEE 519 / IS 16528: COMPLIANT' if all_pass \
                 else 'IEEE 519 / IS 16528: NON-COMPLIANT'
    ax4.set_title(status_str, fontsize=7.5, fontweight='bold',
                  color=PASS_COLOR if all_pass else FAIL_COLOR, pad=6)

    # ── 5. Power factor vs SoC ───────────────────────────────
    ax5 = fig.add_subplot(gs[2, 1])
    pf_arr = power_factor_from_thd(thd_profile)

    ax5.plot(data['soc'], pf_arr,
             color='black', linewidth=1.2, linestyle='-', zorder=4)
    ax5.axhline(0.95, color=LIMIT_COLOR, linestyle='--',
                linewidth=0.9, label='Min. PF = 0.95', zorder=3)
    ax5.set_xlabel('State of Charge (%)', fontsize=8)
    ax5.set_ylabel('Power Factor', fontsize=8)
    ax5.set_ylim(0.92, 1.01)
    ax5.set_title('Power Factor vs. SoC', fontsize=8, fontweight='bold')
    ax5.legend(loc='lower right', framealpha=1)
    ax5.xaxis.set_minor_locator(AutoMinorLocator())
    ax5.yaxis.set_minor_locator(AutoMinorLocator())

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=300, bbox_inches='tight',
                    facecolor='white')
        print(f'✅ Saved: {save_path}')
    plt.show()


# ============================================================
#  PLOT 3 — TOPOLOGY COMPARISON  (IEEE double-column)
# ============================================================

def plot_topology_comparison(save_path=None):
    """
    IEEE-style converter topology comparison.
    Three subplots: THD | Efficiency | Power Factor.
    Grayscale bars with hatching for B&W print compatibility.
    """
    setup_ieee_style()

    fig, axes = plt.subplots(1, 3, figsize=(IEEE_DOUBLE, 2.8))
    fig.suptitle(
        'Converter Topology Comparison — Literature Survey',
        fontsize=9, fontweight='bold', y=1.03
    )

    names       = list(TOPOLOGY_PROFILES.keys())
    short_names = ['Vienna\n+LLC', 'Vienna\n+LCL', 'AFE\n+DAB', 'Boost\n+FB']
    x           = np.arange(len(names))
    w           = 0.35

    # ── THD ──────────────────────────────────────────────────
    ax1 = axes[0]
    thd_cc = [TOPOLOGY_PROFILES[n]['thd_cc'] for n in names]
    thd_cv = [TOPOLOGY_PROFILES[n]['thd_cv'] for n in names]

    for i, (v_cc, v_cv) in enumerate(zip(thd_cc, thd_cv)):
        ax1.bar(i - w / 2, v_cc, w,
                color=GRAYS[i % 4], hatch=HATCHES[i % 4],
                edgecolor='black', linewidth=0.5, zorder=3)
        ax1.bar(i + w / 2, v_cv, w,
                color=GRAYS[i % 4], alpha=0.45,
                edgecolor='black', linewidth=0.5,
                linestyle='--', zorder=3)

    ax1.axhline(5.0, color=LIMIT_COLOR, linestyle='--',
                linewidth=0.9, label='IEEE 519\nlimit (5%)', zorder=4)
    ax1.set_xticks(x)
    ax1.set_xticklabels(short_names, fontsize=6.5)
    ax1.set_ylabel('THD (%)', fontsize=8)
    ax1.set_title('THD by Topology', fontsize=8, fontweight='bold')
    ax1.legend(fontsize=6.5, loc='upper left')
    ax1.yaxis.set_minor_locator(AutoMinorLocator())

    # Value labels
    for i, v in enumerate(thd_cc):
        ax1.text(i - w / 2, v + 0.15, f'{v:.1f}',
                 ha='center', fontsize=6, color='black')

    # ── Efficiency ────────────────────────────────────────────
    ax2 = axes[1]
    etas = [TOPOLOGY_PROFILES[n]['eta'] * 100 for n in names]

    for i, v in enumerate(etas):
        bar = ax2.bar(i, v, 0.6,
                      color=GRAYS[i % 4], hatch=HATCHES[i % 4],
                      edgecolor='black', linewidth=0.5, zorder=3)
        ax2.text(i, v + 0.08, f'{v:.1f}%',
                 ha='center', fontsize=6.5, fontweight='bold')

    ax2.set_xticks(x)
    ax2.set_xticklabels(short_names, fontsize=6.5)
    ax2.set_ylim(90, 96.5)
    ax2.set_ylabel('Efficiency (%)', fontsize=8)
    ax2.set_title('Charger Efficiency', fontsize=8, fontweight='bold')
    ax2.yaxis.set_minor_locator(AutoMinorLocator())

    # ── Power factor ─────────────────────────────────────────
    ax3 = axes[2]
    pf_cc = [power_factor_from_thd(TOPOLOGY_PROFILES[n]['thd_cc']) for n in names]
    pf_cv = [power_factor_from_thd(TOPOLOGY_PROFILES[n]['thd_cv']) for n in names]

    for i, (v_cc, v_cv) in enumerate(zip(pf_cc, pf_cv)):
        ax3.bar(i - w / 2, v_cc, w,
                color=GRAYS[i % 4], hatch=HATCHES[i % 4],
                edgecolor='black', linewidth=0.5, zorder=3)
        ax3.bar(i + w / 2, v_cv, w,
                color=GRAYS[i % 4], alpha=0.45,
                edgecolor='black', linewidth=0.5,
                linestyle='--', zorder=3)

    ax3.axhline(0.95, color=LIMIT_COLOR, linestyle='--',
                linewidth=0.9, label='Min. PF = 0.95', zorder=4)
    ax3.set_xticks(x)
    ax3.set_xticklabels(short_names, fontsize=6.5)
    ax3.set_ylim(0.92, 1.005)
    ax3.set_ylabel('Power Factor', fontsize=8)
    ax3.set_title('Power Factor', fontsize=8, fontweight='bold')
    ax3.legend(fontsize=6.5, loc='lower right')
    ax3.yaxis.set_minor_locator(AutoMinorLocator())

    # Shared legend for CC/CV bar fill
    cc_patch = mpatches.Patch(facecolor='#555555', edgecolor='black',
                               linewidth=0.5, label='CC/CP Mode')
    cv_patch = mpatches.Patch(facecolor='#aaaaaa', alpha=0.5, edgecolor='black',
                               linewidth=0.5, linestyle='--', label='CV Mode')
    fig.legend(handles=[cc_patch, cv_patch],
               loc='lower center', ncol=2,
               fontsize=7, bbox_to_anchor=(0.5, -0.08),
               framealpha=1, edgecolor='black')

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=300, bbox_inches='tight',
                    facecolor='white')
        print(f'✅ Saved: {save_path}')
    plt.show()
