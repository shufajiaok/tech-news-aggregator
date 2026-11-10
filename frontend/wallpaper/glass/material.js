// User-approved Studio material. Page geometry and navigation do not belong here.
export const MATERIAL_DEFAULTS = {
  "refThickness": 10.79,
  "refFactor": 1.63,
  "refDispersion": 21.04,
  "refFresnelRange": 9.17,
  "refFresnelHardness": 0,
  "refFresnelFactor": 21.27,
  "glareRange": 20,
  "glareHardness": 20.89,
  "glareFactor": 64.93,
  "glareConvergence": 31.55,
  "glareOppositeFactor": 52.5,
  "glareAngle": -41.1,
  "blurRadius": 1,
  "blurEdge": true,
  "tint": "#000000",
  "tintAlpha": 0.13
};

// [minimum, maximum, slider step]; JS validation and the tuner share these bounds.
export const MATERIAL_LIMITS = {
  refThickness:[1,80,.01], refFactor:[1,4,.01], refDispersion:[0,50,.01],
  refFresnelRange:[.1,100,.01], refFresnelHardness:[0,100,.01], refFresnelFactor:[0,100,.01],
  glareRange:[.1,100,.01], glareHardness:[0,100,.01], glareFactor:[0,120,.01],
  glareConvergence:[0,100,.01], glareOppositeFactor:[0,100,.01], glareAngle:[-180,180,.1],
  blurRadius:[0,12,.1], tintAlpha:[0,1,.01],
};
