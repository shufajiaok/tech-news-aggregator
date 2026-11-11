// CSS and the optical silhouette use the same radius; layout owns actual width/height.
// roundness = superellipse 指数 n：2 是普通圆弧，4 起是 iOS / GlassKit 的
// G2 连续曲率。CSS 的 corner-shape: superellipse(n) 与 shader 共用这个值。
export const GEOMETRY_DEFAULTS = {
  card:{radius:39,roundness:4}, dense:{radius:39,roundness:4}, showcase:{radius:39,roundness:4},
};
export const GEOMETRY_LIMITS = {radius:[4,80,.5], roundness:[2,8,.1]};

// kind 只由卡片内部结构决定，算一次就缓存：以前每帧对每张卡片做两次 querySelector，
// 20+ 卡片 × 60fps 是纯浪费，也是滚动时掉帧的来源之一。
const KIND_CACHE = new WeakMap();
export function cardKind(card) {
  let kind = KIND_CACHE.get(card);
  if (kind === undefined) {
    kind = card.querySelector('table, .matrix') ? 'dense'
      : card.querySelector('canvas, .knowledge-map, .bar-chart') ? 'showcase'
        : 'card';
    KIND_CACHE.set(card, kind);
  }
  return kind;
}

export function readCardGeometry(card, geometry) {
  const rect = card.getBoundingClientRect();
  const kind = cardKind(card);
  return {rect, kind,
    radius:Math.min(geometry[kind].radius,rect.width/2,rect.height/2),
    roundness:geometry[kind].roundness};
}

// Matches background-size: cover; background-position: center top.
export function coverDrawRect(box, imgW, imgH) {
  const scale = Math.max(box.width/imgW,box.height/imgH);
  const w = imgW*scale, h = imgH*scale;
  return {x:box.left+(box.width-w)/2,y:box.top,w,h};
}
