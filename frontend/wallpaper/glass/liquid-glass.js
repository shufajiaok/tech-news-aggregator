import {MATERIAL_DEFAULTS,MATERIAL_LIMITS} from './material.js';
import {GEOMETRY_DEFAULTS,GEOMETRY_LIMITS} from './card-geometry.js';
import {NAV_DEFAULTS,NAV_LIMITS,initNavigation} from './navigation.js';

// Entry point for the only remaining glass surface: the top navigation.
// 卡片（控制台等）已经全部改为不透明内容层，canvas 折射通道整条移除。
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
    } else if (typeof target[key] === 'number' && Number.isFinite(value)) {
      const [min,max] = GLASS_LIMITS[key];
      target[key] = Math.min(max,Math.max(min,value));
    }
  }
}

let styleRaf = 0;

export function setGlassConfig(patch) {
  if (!patch || typeof patch !== 'object') return getGlassConfig();
  updateGroup(config.material,patch.material);
  for (const name of Object.keys(config.geometry)) updateGroup(config.geometry[name],patch.geometry?.[name]);
  updateGroup(config.nav,patch.nav);
  // 同一帧内的多次调用合并成一次样式写入 + 一次重绘
  if (!styleRaf) styleRaf = requestAnimationFrame(() => {
    styleRaf = 0;
    applyConfigStyles();
    dispatchEvent(new Event('glass-config-change'));
  });
  return getGlassConfig();
}

function applyConfigStyles() {
  const root = document.documentElement.style;
  // 圆角是形状而不是玻璃：卡片仍吃 superellipse，靠这两个变量下发。
  root.setProperty('--corner-radius', `${config.geometry.card.radius}px`);
  root.setProperty('--corner-n', config.geometry.card.roundness);
  const nav = document.querySelector('.nav');
  if (nav) for (const [key,value] of Object.entries(config.nav)) nav.style.setProperty(`--${key}`,value);
}

export function getGlassMetrics() {
  return {
    engine:'navigation-only',
    nav:document.querySelector('.nav')?.classList.contains('nav--refracting') || false,
    config:structuredClone(config),
  };
}

try { setGlassConfig(JSON.parse(document.querySelector('#glass-config')?.textContent || '{}')); applyConfigStyles(); }
catch { console.warn('Invalid glass-config JSON; using the saved material defaults.'); }

// Diagnostics use the actual production texture or native backdrop.
const DEBUG_VIEW = new URLSearchParams(location.search).get('glassDebug');
const GLASS_DEBUG = ({source:1,coordinates:1,displacement:2,refractionOnly:3})[DEBUG_VIEW] || 0;
if (GLASS_DEBUG) document.documentElement.dataset.glassDebug = DEBUG_VIEW;

initNavigation(() => ({...config.nav}), {
  glassAllowed,reducedMotion:REDUCED_MOTION,reducedTransparency:REDUCED_TRANSPARENCY,
  moreContrast:MORE_CONTRAST,debug:GLASS_DEBUG,
});
