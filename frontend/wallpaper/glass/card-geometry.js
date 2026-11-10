// CSS and the optical silhouette use the same radius; layout owns actual width/height.
export const GEOMETRY_DEFAULTS = {
  card:{radius:39}, dense:{radius:39}, showcase:{radius:39},
};
export const GEOMETRY_LIMITS = {radius:[4,80,.5]};

export function cardKind(card) {
  if (card.querySelector('table, .matrix')) return 'dense';
  if (card.querySelector('canvas, .knowledge-map, .bar-chart')) return 'showcase';
  return 'card';
}

export function readCardGeometry(card, geometry) {
  const rect = card.getBoundingClientRect();
  const kind = cardKind(card);
  return {rect, kind, radius:Math.min(geometry[kind].radius,rect.width/2,rect.height/2)};
}

// Matches background-size: cover; background-position: center top.
export function coverDrawRect(box, imgW, imgH) {
  const scale = Math.max(box.width/imgW,box.height/imgH);
  const w = imgW*scale, h = imgH*scale;
  return {x:box.left+(box.width-w)/2,y:box.top,w,h};
}
