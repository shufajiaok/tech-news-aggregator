const VERT = `#version 300 es
layout(location=0) in vec2 aPos;
uniform vec4 uQuad;
uniform vec2 uView;
out vec2 v_uv;
void main() {
  vec2 px = uQuad.xy + aPos * uQuad.zw;
  v_uv = vec2(px.x/uView.x,1.0-px.y/uView.y);
  gl_Position = vec4(px.x / uView.x * 2.0 - 1.0, 1.0 - px.y / uView.y * 2.0, 0.0, 1.0);
}
`;

// Liquid Glass Studio, MIT / Charles Yin. Original GLSL and license live in vendor/studio/.
// This adapter supplies viewport textures and makes the single-shape output compositable.
export async function loadStudioSource() {
  const paths=['fragment-main.glsl','lib/sdf.glsl','lib/math.glsl','lib/color.glsl'];
  const data=await Promise.all(paths.map(async path=>{
    const response=await fetch(new URL('./vendor/studio/'+path,import.meta.url));
    if(!response.ok)throw Error(`Studio shader ${path}: ${response.status}`);
    return response.text();
  }));
  const source=data[0].replace("#include './lib/sdf.glsl'",data[1])
    .replace("#include './lib/math.glsl'",data[2]).replace("#include './lib/color.glsl'",data[3]);
  // Upstream paints the whole background. Clip only its output, leaving its optics intact.
  return source.replace('void main() {','void studioMain() {')+`
    void main() {
      vec2 p1 = -u_resolution.xy * 0.5 / u_resolution.y;
      vec2 p2 = -u_mouseSpring / u_resolution.y;
      float distance = mainSDF(p1, p2, gl_FragCoord.xy);
      float coverage = 1.0 - smoothstep(-1.0/u_resolution.y, 1.0/u_resolution.y, distance);
      if (coverage <= 0.0) discard;
      studioMain();
      fragColor = vec4(fragColor.rgb * coverage, coverage);
    }
  `;
}

export function createStudioPass(gl,fragment) {
  const program=gl.createProgram();
  for(const [type,source]of [[gl.VERTEX_SHADER,VERT],[gl.FRAGMENT_SHADER,fragment]]) {
    const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);
    if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)) {
      const error=gl.getShaderInfoLog(shader);gl.deleteShader(shader);gl.deleteProgram(program);throw Error(error);
    }
    gl.attachShader(program,shader);gl.deleteShader(shader);
  }
  gl.bindAttribLocation(program,0,'aPos');gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS)) {
    const error=gl.getProgramInfoLog(program);gl.deleteProgram(program);throw Error(error);
  }
  const locations=new Map();let textures=[],signature='',textureBuilds=0;
  function uniform(key,value,int=false) {
    if(!locations.has(key))locations.set(key,gl.getUniformLocation(program,key));
    const location=locations.get(key);if(location===null)return;
    if(Array.isArray(value))gl[`uniform${value.length}fv`](location,value);
    else if(int)gl.uniform1i(location,value);else gl.uniform1f(location,value);
  }
  function upload(source) {
    const tex=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,tex);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,source);return tex;
  }
  // 纹理重建分两种：几何变了（换背景/视口/滚动）立即重建；只有模糊值变了就防抖。
  // 之前把 blur 塞进 signature 里逐帧比较，拖动模糊滑块 = 每帧一次全屏 CPU 高斯，必卡。
  let geomSig='',blurSig='',blurTimer=0,pendingBlur=null;

  function build(img,rect,w,h,dpr,blur) {
    // Keep offscreen wallpaper pixels available to the blur kernel before cropping.
    const pad=Math.ceil(blur*dpr*3)+2,source=document.createElement('canvas');
    source.width=Math.round(w*dpr)+pad*2;source.height=Math.round(h*dpr)+pad*2;
    const ctx=source.getContext('2d');ctx.fillStyle='#071a36';ctx.fillRect(0,0,source.width,source.height);
    ctx.drawImage(img,rect.x*dpr+pad,rect.y*dpr+pad,rect.w*dpr,rect.h*dpr);
    // Repeat boundary pixels when the wallpaper ends exactly at the viewport edge.
    if(rect.x>=0)ctx.drawImage(source,pad,pad,1,source.height-pad*2,0,pad,pad,source.height-pad*2);
    if(rect.x+rect.w<=w)ctx.drawImage(source,source.width-pad-1,pad,1,source.height-pad*2,source.width-pad,pad,pad,source.height-pad*2);
    if(rect.y>=0)ctx.drawImage(source,0,pad,source.width,1,0,0,source.width,pad);
    if(rect.y+rect.h<=h)ctx.drawImage(source,0,source.height-pad-1,source.width,1,0,source.height-pad,source.width,pad);
    const sharp=document.createElement('canvas');sharp.width=Math.round(w*dpr);sharp.height=Math.round(h*dpr);
    sharp.getContext('2d').drawImage(source,-pad,-pad);
    // 模糊层用半分辨率：玻璃本来就是糊的，肉眼分不出，但像素量只剩 1/4，CPU 高斯快约 4 倍。
    const soft=document.createElement('canvas');
    soft.width=Math.max(1,Math.round(sharp.width/2));soft.height=Math.max(1,Math.round(sharp.height/2));
    const b=soft.getContext('2d');
    b.filter=`blur(${Math.max(.5,blur*dpr/2)}px)`;
    b.drawImage(source,-pad,-pad,w*dpr,h*dpr,0,0,soft.width,soft.height);
    for(const tex of textures)gl.deleteTexture(tex);
    textures=[upload(sharp),upload(soft)];signature=geomSig+'|'+blurSig;textureBuilds++;
  }

  function prepare(img,rect,w,h,dpr,blur) {
    const geom=[img.src,rect.x,rect.y,rect.w,rect.h,w,h,dpr].join('|');
    if(geom!==geomSig){
      geomSig=geom;blurSig=String(blur);
      build(img,rect,w,h,dpr,blur);
      return;
    }
    if(String(blur)===blurSig)return;
    blurSig=String(blur);
    // 拖到停下来 140ms 才按最终值重建，拖动过程中继续用上一张纹理，画面不跳。
    pendingBlur={img,rect,w,h,dpr,blur};
    clearTimeout(blurTimer);
    blurTimer=setTimeout(()=>{
      const p=pendingBlur;pendingBlur=null;
      if(p)build(p.img,p.rect,p.w,p.h,p.dpr,p.blur);
    },140);
  }
  function draw(r,radius,roundness,c,w,h,dpr,debug=0) {
    gl.useProgram(program);
    textures.forEach((t,i)=>{gl.activeTexture(gl.TEXTURE0+i);gl.bindTexture(gl.TEXTURE_2D,t);});
    uniform('uQuad',[r.left-2,r.top-2,r.width+4,r.height+4]);uniform('uView',[w,h]);
    uniform('u_bg',0,true);uniform('u_blurredBg',1,true);
    uniform('u_resolution',[Math.round(w*dpr),Math.round(h*dpr)]);uniform('u_dpr',dpr);
    uniform('u_mouseSpring',[(r.left+r.width/2)*dpr,(h-r.top-r.height/2)*dpr]);
    uniform('u_shapeWidth',r.width);uniform('u_shapeHeight',r.height);uniform('u_shapeRadius',radius);
    // 与 CSS corner-shape: superellipse(n) 共用同一个 n，折射轮廓和卡片形状严丝合缝；
    // n=2 圆弧，n=4 起是 GlassKit(com.kyant.shapes) 的 G2 连续曲率。
    uniform('u_shapeRoundness',roundness);uniform('u_mergeRate',.05);uniform('u_showShape1',0,true);
    uniform('STEP',debug===1?0:debug===2?4:debug===3?6:9,true);
    for(const key of ['refThickness','refFactor','refDispersion','refFresnelRange','glareRange'])uniform('u_'+key,c[key]);
    for(const key of ['refFresnelHardness','refFresnelFactor','glareHardness','glareFactor','glareConvergence','glareOppositeFactor'])uniform('u_'+key,c[key]/100);
    uniform('u_glareAngle',c.glareAngle*Math.PI/180);uniform('u_blurEdge',c.blurEdge?1:0,true);
    uniform('u_tint',[...c.tint.match(/[a-f\d]{2}/gi).map(v=>parseInt(v,16)/255),c.tintAlpha]);
    if(debug===1) {
      // STEP 9 with zero material effects is the undistorted source diagnostic.
      uniform('STEP',9,true);uniform('u_refFactor',1);uniform('u_refDispersion',0);
      uniform('u_refFresnelFactor',0);uniform('u_glareFactor',0);uniform('u_tint',[0,0,0,0]);
      gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,textures[0]);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
  }
  return {prepare,draw,getTextureBuilds:()=>textureBuilds};
}
