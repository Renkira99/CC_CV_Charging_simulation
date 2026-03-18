// ============================================================
//  EV DYNAMIC CHARGING SIMULATOR
//  Based on literature review of CC-CV charging characteristics
//  References: IEEE TPEL 2023, IEEE TIE 2025, IEEE TTE 2024
//  Indian Standards: AIS-138, BIS IS 17017, IEC 61000-3-2, CEA Regulations
// ============================================================

const GRID_FREQ_HZ = 50; // India operates at 50 Hz (CEA Regulations)

// ---------- STATE ----------
const state = {
  running: false,
  paused: false,
  speed: 10,          // seconds of sim time per real second
  simTimeSec: 0,      // simulation elapsed seconds
  animFrame: null,
  lastFrameTime: null,

  // Battery
  batteryCapacity: 60,   // kWh
  nominalVoltage: 400,   // V
  initialSoC: 20,        // %
  targetSoC: 100,        // %
  currentSoC: 20,        // %

  // Charger
  maxPower: 7200,        // W
  maxCurrent: 30,        // A
  cableLimit: 30,        // Cable physical current rating (A)
  tempLimit: Infinity,   // Dynamic connector temperature limit
  evseMaxLimit: Infinity,// ISO 15118 EVSE constraint
  maxVoltage: 400,       // V
  ccCvTransition: 80,    // SoC %
  efficiency: 0.96,
  topology: 'llc',

  // Live values
  voltage: 0,
  current: 0,
  power: 0,
  mode: '—',            // CC, CV, DONE
  lastCCCurrent: null,   // Actual current at CC→CV boundary for continuity
  lastCCVoltage: null,   // Actual voltage at CC→CV boundary for ramp

  // Data arrays (for charting)
  dataTime: [],
  dataVoltage: [],
  dataCurrent: [],
  dataPower: [],
  dataSoC: [],
  dataTHD: [],
  dataTHDSoC: []
};

// Charger level presets — Indian Standards (AIS-138 / BIS IS 17017 / MoP Guidelines 2022)
// cableLimit is the physical cable current rating, which may be lower than maxCurrent
const chargerPresets = {
  level1:    { maxPower: 3300,   maxCurrent: 15,  cableLimit: 15,  label: 'Bharat AC-001 (3.3kW)' },   // 230V/15A, Type 2 / IS 60309
  level2:    { maxPower: 7400,   maxCurrent: 32,  cableLimit: 32,  label: 'AC Level 2 1-φ (7.4kW)' },  // 230V/32A, single-phase
  level2max: { maxPower: 22000,  maxCurrent: 32,  cableLimit: 32,  label: 'AC Level 2 3-φ (22kW)' },   // 415V/32A, three-phase
  dcfast50:  { maxPower: 15000,  maxCurrent: 200, cableLimit: 200, label: 'Bharat DC-001 (15kW)' },    // CCS2/CHAdeMO
  dcfast150: { maxPower: 60000,  maxCurrent: 250, cableLimit: 200, label: 'DC Fast (60kW)' },           // cable limit ~150-200A
  dcfast350: { maxPower: 150000, maxCurrent: 375, cableLimit: 350, label: 'DC Ultra-Fast (150kW)' }     // cable limit ~350A
};

// Topology efficiency (η_PFC * η_DCDC) + input AC harmonic profile
// Note: Triplen harmonics (3, 9, 15) are 0 in balanced 3-phase topologies
// cv_thd_stable: active PFC holds THD at light load; passive front end rises in CV tail
const topologyProfiles = {
  viennallc: { eta: 0.951, thd_cc: 4.2, thd_cv: 2.1, cv_thd_stable: true,  harmonics: [100, 0.0, 3.2, 2.1, 0.0, 1.4, 0.9, 0.0, 0.6, 0.5] },
  viennalcl: { eta: 0.938, thd_cc: 5.1, thd_cv: 2.8, cv_thd_stable: true,  harmonics: [100, 0.0, 4.0, 2.8, 0.0, 1.8, 1.1, 0.0, 0.7, 0.6] },
  afedab:    { eta: 0.94,  thd_cc: 3.8, thd_cv: 1.9, cv_thd_stable: true,  harmonics: [100, 0.0, 2.8, 1.9, 0.0, 1.2, 0.7, 0.0, 0.4, 0.3] },
  boostfb:   { eta: 0.912, thd_cc: 7.2, thd_cv: 4.0, cv_thd_stable: false, harmonics: [100, 5.5, 3.8, 2.5, 1.5, 1.0, 0.8, 0.5, 0.4, 0.3] }
};


// ---------- DOM REFS ----------
const $ = id => document.getElementById(id);

function setupSlider(id) {
  const el = $(id);
  if (!el) return;
  el.addEventListener('input', () => {
    const valEl = $(id + 'Val');
    if (valEl) valEl.textContent = el.value;
  });
}

// ---------- INIT ----------
function init() {
  // Bind sliders
  ['batteryCapacity', 'nominalVoltage', 'initialSoC', 'targetSoC',
    'ccCvTransition', 'chargerEfficiency'].forEach(setupSlider);

  // Nav scroll effect + active tracking
  window.addEventListener('scroll', () => {
    const nav = $('topnav');
    if (nav) nav.classList.toggle('scrolled', window.scrollY > 50);

    const sections = document.querySelectorAll('.section, .hero');
    const pills = document.querySelectorAll('.nav-pill');
    let current = '';
    sections.forEach(s => {
      if (window.scrollY >= s.offsetTop - 200) current = s.getAttribute('id');
    });
    pills.forEach(p => {
      p.classList.remove('active');
      if (p.getAttribute('href') === '#' + current) p.classList.add('active');
    });
  });

  // Initial chart draw
  drawAllCharts();
}

document.addEventListener('DOMContentLoaded', init);


// ---------- READ PARAMETERS ----------
function readParams() {
  state.batteryCapacity = +$('batteryCapacity').value;
  state.nominalVoltage = +$('nominalVoltage').value;
  state.initialSoC = +$('initialSoC').value;
  state.targetSoC = +$('targetSoC').value;
  state.ccCvTransition = +$('ccCvTransition').value;
  state.efficiency = +$('chargerEfficiency').value / 100;
  state.topology = $('converterTopology').value;

  const level = $('chargingLevel').value;
  const preset = chargerPresets[level];
  state.maxPower = preset.maxPower;
  state.maxCurrent = preset.maxCurrent;
  state.cableLimit = preset.cableLimit || preset.maxCurrent;
  state.maxVoltage = state.nominalVoltage * 1.05; // ~420V at full charge for 400V architecture
}


// ---------- BATTERY MODEL ----------
// NMC/NCM pack OCV lookup — 13-point curve anchored to [0.82, 1.05]×V_nom.
// Flat plateau at 20–70% SoC captures the Li-ion characteristic that a polynomial misses.
const OCV_SOC_PTS  = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100];
const OCV_NORM_PTS = [0.820, 0.838, 0.855, 0.878, 0.895, 0.908, 0.920, 0.933, 0.950, 0.970, 1.000, 1.028, 1.050];

function linearInterp(xPts, yPts, x) {
  if (x <= xPts[0]) return yPts[0];
  if (x >= xPts[xPts.length - 1]) return yPts[yPts.length - 1];
  for (let i = 1; i < xPts.length; i++) {
    if (x <= xPts[i]) {
      const t = (x - xPts[i - 1]) / (xPts[i] - xPts[i - 1]);
      return yPts[i - 1] + t * (yPts[i] - yPts[i - 1]);
    }
  }
}

function batteryVoltage(soc) {
  // OCV via piecewise interpolation of NMC cell curve, scaled to pack voltage
  const vRef = linearInterp(OCV_SOC_PTS, OCV_NORM_PTS, soc);
  return Math.min(state.nominalVoltage * vRef, state.nominalVoltage * 1.05);
}

// Internal resistance — U-curve: higher at both extremes, minimum near 50% SoC
// Formula: r_base × (1 + 2(0.5−s)²) → 1.5× at SoC=0%/100%, 1.0× at SoC=50%
function internalResistance(soc) {
  const s = soc / 100;
  const rBase = state.nominalVoltage * 0.0001; // ~0.04 Ω at 400V
  return rBase * (1.0 + 2.0 * (0.5 - s) * (0.5 - s));
}

// BMS current derating vs SoC — real Li-ion packs reduce acceptance rate near full charge
// Typical derating steps: full rate <70%, 80%/50%/20% at 70/85/95% SoC
function bmsCurrentLimit(soc, ahCapacity, maxCRate = 2.0) {
  const iMax = ahCapacity * maxCRate;
  if (soc < 70)  return iMax;
  if (soc < 85)  return iMax * 0.8;
  if (soc < 95)  return iMax * 0.5;
  return iMax * 0.2;
}


// ---------- CC-CV CHARGING PHYSICS ----------
function chargingStep(dt) {
  // dt in seconds
  const soc = state.currentSoC;
  if (soc >= state.targetSoC) {
    state.mode = 'DONE';
    state.voltage = batteryVoltage(soc);
    state.current = 0;
    state.power = 0;
    return;
  }

  const vOcv = batteryVoltage(soc);
  const rInt = internalResistance(soc);
  const vMax = state.nominalVoltage * 1.05; // ~420V at full charge for 400V architecture

  // Energy capacity in Wh; ahCapacity shared between CC and CV branches
  const capacityWh = state.batteryCapacity * 1000;
  const ahCapacity = capacityWh / state.nominalVoltage;

  if (soc < state.ccCvTransition) {
    // === CP or CC MODE ===

    // Power limit: quadratic solve for I given P_max = (V_ocv + I*R_int)*I
    const iPowerLimit = (-vOcv + Math.sqrt(vOcv ** 2 + 4 * rInt * state.maxPower)) / (2 * rInt);
    // BMS derates current acceptance at high SoC to protect cells
    const iBMS = bmsCurrentLimit(soc, ahCapacity);
    
    // Additional EVSE and Thermal limit constraints (DIN 70121 / ISO 15118)
    const iTemp = state.tempLimit !== undefined ? state.tempLimit : Infinity;
    const iEvse = state.evseMaxLimit !== undefined ? state.evseMaxLimit : Infinity;
    
    // The actual current delivered is the minimum of all constraints
    const iCC = Math.min(iPowerLimit, iBMS, state.cableLimit, state.maxCurrent, iTemp, iEvse);

    // CP = power is genuinely binding when it's measurably below all hardware limits.
    // Level 2 AC: current limit always binds first — CP essentially never fires.
    // DC fast: power often limits before cable/BMS — CP is the realistic label here.
    const otherLimits = Math.min(iBMS, state.cableLimit, state.maxCurrent, iTemp, iEvse);
    if (iPowerLimit < otherLimits - 1.0) {
      state.mode = 'CP';
    } else {
      state.mode = 'CC';
    }

    state.current = iCC;
    state.voltage = Math.min(vOcv + iCC * rInt, vMax); // cap at battery vMax
    state.power = state.voltage * state.current;
    state.lastCCCurrent = iCC; // store for CV entry continuity
    state.lastCCVoltage = state.voltage; // store for CV voltage ramp
    state.currentSoC += ((state.current * dt) / 3600) / ahCapacity * 100;

  } else {
    // === CV MODE ===
    state.mode = 'CV';

    const progress = (soc - state.ccCvTransition) / Math.max(state.targetSoC - state.ccCvTransition, 1);
    
    // Ramp v_term from last CC terminal voltage → v_max over the first 10% of CV progress.
    // This eliminates the one-timestep power spike caused by jumping straight to vMax
    // while current is still at lastCCCurrent.
    const iEntryVoltage = state.lastCCVoltage !== null ? state.lastCCVoltage : vMax;
    state.voltage = Math.min(iEntryVoltage + (vMax - iEntryVoltage) * Math.min(progress * 10, 1.0), vMax);

    // Start exponential from actual last CC current, not maxCurrent, to avoid entry discontinuity
    const iEntry = state.lastCCCurrent !== null ? state.lastCCCurrent : state.maxCurrent;
    
    // CV exponent: 3.5 is appropriate for fast chargers. Scale down for slow chargers.
    const cRate = state.maxCurrent / ahCapacity;
    const cvExponent = Math.max(1.5, Math.min(1.5 + 2.0 * cRate, 3.5)); // clip to [1.5, 3.5]
    
    let iCV = iEntry * Math.exp(-cvExponent * progress);

    // Max current battery can physically absorb at vMax given current OCV and R_int
    const iPhysical = Math.max((vMax - vOcv) / Math.max(rInt, 0.001), 0);
    iCV = Math.min(iCV, iPhysical, state.maxCurrent);

    // Cutoff current (standard C/20 but capped at 5% of charger max to ensure CV tail runs on slow chargers)
    const cutoffCurrent = Math.min(ahCapacity / 20, state.maxCurrent * 0.05);
    
    if (iCV < cutoffCurrent) {
      state.mode = 'DONE';
      state.current = 0;
      state.power = 0;
      return;
    }

    state.current = iCV;
    state.power = state.voltage * state.current;
    state.currentSoC += ((state.current * dt) / 3600) / ahCapacity * 100;
  }

  state.currentSoC = Math.min(state.currentSoC, state.targetSoC);
}


// ---------- HARMONIC MODEL ----------
function computeHarmonics() {
  const profile = topologyProfiles[state.topology];
  const soc = state.currentSoC;
  const mode = state.mode;

  // THD varies with operating mode
  let thdBase;
  if (mode === 'CC' || mode === 'CP') {
    thdBase = profile.thd_cc;
  } else if (mode === 'CV') {
    if (profile.cv_thd_stable) {
      // Active PFC control loop holds THD at its CV-mode value regardless of load level
      thdBase = profile.thd_cv;
    } else {
      // Passive front end (Diode Bridge + Boost PFC): THD rises as current tapers in CV tail
      const progress = (soc - state.ccCvTransition) / Math.max(state.targetSoC - state.ccCvTransition, 1);
      thdBase = profile.thd_cv * (1 + 1.5 * Math.max(0, Math.min(1, progress)));
    }
  } else {
    thdBase = 0;
  }

  // Scale harmonics
  const harmonics = profile.harmonics.map((h, i) => {
    if (i === 0) return 100; // fundamental
    const scale = (mode === 'CC' || mode === 'CP') ? 1.0 : 0.6;
    return h * scale * (thdBase / profile.thd_cc);
  });

  return { thd: thdBase, harmonics };
}

// Power factor from THD
function powerFactor(thd) {
  // PF ≈ 1 / sqrt(1 + (THD/100)^2) × displacement PF
  const dpf = 0.99; // displacement PF (close to 1 for modern converters)
  return dpf / Math.sqrt(1 + (thd / 100) ** 2);
}


// ---------- SIMULATION LOOP ----------
function startSimulation() {
  if (state.running && !state.paused) return;

  if (!state.paused) {
    // Fresh start
    readParams();
    state.currentSoC = state.initialSoC;
    state.simTimeSec = 0;
    state.dataTime = [];
    state.dataVoltage = [];
    state.dataCurrent = [];
    state.dataPower = [];
    state.dataSoC = [];
    state.dataTHD = [];
    state.dataTHDSoC = [];
  }

  state.running = true;
  state.paused = false;
  state.lastFrameTime = performance.now();

  $('btnStart').disabled = true;
  $('btnPause').disabled = false;
  updateStatusUI('running');

  state.animFrame = requestAnimationFrame(simLoop);
}

function pauseSimulation() {
  state.paused = true;
  state.running = false;
  cancelAnimationFrame(state.animFrame);
  $('btnStart').disabled = false;
  $('btnPause').disabled = true;
  $('btnStart').innerHTML = '<span>▶</span> Resume';
  updateStatusUI('paused');
}

function resetSimulation() {
  state.running = false;
  state.paused = false;
  cancelAnimationFrame(state.animFrame);

  state.currentSoC = state.initialSoC;
  state.simTimeSec = 0;
  state.voltage = 0;
  state.current = 0;
  state.power = 0;
  state.mode = '—';
  state.lastCCCurrent = null;
  state.lastCCVoltage = null;
  state.dataTime = [];
  state.dataVoltage = [];
  state.dataCurrent = [];
  state.dataPower = [];
  state.dataSoC = [];
  state.dataTHD = [];
  state.dataTHDSoC = [];

  $('btnStart').disabled = false;
  $('btnPause').disabled = true;
  $('btnStart').innerHTML = '<span>▶</span> Start';
  updateStatusUI('ready');
  updateHeroStats();
  updateLiveValues();
  drawAllCharts();
  updateGauges();
  updateComplianceUI({ thd: 0, harmonics: [100, 0, 0, 0, 0, 0, 0, 0, 0, 0] });
}

let frameCounter = 0;

function simLoop(timestamp) {
  if (!state.running) return;

  const realDt = (timestamp - state.lastFrameTime) / 1000; // real seconds
  state.lastFrameTime = timestamp;

  // Simulation time step
  const simDt = realDt * state.speed;
  state.simTimeSec += simDt;

  // Physics step (sub-steps for accuracy at high speed)
  const subSteps = Math.max(1, Math.ceil(simDt / 10));
  const subDt = simDt / subSteps;
  for (let i = 0; i < subSteps; i++) {
    chargingStep(subDt);
    if (state.mode === 'DONE') break;
  }

  // Record data every ~5 sim-seconds
  frameCounter++;
  if (frameCounter % 3 === 0 || state.mode === 'DONE') {
    const tMin = state.simTimeSec / 60;
    state.dataTime.push(tMin);
    state.dataVoltage.push(state.voltage);
    state.dataCurrent.push(state.current);
    state.dataPower.push(state.power / 1000);
    state.dataSoC.push(state.currentSoC);

    const harm = computeHarmonics();
    state.dataTHD.push(harm.thd);
    state.dataTHDSoC.push({ soc: state.currentSoC, thd: harm.thd });
  }

  // Update UI
  updateHeroStats();
  updateLiveValues();
  updateGauges();

  // Redraw charts ~10fps
  if (frameCounter % 6 === 0 || state.mode === 'DONE') {
    drawAllCharts();
    const harm = computeHarmonics();
    updateComplianceUI(harm);
  }

  if (state.mode === 'DONE') {
    state.running = false;
    $('btnStart').disabled = true;
    $('btnPause').disabled = true;
    updateStatusUI('complete');
    drawAllCharts();
    return;
  }

  state.animFrame = requestAnimationFrame(simLoop);
}

function setSpeed(s) {
  state.speed = s;
  document.querySelectorAll('.speed-btn').forEach(b => {
    b.classList.toggle('active', b.textContent.trim() === s + '×');
  });
}


// ---------- UI UPDATES ----------
function updateStatusUI(status) {
  const dot = document.querySelector('.status-dot');
  const text = document.querySelector('.status-text');
  dot.className = 'status-dot';
  if (status === 'running') { dot.classList.add('running'); text.textContent = 'Running'; }
  if (status === 'paused') { dot.classList.add('paused'); text.textContent = 'Paused'; }
  if (status === 'complete') { dot.classList.add('complete'); text.textContent = 'Complete'; }
  if (status === 'ready') { text.textContent = 'Ready'; }
}

function updateHeroStats() {
  $('heroVoltage').textContent = state.voltage.toFixed(0) + 'V';
  $('heroCurrent').textContent = state.current.toFixed(1) + 'A';
  $('heroPower').textContent = (state.power / 1000).toFixed(1) + 'kW';
  $('heroSoC').textContent = state.currentSoC.toFixed(0) + '%';
}

function updateLiveValues() {
  // Time display
  const h = Math.floor(state.simTimeSec / 3600);
  const m = Math.floor((state.simTimeSec % 3600) / 60);
  const s = Math.floor(state.simTimeSec % 60);
  $('simTime').textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

  // Mode badge
  const badge = $('modeBadge');
  badge.className = 'mode-badge';
  if (state.mode === 'CP') { badge.classList.add('cc'); badge.textContent = 'CP Mode'; } // Re-use CC style for CP
  else if (state.mode === 'CC') { badge.classList.add('cc'); badge.textContent = 'CC Mode'; }
  else if (state.mode === 'CV') { badge.classList.add('cv'); badge.textContent = 'CV Mode'; }
  else if (state.mode === 'DONE') { badge.classList.add('done'); badge.textContent = 'Complete'; }
  else { badge.textContent = '—'; }

  // Chart live values
  $('voltLive').textContent = state.voltage.toFixed(1) + ' V';
  $('currLive').textContent = state.current.toFixed(2) + ' A';
  $('powLive').textContent = (state.power / 1000).toFixed(2) + ' kW';
  $('socLive').textContent = state.currentSoC.toFixed(1) + ' %';

  const harm = computeHarmonics();
  $('thdLive').textContent = 'THD: ' + harm.thd.toFixed(1) + ' %';
}

function updateGauges() {
  const circumference = 2 * Math.PI * 50; // r=50 from SVG

  // SoC gauge
  const socPct = state.currentSoC / 100;
  const socOffset = circumference * (1 - socPct);
  $('socGauge').style.strokeDashoffset = socOffset;
  $('gaugeSoC').textContent = state.currentSoC.toFixed(0) + '%';

  // Power gauge
  const pPct = Math.min(state.power / state.maxPower, 1);
  const pOffset = circumference * (1 - pPct);
  $('powerGauge').style.strokeDashoffset = pOffset;
  $('gaugePower').textContent = (state.power / 1000).toFixed(1) + 'kW';
}

function updateComplianceUI(harm) {
  const thd = harm.thd;
  const h3 = harm.harmonics[1] || 0;
  const h5 = harm.harmonics[2] || 0;
  const h7 = harm.harmonics[3] || 0;
  const pf = powerFactor(thd);

  $('compTHD').textContent = thd.toFixed(2) + '%';
  $('comp3rd').textContent = h3.toFixed(2) + '%';
  $('comp5th').textContent = h5.toFixed(2) + '%';
  $('comp7th').textContent = h7.toFixed(2) + '%';
  $('compPF').textContent = pf.toFixed(4);

  // Bars
  setCompBar('compTHDFill', thd, 5);
  setCompBar('comp3rdFill', h3, 4);
  setCompBar('comp5thFill', h5, 4);
  setCompBar('comp7thFill', h7, 4);

  // PF bar (inverted — higher is better)
  const pfEl = $('compPFFill');
  pfEl.style.width = (pf * 100) + '%';
  pfEl.className = 'comp-fill pf-fill';
  if (pf < 0.90) pfEl.classList.add('fail');
  else if (pf < 0.95) pfEl.classList.add('warning');

  // Overall compliance
  const badge = $('complianceBadge');
  if (state.mode === '—') {
    badge.textContent = '⏳ Waiting';
    badge.className = 'compliance-badge';
  } else if (thd <= 5 && h3 <= 4 && h5 <= 4 && h7 <= 4 && pf >= 0.95) {
    badge.textContent = '✅ Compliant';
    badge.className = 'compliance-badge pass';
  } else {
    badge.textContent = '❌ Non-Compliant';
    badge.className = 'compliance-badge fail';
  }
}

function setCompBar(id, value, limit) {
  const el = $(id);
  const pct = Math.min((value / limit) * 100, 120);
  el.style.width = pct + '%';
  el.className = 'comp-fill';
  if (value > limit) el.classList.add('fail');
  else if (value > limit * 0.8) el.classList.add('warning');
}


// ============================================================
//  CANVAS CHARTING ENGINE (Pure JS, no libraries)
// ============================================================

function drawChart(canvasId, data, options) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;

  // Size canvas to CSS size
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const pad = { top: 30, right: 20, bottom: 40, left: 60 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // Clear
  ctx.clearRect(0, 0, W, H);

  if (!data || data.length === 0) {
    // Empty state placeholder
    ctx.fillStyle = '#3a3a6a';
    ctx.font = '500 13px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Start simulation to see data', W / 2, H / 2);
    return;
  }

  // Compute bounds
  let xMin = options.xData ? Math.min(...options.xData) : 0;
  let xMax = options.xData ? Math.max(...options.xData) : data.length - 1;
  let yMin = options.yMin !== undefined ? options.yMin : Math.min(...data);
  let yMax = options.yMax !== undefined ? options.yMax : Math.max(...data);

  if (xMax === xMin) xMax = xMin + 1;
  if (yMax === yMin) yMax = yMin + 1;

  // Add padding to y range
  const yRange = yMax - yMin;
  yMin -= yRange * 0.05;
  yMax += yRange * 0.05;

  function mapX(val) {
    return pad.left + ((val - xMin) / (xMax - xMin)) * plotW;
  }
  function mapY(val) {
    return pad.top + plotH - ((val - yMin) / (yMax - yMin)) * plotH;
  }

  // Grid lines
  ctx.strokeStyle = 'rgba(42, 42, 69, 0.6)';
  ctx.lineWidth = 1;
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const yVal = yMin + (yMax - yMin) * (i / yTicks);
    const y = mapY(yVal);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(W - pad.right, y);
    ctx.stroke();

    // Y label
    ctx.fillStyle = '#6a6a8a';
    ctx.font = '500 10px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(yVal.toFixed(options.yDecimals || 0), pad.left - 8, y + 3);
  }

  // X axis labels
  const xTicks = 6;
  for (let i = 0; i <= xTicks; i++) {
    const xVal = xMin + (xMax - xMin) * (i / xTicks);
    const x = mapX(xVal);
    ctx.fillStyle = '#6a6a8a';
    ctx.font = '500 10px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(xVal.toFixed(0), x, H - pad.bottom + 20);
  }

  // Axis labels
  ctx.fillStyle = '#6a6a8a';
  ctx.font = '600 11px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(options.xLabel || '', W / 2, H - 4);
  ctx.save();
  ctx.translate(14, pad.top + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(options.yLabel || '', 0, 0);
  ctx.restore();

  // CC-CV transition line
  if (options.showTransition && state.ccCvTransition && state.dataTime.length > 0) {
    // Find the time when SoC crosses ccCvTransition
    let transTime = null;
    for (let i = 1; i < state.dataSoC.length; i++) {
      if (state.dataSoC[i - 1] < state.ccCvTransition && state.dataSoC[i] >= state.ccCvTransition) {
        transTime = state.dataTime[i];
        break;
      }
    }
    if (transTime !== null) {
      const x = mapX(transTime);
      ctx.strokeStyle = 'rgba(255, 200, 87, 0.5)';
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(x, pad.top);
      ctx.lineTo(x, pad.top + plotH);
      ctx.stroke();
      ctx.setLineDash([]);

      // Label
      ctx.fillStyle = 'rgba(255, 200, 87, 0.8)';
      ctx.font = '600 9px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('CC → CV', x, pad.top - 8);
    }
  }

  // Line only, no heavy gradients
  ctx.beginPath();
  const xData = options.xData || data.map((_, i) => i);
  ctx.moveTo(mapX(xData[0]), mapY(data[0]));
  for (let i = 1; i < data.length; i++) {
    ctx.lineTo(mapX(xData[i]), mapY(data[i]));
  }
  const color = options.color || '#0d6efd';

  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // Current value dot
  if (data.length > 0) {
    const lastX = mapX(xData[xData.length - 1]);
    const lastY = mapY(data[data.length - 1]);
    ctx.beginPath();
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }
}


function drawBarChart(canvasId, labels, values, options) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const pad = { top: 30, right: 20, bottom: 50, left: 55 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  ctx.clearRect(0, 0, W, H);

  if (!values || values.length === 0) {
    ctx.fillStyle = '#3a3a6a';
    ctx.font = '500 13px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Start simulation to see data', W / 2, H / 2);
    return;
  }

  const yMax = options.yMax || Math.max(...values) * 1.2;
  const barW = plotW / values.length * 0.65;
  const gap = plotW / values.length * 0.35;

  // Grid
  ctx.strokeStyle = 'rgba(42, 42, 69, 0.6)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + plotH - (plotH * i / 4);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(W - pad.right, y);
    ctx.stroke();

    ctx.fillStyle = '#6a6a8a';
    ctx.font = '500 10px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText((yMax * i / 4).toFixed(1), pad.left - 8, y + 3);
  }

  // Limit line
  if (options.limitLine) {
    const y = pad.top + plotH - (plotH * options.limitLine / yMax);
    ctx.strokeStyle = 'rgba(255, 82, 82, 0.6)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(W - pad.right, y);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = 'rgba(255, 82, 82, 0.8)';
    ctx.font = '600 9px Inter, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('IEC 61000-3-2 Limit', W - pad.right - 80, y - 6);
  }

  // Bars
  const colors = options.colors || ['#7c6cf0', '#00d4c8', '#ff6b9d', '#ffc857', '#4ae0a0',
    '#7c6cf0', '#00d4c8', '#ff6b9d', '#ffc857'];

  values.forEach((val, i) => {
    const x = pad.left + (plotW / values.length) * i + gap / 2;
    const barH = (val / yMax) * plotH;
    const y = pad.top + plotH - barH;

    const c = colors[i % colors.length];
    ctx.fillStyle = c;

    // Rounded rect
    const r = 4;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + barW - r, y);
    ctx.quadraticCurveTo(x + barW, y, x + barW, y + r);
    ctx.lineTo(x + barW, pad.top + plotH);
    ctx.lineTo(x, pad.top + plotH);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
    ctx.fill();



    // Value on top
    ctx.fillStyle = '#e8e8f4';
    ctx.font = '600 9px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(val.toFixed(1) + '%', x + barW / 2, y - 8);

    // Label
    ctx.fillStyle = '#6a6a8a';
    ctx.font = '500 9px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.save();
    ctx.translate(x + barW / 2, H - pad.bottom + 15);
    ctx.fillText(labels[i], 0, 0);
    ctx.restore();
  });
}


function drawWaveform(canvasId) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const pad = { top: 20, right: 20, bottom: 30, left: 50 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const midY = pad.top + plotH / 2;

  ctx.clearRect(0, 0, W, H);

  if (state.mode === '—' || state.mode === 'DONE') {
    ctx.fillStyle = '#3a3a6a';
    ctx.font = '500 13px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(state.mode === 'DONE' ? 'Charging complete' : 'Start simulation to see waveform', W / 2, H / 2);
    return;
  }

  // Zero line
  ctx.strokeStyle = 'rgba(42, 42, 69, 0.6)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, midY);
  ctx.lineTo(W - pad.right, midY);
  ctx.stroke();

  // Generate waveform: fundamental + harmonics
  const harm = computeHarmonics();
  const fundamentalAmp = plotH / 2 * 0.7;
  const numCycles = 3;
  const points = 600;

  // Pure sine (reference)
  ctx.beginPath();
  ctx.strokeStyle = 'rgba(106, 106, 138, 0.3)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= points; i++) {
    const x = pad.left + (i / points) * plotW;
    const t = (i / points) * numCycles * 2 * Math.PI;
    const y = midY - fundamentalAmp * Math.sin(t);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Distorted waveform
  ctx.beginPath();
  const harmonicOrders = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19];
  for (let i = 0; i <= points; i++) {
    const x = pad.left + (i / points) * plotW;
    const t = (i / points) * numCycles * 2 * Math.PI;

    let val = 0;
    harmonicOrders.forEach((order, hi) => {
      const amp = (harm.harmonics[hi] || 0) / 100;
      const phase = hi * 0.3; // slight phase shift
      val += amp * Math.sin(order * t + phase);
    });

    const y = midY - fundamentalAmp * val;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }

  const waveColor = (state.mode === 'CC' || state.mode === 'CP') ? '#0d6efd' : '#198754';
  ctx.strokeStyle = waveColor;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // Labels
  ctx.fillStyle = '#6a6a8a';
  ctx.font = '600 10px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(`Time (cycles at ${GRID_FREQ_HZ} Hz — Indian Grid)`, W / 2, H - 4);

  // Mode label
  ctx.fillStyle = waveColor;
  ctx.font = '700 11px Inter, sans-serif';
  ctx.textAlign = 'right';
  ctx.fillText((state.mode === 'DONE' ? 'DONE' : state.mode + ' Mode') + ' — THD: ' + harm.thd.toFixed(1) + '%', W - pad.right, pad.top + 14);
}


function drawAllCharts() {
  // Voltage
  drawChart('voltageChart', state.dataVoltage, {
    xData: state.dataTime,
    xLabel: 'Time (min)',
    yLabel: 'Voltage (V)',
    color: '#ff6b9d',
    showTransition: true,
    yDecimals: 0
  });

  // Current
  drawChart('currentChart', state.dataCurrent, {
    xData: state.dataTime,
    xLabel: 'Time (min)',
    yLabel: 'Current (A)',
    color: '#6b9dff',
    showTransition: true,
    yDecimals: 1,
    yMin: 0
  });

  // Power
  drawChart('powerChart', state.dataPower, {
    xData: state.dataTime,
    xLabel: 'Time (min)',
    yLabel: 'Power (kW)',
    color: '#4ae0a0',
    showTransition: true,
    yDecimals: 1,
    yMin: 0
  });

  // SoC
  drawChart('socChart', state.dataSoC, {
    xData: state.dataTime,
    xLabel: 'Time (min)',
    yLabel: 'SoC (%)',
    color: '#ffc857',
    showTransition: true,
    yMin: 0,
    yMax: 105,
    yDecimals: 0
  });

  // Waveform
  drawWaveform('waveformChart');

  // Spectrum bar chart
  const harm = computeHarmonics();
  const harmLabels = ['1st', '3rd', '5th', '7th', '9th', '11th', '13th', '15th', '17th', '19th'];
  const harmValues = harm.harmonics.map((h, i) => i === 0 ? 0 : h); // skip fundamental for bar chart
  drawBarChart('spectrumChart', harmLabels.slice(1), harmValues.slice(1), {
    yMax: 8,
    limitLine: 4,
    colors: ['#ff6b9d', '#ffc857', '#7c6cf0', '#00d4c8', '#4ae0a0', '#ff6b9d', '#ffc857', '#7c6cf0', '#00d4c8']
  });

  // THD vs SoC
  if (state.dataTHDSoC.length > 0) {
    const thdVals = state.dataTHDSoC.map(d => d.thd);
    const socVals = state.dataTHDSoC.map(d => d.soc);
    drawChart('thdSocChart', thdVals, {
      xData: socVals,
      xLabel: 'SoC (%)',
      yLabel: 'THD (%)',
      color: '#ff6b9d',
      yMin: 0,
      yDecimals: 1
    });
    $('thdSocLive').textContent = 'THD: ' + (thdVals[thdVals.length - 1] || 0).toFixed(1) + '%';
    $('specLive').textContent = state.mode;
  }
}


// ---------- WINDOW RESIZE ----------
window.addEventListener('resize', () => {
  if (state.dataTime.length > 0) {
    drawAllCharts();
  }
});
