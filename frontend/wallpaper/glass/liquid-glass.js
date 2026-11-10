import {MATERIAL_DEFAULTS,MATERIAL_LIMITS} from './material.js';
import {GEOMETRY_DEFAULTS,GEOMETRY_LIMITS,cardKind,readCardGeometry,coverDrawRect} from './card-geometry.js';
import {NAV_DEFAULTS,NAV_LIMITS,initNavigation} from './navigation.js';
import {loadStudioSource,createStudioPass} from './studio-optics.js';

// Entry point and DOM lifecycle. See AGENTS.md for the integration contract.
const REDUCED_MOTION = matchMedia('(prefers-reduced-motion: reduce)').matches;
const REDUCED_TRANSPARENCY = matchMedia('(prefers-reduced-transparency: reduce)');
const MORE_CONTRAST = matchMedia('(prefers-contrast: more)');
const glassAllowed = () => !REDUCED_TRANSPARENCY.matches && !MORE_CONTRAST.matches;

export const GLASS_LIMITS = {...MATERIAL_LIMITS,...GEOMETRY_LIMITS,...NAV_LIMITS};
export const GLASS_DEFAULTS = {
  material:structuredClone(MATERIAL_DEFAULTS),
  geometry:structuredClone(GEOMETRY_DEFAULTS),
  nav:structuredClone(NAV_DEFAULTS),
};
let config = structuredClone(GLASS_DEFAULTS);
export const getGlassConfig = () => structuredClone(config);

function updateGroup(target, patch) {
  if (!patch || typeof patch !== 'object') return;
  for (const key of Object.keys(target)) {
    const value = patch[key];
    if (key === 'tint') {
      if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) target[key] = value;
    } else if (typeof target[key] === 'boolean') {
      if (typeof value === 'boolean') target[key] = value;
    } else if (typeof value === 'number' && Number.isFinite(value)) {
      const [min,max] = GLASS_LIMITS[key];
      target[key] = Math.min(max,Math.max(min,value));
    }
  }
}

export function setGlassConfig(patch) {
  if (!patch || typeof patch !== 'object') return getGlassConfig();
  updateGroup(config.material,patch.material);
  for (const name of Object.keys(config.geometry)) updateGroup(config.geometry[name],patch.geometry?.[name]);
  updateGroup(config.nav,patch.nav);
  applyConfigStyles();
  dispatchEvent(new Event('glass-config-change'));
  return getGlassConfig();
}

function applyConfigStyles() {
  const c = config.material;
  document.querySelectorAll('.card').forEach(card => {
    card.style.borderRadius = `${config.geometry[cardKind(card)].radius}px`;
    card.style.setProperty('--glass-tint-color',c.tint.match(/[0-9a-f]{2}/gi).map(v=>parseInt(v,16)).join(' '));
    // Match upstream tint.a * 0.8 for CSS fallback without changing the saved material.
    card.style.setProperty('--glass-tint-alpha',c.tintAlpha*.8);
    card.style.setProperty('--glass-blur',`${c.blurRadius}px`);
  });
  const nav = document.querySelector('.nav');
  if (nav) for (const [key,value] of Object.entries(config.nav)) nav.style.setProperty(`--${key}`,value);
}

const renderMetrics = {frames:0,studioTextureBuilds:0,error:''};
export function getGlassMetrics() {
  return {engine:'studio',material:{...config.material},...renderMetrics,
    cards:[...document.querySelectorAll('.card')].map(card => {
      const {kind,radius} = readCardGeometry(card,config.geometry);
      return {kind,radius,gpu:card.classList.contains('glass-gpu')};
    }),nav:document.querySelector('.nav')?.classList.contains('nav--refracting') || false};
}
try { setGlassConfig(JSON.parse(document.querySelector('#glass-config')?.textContent || '{}')); }
catch { console.warn('Invalid glass-config JSON; using the saved material defaults.'); }

// Diagnostics use the actual production texture or native backdrop.
const DEBUG_VIEW = new URLSearchParams(location.search).get('glassDebug');
const GLASS_DEBUG = ({source:1,coordinates:1,displacement:2,refractionOnly:3})[DEBUG_VIEW] || 0;
if (GLASS_DEBUG) document.documentElement.dataset.glassDebug = DEBUG_VIEW;

async function initCardGlass() {
  const layer = document.querySelector('#glassLayer');
  const backdrop = document.querySelector('.site-backdrop');
  if (!layer || !backdrop) return;

  const img = new Image();
  const background=getComputedStyle(backdrop).backgroundImage.match(/url\(["']?(.*?)["']?\)/);
  img.src=backdrop.dataset.glassSource || background?.[1] || new URL('./assets/native-wallpapers/tahoe-light.jpg',import.meta.url).href;
  try {
    await img.decode();
  } catch {
    return;
  }

  const gl = layer.getContext('webgl2', {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: 'default',
  });
  if (!gl) return;

  let studioSource,studioPass;
  try {
    studioSource=await loadStudioSource();
    studioPass=createStudioPass(gl,studioSource);
  } catch(error) {
    renderMetrics.error=error.message;console.warn('Glass renderer:',error);return;
  }

  let quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);

  const cards = [...document.querySelectorAll('.card')];
  const dprCap = 2;
  let lost = false;
  let ticking = false;
  let holdUntil = 0;
  let scrolling = false;
  let scrollEnd = 0;

  const usesGpu = () => !lost && glassAllowed();

  const markGpu = () => {
    let any = false;
    cards.forEach((card) => {
      const on = usesGpu(card);
      card.classList.toggle('glass-gpu', on);
      any = any || on;
    });
    layer.hidden = !any;
  };

  const restoreCss = () => {
    lost = true;
    markGpu();
  };

  layer.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    restoreCss();
  });
  layer.addEventListener('webglcontextrestored', () => {
    // Rebuild resources in place; retain listeners, scroll and the reader's inputs.
    try {
      studioPass=createStudioPass(gl,studioSource);
      quad = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, quad);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
      lost = false;
      dirty(300);
    } catch {
      restoreCss();
    }
  });

  const resizeLayer = () => {
    const dpr = Math.min(devicePixelRatio || 1, dprCap);
    const w = Math.max(1, Math.round(innerWidth * dpr));
    const h = Math.max(1, Math.round(innerHeight * dpr));
    if (layer.width !== w || layer.height !== h) {
      layer.width = w;
      layer.height = h;
    }
  };

  const draw = () => {
    if (lost || !glassAllowed()) {
      markGpu();
      return;
    }
    resizeLayer();
    const viewW = innerWidth;
    const viewH = innerHeight;
    const box = backdrop.getBoundingClientRect();
    const drawRect = coverDrawRect(box, img.naturalWidth, img.naturalHeight);
    const dpr=Math.min(devicePixelRatio||1,dprCap);
    studioPass.prepare(img,drawRect,viewW,viewH,dpr,config.material.blurRadius);
    renderMetrics.studioTextureBuilds=studioPass.getTextureBuilds();
    gl.viewport(0, 0, layer.width, layer.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

    let drew = false;
    for (const card of cards) {
      const {rect:r,radius} = readCardGeometry(card,config.geometry);
      if (r.bottom < -48 || r.top > viewH + 48 || r.right < -48 || r.left > viewW + 48) continue;
      if (r.width < 8 || r.height < 8) continue;
      studioPass.draw(r,radius,config.material,viewW,viewH,dpr,GLASS_DEBUG);
      drew = true;
    }

    markGpu();
    renderMetrics.frames++;
    return drew;
  };

  const loop = (now) => {
    try {draw();}catch(error){renderMetrics.error=error.message;restoreCss();}
    if (!lost && (now < holdUntil || scrolling)) requestAnimationFrame(loop);
    else ticking = false;
  };

  const dirty = (holdMs = 0) => {
    holdUntil = Math.max(holdUntil, performance.now() + holdMs);
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(loop);
    }
  };

  addEventListener('scroll', () => {
    scrolling = true;
    window.clearTimeout(scrollEnd);
    scrollEnd = window.setTimeout(() => {
      scrolling = false;
      dirty(80);
    }, 120);
    dirty();
  }, { passive: true });
  addEventListener('resize', () => dirty(200));
  if (window.visualViewport) {
    visualViewport.addEventListener('resize', () => dirty(200));
    visualViewport.addEventListener('scroll', () => dirty());
  }
  REDUCED_TRANSPARENCY.addEventListener('change', () => dirty());
  MORE_CONTRAST.addEventListener('change', () => dirty());
  addEventListener('glass-config-change', () => dirty());

  const resizeObserver = new ResizeObserver(() => dirty(1000));
  cards.forEach(card => resizeObserver.observe(card));
  new MutationObserver(records => {
    if (records.some(r => r.attributeName === 'open' ||
      (r.oldValue || '').split(/\s+/).includes('in') !== r.target.classList.contains('in'))) dirty(1100);
  }).observe(document.querySelector('main'), {
    subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ['open', 'class'],
  });
  markGpu();
  dirty(300);
}

initNavigation(() => config.nav, {
  glassAllowed,reducedMotion:REDUCED_MOTION,reducedTransparency:REDUCED_TRANSPARENCY,
  moreContrast:MORE_CONTRAST,debug:GLASS_DEBUG,
});
initCardGlass();
