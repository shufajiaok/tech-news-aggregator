// Navigation samples the live browser backdrop, independently of the card texture.
export const NAV_DEFAULTS = {
  "height": 64,
  "navEdge": 14,
  "navRefraction": 3.5,
  "lensWidth": 13,
  "lensStrength": 0.45,
  "navBlur": 0,
  "navFill": 0.24,
  "navSaturation": 1.08
};
export const NAV_LIMITS = {
  height:[48,96,1], navEdge:[1,24,.5], navRefraction:[0,6,.1],
  lensWidth:[1,24,.5], lensStrength:[0,.45,.01], navBlur:[0,6,.1],
  navFill:[0,.7,.01], navSaturation:[0,2,.01],
};

export function initNavigation(getConfig, {glassAllowed, reducedMotion, reducedTransparency, moreContrast, debug}) {
  /** Native backdrop displacement: the browser supplies the live composited page.
   * Enable only on Chromium, where this combination is visually tested. Safari /
   * Firefox retain the CSS material; CSS.supports alone cannot prove pixel support.
   */
  function initNavRefraction(nav, lens) {
    if (!/Chrome\//.test(navigator.userAgent) || /EdgiOS|CriOS/.test(navigator.userAgent)
        || !CSS.supports('backdrop-filter', 'url("#nav-refraction")')) return () => {};
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('aria-hidden', 'true');
    svg.style.cssText = 'position:fixed;width:0;height:0;pointer-events:none';
    svg.innerHTML = `<defs><filter id="nav-refraction" filterUnits="userSpaceOnUse"
      primitiveUnits="userSpaceOnUse" color-interpolation-filters="sRGB">
      <feImage result="map" preserveAspectRatio="none"/>
      <feDisplacementMap in="SourceGraphic" in2="map" xChannelSelector="R" yChannelSelector="G"/>
    </filter></defs>`;
    document.body.append(svg);
    const filter = svg.querySelector('filter');
    const map = svg.querySelector('feImage');
    const displacement = svg.querySelector('feDisplacementMap');
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    let signature = '';
    let frame = 0;
    let until = 0;

    function shape(x, y, r, requestedWidth, requestedAmplitude = Infinity) {
      const hx = r.width / 2, hy = r.height / 2, radius = Math.min(hx, hy);
      const px = x - r.x - hx, py = y - r.y - hy;
      const qx = Math.abs(px) - hx + radius, qy = Math.abs(py) - hy + radius;
      const ax = Math.max(qx, 0), ay = Math.max(qy, 0), length = Math.hypot(ax, ay);
      const d = length + Math.min(Math.max(qx, qy), 0) - radius;
      const width = Math.min(requestedWidth, .8 * radius, .45 * Math.min(hx, hy));
      const t = Math.min(1, Math.max(0, -d / width));
      const profile = 16 * t * t * (1 - t) * (1 - t);
      const nx = length ? Math.sign(px) * ax / length : (qx > qy ? Math.sign(px) : 0);
      const ny = length ? Math.sign(py) * ay / length : (qy >= qx ? Math.sign(py) : 0);
      return { x: nx * Math.min(requestedAmplitude,width*.25) * profile, y: ny * Math.min(requestedAmplitude,width*.25) * profile, profile, width };
    }

    function draw(now) {
      frame = 0;
      const allowed = glassAllowed();
      nav.classList.toggle('nav--refracting', allowed);
      if (!allowed) return;
      const r = nav.getBoundingClientRect(), l = lens.getBoundingClientRect();
      const local = { x: l.left - r.left, y: l.top - r.top, width: l.width, height: l.height };
      const key = [r.width, r.height, ...Object.values(local), devicePixelRatio].map(v => v.toFixed(2)).join() + JSON.stringify(getConfig());
      if (key !== signature) {
        signature = key;
        const scale = Math.min(devicePixelRatio || 1, 2);
        canvas.width = Math.ceil(r.width * scale);
        canvas.height = Math.ceil(r.height * scale);
        const data = ctx.createImageData(canvas.width, canvas.height);
        const shell = { x: 0, y: 0, width: r.width, height: r.height };
        const navWidth = Math.min(getConfig().navEdge, .45*r.height/2);
        const maxOffset = Math.max(.01, Math.min(getConfig().navRefraction,navWidth*.25) + getConfig().lensWidth*.25*getConfig().lensStrength);
        for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
          const px = (x + .5) / scale, py = (y + .5) / scale;
          const base = shape(px, py, shell, getConfig().navEdge, getConfig().navRefraction);
          const active = shape(px, py, local, getConfig().lensWidth);
          // Add the active bulge only where the outer bevel is quiet: one sampling pass.
          const weight = getConfig().lensStrength * (1 - base.profile);
          const i = (y * canvas.width + x) * 4;
          data.data[i] = Math.round(255 * (.5 + (base.x + active.x * weight) / (2 * maxOffset)));
          data.data[i + 1] = Math.round(255 * (.5 + (base.y + active.y * weight) / (2 * maxOffset)));
          data.data[i + 2] = 128;
          data.data[i + 3] = 255;
        }
        ctx.putImageData(data, 0, 0);
        filter.setAttribute('x', '0'); filter.setAttribute('y', '0');
        filter.setAttribute('width', r.width); filter.setAttribute('height', r.height);
        map.setAttribute('x', '0'); map.setAttribute('y', '0');
        map.setAttribute('width', r.width); map.setAttribute('height', r.height);
        map.setAttribute('href', canvas.toDataURL());
        displacement.setAttribute('scale', debug === 1 ? 0 : 2 * maxOffset);
        nav.style.setProperty('--nav-map', `url("${canvas.toDataURL()}")`);
        nav.dataset.edgeWidth = navWidth.toFixed(2);
        nav.dataset.amplitude = Math.min(getConfig().navRefraction,navWidth*.25).toFixed(2);
      }
      if (now < until) frame = requestAnimationFrame(draw);
    }
    const update = (duration = 0) => {
      until = Math.max(until, performance.now() + duration);
      if (!frame) frame = requestAnimationFrame(draw);
    };
    new ResizeObserver(() => update(350)).observe(nav);
    reducedTransparency.addEventListener('change', () => update());
    moreContrast.addEventListener('change', () => update());
    update();
    return update;
  }

  let navSwitchTimer;
  function placeNavLens(instant) {
    const nav = document.querySelector('.nav');
    const lens = document.querySelector('.nav__lens');
    if (!nav || !lens) return;
    const active = nav.querySelector('a.active') || nav.querySelector('a');
    if (!active) return;
    const navR = nav.getBoundingClientRect();
    const r = active.getBoundingClientRect();
    const x = r.left - navR.left;
    const y = r.top - navR.top;
    clearTimeout(navSwitchTimer);
    if (instant || reducedMotion) {
      nav.classList.remove('nav--switching');
      nav.classList.add('nav--snap');
      lens.style.left = `${x}px`;
      lens.style.top = `${y}px`;
      lens.style.width = `${r.width}px`;
      lens.style.height = `${r.height}px`;
      lens.getBoundingClientRect();
      nav.classList.remove('nav--snap');
      return;
    }
    nav.classList.add('nav--switching');
    lens.style.left = `${x}px`;
    lens.style.top = `${y}px`;
    lens.style.width = `${r.width}px`;
    lens.style.height = `${r.height}px`;
    navSwitchTimer = window.setTimeout(() => nav.classList.remove('nav--switching'), 320);
  }

  function initNavLens() {
    const nav = document.querySelector('.nav');
    const lens = document.querySelector('.nav__lens');
    if (!nav || !lens) return;
    const updateRefraction = initNavRefraction(nav, lens);
    const sync = (instant = false) => {
      placeNavLens(instant);
      updateRefraction(instant || reducedMotion ? 0 : 600);
    };
    sync(true);
    // Observe only link selection; observing our own transition classes loops forever.
    const observer = new MutationObserver(() => sync());
    nav.querySelectorAll('a').forEach(a => observer.observe(a, { attributes: true, attributeFilter: ['class'] }));
    nav.querySelector('.glass__content').addEventListener('scroll', () => sync(true), { passive: true });
    addEventListener('resize', () => sync(true));
    document.fonts.ready.then(() => sync(true));
    addEventListener('glass-config-change', () => sync(true));
  }

  initNavLens();
}
